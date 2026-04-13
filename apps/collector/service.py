from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from apps.collector.publisher import CollectorPublisher
from apps.collector.runtime import CollectorRuntime, SUPPORTED_LIVE_MARKETS
from packages.contracts.topics import DASHBOARD_CONTROL_TOPIC
from packages.infrastructure.kafka import AsyncKafkaJsonBroker
from packages.shared.config import load_service_settings
from src.config import settings as kis_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DashboardSubscription:
    symbol: str
    market: str

    @property
    def subscription_key(self) -> str:
        return f"dashboard:{self.market.lower()}:{self.symbol}"


@dataclass(slots=True)
class ActiveDashboardPublication:
    task: asyncio.Task[None]
    owners: set[str]


class DashboardSubscriptionRequest(BaseModel):
    symbol: str
    market: str


class CollectorDashboardService:
    def __init__(self, settings: Any):
        self._settings = settings
        self._collector_runtime = CollectorRuntime(kis_settings)
        self._broker = AsyncKafkaJsonBroker(settings.bootstrap_servers)
        self._publisher = CollectorPublisher(self._broker)
        self._publications: dict[str, ActiveDashboardPublication] = {}
        self._owner_index: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._control_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._control_task is None or self._control_task.done():
            self._control_task = asyncio.create_task(self._consume_dashboard_control())

    async def aclose(self) -> None:
        control_task = self._control_task
        self._control_task = None
        if control_task is not None:
            control_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await control_task

        async with self._lock:
            publications = list(self._publications.values())
            self._publications.clear()
            self._owner_index.clear()

        for publication in publications:
            publication.task.cancel()
        for publication in publications:
            with contextlib.suppress(asyncio.CancelledError):
                await publication.task

        await self._broker.aclose()
        await self._collector_runtime.aclose()

    async def start_dashboard_publication(self, *, symbol: str, market: str, owner_id: str | None = None) -> dict[str, Any]:
        subscription = self._build_subscription(symbol=symbol, market=market)
        resolved_owner_id = owner_id or uuid.uuid4().hex

        async with self._lock:
            publication = self._publications.get(subscription.subscription_key)
            if publication is None or publication.task.done():
                publication = ActiveDashboardPublication(
                    task=asyncio.create_task(self._publish_dashboard_events(subscription)),
                    owners=set(),
                )
                self._publications[subscription.subscription_key] = publication
            publication.owners.add(resolved_owner_id)
            self._owner_index[resolved_owner_id] = subscription.subscription_key

        return {
            "subscription_id": resolved_owner_id,
            "symbol": subscription.symbol,
            "market": subscription.market,
            "status": "started",
        }

    async def stop_dashboard_publication(self, *, subscription_id: str) -> dict[str, Any]:
        task: asyncio.Task[None] | None = None
        subscription_key: str | None = None

        async with self._lock:
            subscription_key = self._owner_index.pop(subscription_id, None)
            if subscription_key is None:
                return {"subscription_id": subscription_id, "status": "not_found"}

            publication = self._publications.get(subscription_key)
            if publication is not None:
                publication.owners.discard(subscription_id)
                if not publication.owners:
                    task = publication.task
                    self._publications.pop(subscription_key, None)

        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        return {
            "subscription_id": subscription_id,
            "subscription_key": subscription_key,
            "status": "stopped",
        }

    async def fetch_price_chart(self, *, symbol: str, market: str, interval: int) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._collector_runtime.fetch_price_chart,
            symbol=symbol,
            market=market,
            interval=interval,
        )

    async def _consume_dashboard_control(self) -> None:
        group_id = f"{self._settings.service_name}-dashboard-control"
        while True:
            try:
                async for payload in self._broker.subscribe(topic=DASHBOARD_CONTROL_TOPIC, group_id=group_id):
                    await self._handle_dashboard_control(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("collector dashboard control consumer failed")
                await asyncio.sleep(1)

    async def _handle_dashboard_control(self, payload: dict[str, Any]) -> None:
        action = str(payload.get("action") or "").strip().lower()
        owner_id = str(payload.get("owner_id") or "").strip()
        symbol = str(payload.get("symbol") or "").strip()
        market = str(payload.get("market") or "").strip()

        if action not in {"start", "stop"} or not owner_id:
            return

        if action == "start":
            try:
                await self.start_dashboard_publication(symbol=symbol, market=market, owner_id=owner_id)
            except Exception:
                logger.exception("collector failed to start dashboard publication", extra={"symbol": symbol, "market": market})
            return

        await self.stop_dashboard_publication(subscription_id=owner_id)

    async def _publish_dashboard_events(self, subscription: DashboardSubscription) -> None:
        try:
            async for event_name, payload in self._collector_runtime.stream_dashboard(
                symbol=subscription.symbol,
                market=subscription.market,
            ):
                await self._publisher.publish_dashboard_event(
                    symbol=subscription.symbol,
                    market=subscription.market,
                    event_name=event_name,
                    payload=payload,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("collector dashboard publication failed", extra={"symbol": subscription.symbol, "market": subscription.market})

    def _build_subscription(self, *, symbol: str, market: str) -> DashboardSubscription:
        normalized_symbol = symbol.strip()
        if not normalized_symbol:
            raise ValueError("symbol is required")
        normalized_market = market.strip().lower()
        if normalized_market not in SUPPORTED_LIVE_MARKETS:
            raise ValueError(f"unsupported live market: {market}")
        return DashboardSubscription(symbol=normalized_symbol, market=normalized_market)


app = FastAPI(title="Collector Service")
service_settings = load_service_settings("collector")
dashboard_service = CollectorDashboardService(service_settings)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "service": service_settings.service_name,
            "symbol": service_settings.symbol,
            "market": service_settings.market,
        }
    )


@app.on_event("shutdown")
async def shutdown_runtime() -> None:
    await dashboard_service.aclose()


@app.on_event("startup")
async def startup_runtime() -> None:
    await dashboard_service.start()


@app.post("/dashboard/subscriptions")
async def start_dashboard_subscription(payload: DashboardSubscriptionRequest) -> JSONResponse:
    try:
        response = await dashboard_service.start_dashboard_publication(
            symbol=payload.symbol.strip(),
            market=payload.market.strip(),
        )
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JSONResponse(response)


@app.delete("/dashboard/subscriptions/{subscription_id}")
async def stop_dashboard_subscription(subscription_id: str) -> JSONResponse:
    payload = await dashboard_service.stop_dashboard_publication(subscription_id=subscription_id)
    status_code = 404 if payload["status"] == "not_found" else 200
    return JSONResponse(payload, status_code=status_code)


@app.get("/api/price-chart")
async def price_chart(
    symbol: str = Query(..., min_length=1),
    market: str = Query("krx", pattern="^(krx|nxt|total)$"),
    interval: int = Query(..., ge=1, le=60),
) -> JSONResponse:
    if interval not in {1, 5, 10, 30, 60}:
        return JSONResponse({"error": "unsupported interval"}, status_code=400)

    try:
        payload = await dashboard_service.fetch_price_chart(
            symbol=symbol,
            market=market,
            interval=interval,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except NotImplementedError as exc:
        return JSONResponse({"error": str(exc)}, status_code=501)
    except RuntimeError as exc:
        logger.warning(
            "collector price chart upstream failed",
            extra={"symbol": symbol, "market": market, "interval": interval},
        )
        return JSONResponse({"error": str(exc)}, status_code=502)
    except Exception:
        logger.exception(
            "collector price chart unexpected failure",
            extra={"symbol": symbol, "market": market, "interval": interval},
        )
        return JSONResponse({"error": "collector price-chart request failed unexpectedly"}, status_code=500)

    return JSONResponse(payload)


def main() -> None:
    uvicorn.run(
        "apps.collector.service:app",
        host=service_settings.host,
        port=service_settings.port,
        log_level=service_settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
