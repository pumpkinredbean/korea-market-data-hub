from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from apps.collector.publisher import CollectorPublisher
from apps.collector.runtime import CollectorRuntime, SUPPORTED_MARKET_SCOPES
from packages.contracts.events import EventType
from packages.contracts.topics import DASHBOARD_CONTROL_TOPIC
from packages.infrastructure.kafka import AsyncKafkaJsonBroker
from packages.shared.config import load_service_settings
from src.collector_control_plane import CollectorControlPlaneService
from src.config import settings as kis_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DashboardSubscription:
    symbol: str
    market_scope: str

    @property
    def subscription_key(self) -> str:
        return f"dashboard:{self.market_scope.lower()}:{self.symbol}"


class DashboardSubscriptionRequest(BaseModel):
    symbol: str

    market_scope: str | None = None
    market: str | None = None

    def resolved_market_scope(self) -> str:
        return _resolve_market_scope(scope=self.market_scope, market=self.market)


class AdminTargetUpsertRequest(BaseModel):
    symbol: str
    target_id: str | None = None
    event_types: list[str]
    enabled: bool = True
    market_scope: str | None = None
    market: str | None = None

    def resolved_market_scope(self) -> str:
        return _resolve_market_scope(scope=self.market_scope, market=self.market)


def _resolve_market_scope(*, scope: str | None = None, market: str | None = None) -> str:
    resolved = (scope or market or "").strip().lower()
    if resolved not in SUPPORTED_MARKET_SCOPES:
        raise ValueError(f"unsupported market scope: {scope or market}")
    return resolved


class CollectorDashboardService:
    def __init__(self, settings: Any):
        self._settings = settings
        self._collector_runtime = CollectorRuntime(
            kis_settings,
            on_event=self._handle_runtime_event,
            on_failure=self._handle_runtime_failure,
            on_recovery=self._handle_runtime_recovery,
        )
        self._broker = AsyncKafkaJsonBroker(settings.bootstrap_servers)
        self._publisher = CollectorPublisher(self._broker)
        self._owner_index: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._control_task: asyncio.Task[None] | None = None
        self._control_plane = CollectorControlPlaneService(
            service_name=settings.service_name,
            default_symbol=settings.symbol,
            default_market_scope=settings.market,
            start_publication=self.start_dashboard_publication,
            stop_publication=self.stop_dashboard_publication,
            is_publication_active=self.is_publication_active,
        )

    async def start(self) -> None:
        if self._control_task is None or self._control_task.done():
            self._control_task = asyncio.create_task(self._consume_dashboard_control())
        await self._control_plane.mark_running()

    async def aclose(self) -> None:
        await self._control_plane.mark_stopping()
        control_task = self._control_task
        self._control_task = None
        if control_task is not None:
            control_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await control_task

        async with self._lock:
            self._owner_index.clear()

        await self._broker.aclose()
        await self._collector_runtime.aclose()
        await self._control_plane.mark_stopped()

    async def start_dashboard_publication(
        self,
        *,
        symbol: str,
        market_scope: str,
        owner_id: str | None = None,
        event_types: tuple[str, ...] | list[str] | None = None,
    ) -> dict[str, Any]:
        subscription = self._build_subscription(symbol=symbol, market_scope=market_scope)
        resolved_owner_id = owner_id or uuid.uuid4().hex

        await self._collector_runtime.register_target(
            owner_id=resolved_owner_id,
            symbol=subscription.symbol,
            market_scope=subscription.market_scope,
            event_types=event_types or _all_dashboard_event_types(),
        )

        async with self._lock:
            self._owner_index[resolved_owner_id] = subscription.subscription_key

        return {
            "subscription_id": resolved_owner_id,
            "symbol": subscription.symbol,
            "market_scope": subscription.market_scope,
            "market": subscription.market_scope,
            "status": "started",
        }

    async def stop_dashboard_publication(self, *, subscription_id: str) -> dict[str, Any]:
        subscription_key: str | None = None

        async with self._lock:
            subscription_key = self._owner_index.pop(subscription_id, None)
            if subscription_key is None:
                return {"subscription_id": subscription_id, "status": "not_found"}

        await self._collector_runtime.unregister_target(owner_id=subscription_id)

        return {
            "subscription_id": subscription_id,
            "subscription_key": subscription_key,
            "status": "stopped",
        }

    async def fetch_price_chart(self, *, symbol: str, market_scope: str, interval: int) -> dict[str, Any]:
        return await self._collector_runtime.fetch_price_chart(
            symbol=symbol,
            market_scope=market_scope,
            interval=interval,
        )

    async def snapshot_control_plane(self) -> dict[str, Any]:
        snapshot = await self._control_plane.snapshot()
        return jsonable_encoder(snapshot)

    async def search_instruments(self, *, query: str, market_scope: str) -> dict[str, Any]:
        results = await self._control_plane.search_instruments(query=query, market_scope=market_scope)
        return jsonable_encoder({"query": query, "market_scope": market_scope, "instrument_results": results, "schema_version": "v1"})

    async def upsert_collection_target(self, payload: AdminTargetUpsertRequest) -> dict[str, Any]:
        result = await self._control_plane.upsert_target(
            target_id=payload.target_id,
            symbol=payload.symbol,
            market_scope=payload.resolved_market_scope(),
            event_types=payload.event_types,
            enabled=payload.enabled,
        )
        return jsonable_encoder(result)

    async def delete_collection_target(self, *, target_id: str) -> dict[str, Any]:
        result = await self._control_plane.delete_target(target_id=target_id)
        return jsonable_encoder(result)

    async def recent_runtime_events(
        self,
        *,
        target_id: str | None,
        symbol: str | None,
        market_scope: str | None,
        event_name: str | None,
        limit: int,
    ) -> dict[str, Any]:
        result = await self._control_plane.recent_events(
            target_id=target_id,
            symbol=symbol,
            market_scope=market_scope,
            event_name=event_name,
            limit=limit,
        )
        return jsonable_encoder(result)

    def subscribe_events(self):
        """Return an async context manager yielding a push queue of RecentRuntimeEvent objects."""
        return self._control_plane.subscribe_events()

    def is_publication_active(self, owner_id: str) -> bool:
        return self._collector_runtime.is_target_active(owner_id)

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
        market_scope = str(payload.get("market_scope") or payload.get("market") or "").strip()

        if action not in {"start", "stop"} or not owner_id:
            return

        if action == "start":
            try:
                await self.start_dashboard_publication(symbol=symbol, market_scope=market_scope, owner_id=owner_id)
            except Exception:
                logger.exception("collector failed to start dashboard publication", extra={"symbol": symbol, "market_scope": market_scope})
            return

        await self.stop_dashboard_publication(subscription_id=owner_id)

    async def _handle_runtime_event(self, *, symbol: str, market_scope: str, event_name: str, payload: dict[str, Any]) -> None:
        await self._publisher.publish_dashboard_event(
            symbol=symbol,
            market_scope=market_scope,
            event_name=event_name,
            payload=payload,
        )
        await self._control_plane.record_runtime_event(
            symbol=symbol,
            market_scope=market_scope,
            event_name=event_name,
            payload=payload,
        )

    async def _handle_runtime_failure(self, *, symbol: str, market_scope: str, error: str) -> None:
        await self._control_plane.record_publication_failure(
            symbol=symbol,
            market_scope=market_scope,
            error=error,
        )
        logger.error(
            "collector dashboard upstream failed",
            extra={"symbol": symbol, "market_scope": market_scope},
        )

    async def _handle_runtime_recovery(self, *, symbol: str, market_scope: str) -> None:
        """Clear stale error state for a symbol/market_scope when a new session starts."""
        await self._control_plane.clear_publication_errors(
            symbol=symbol,
            market_scope=market_scope,
        )

    def _build_subscription(self, *, symbol: str, market_scope: str) -> DashboardSubscription:
        normalized_symbol = symbol.strip()
        if not normalized_symbol:
            raise ValueError("symbol is required")
        normalized_market_scope = _resolve_market_scope(scope=market_scope)
        return DashboardSubscription(symbol=normalized_symbol, market_scope=normalized_market_scope)


def _all_dashboard_event_types() -> tuple[str, ...]:
    return tuple(event_type.value for event_type in EventType)


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
            "market_scope": service_settings.market,
            "market": service_settings.market,
        }
    )


@app.get("/admin/snapshot")
async def admin_snapshot() -> JSONResponse:
    return JSONResponse(await dashboard_service.snapshot_control_plane())


@app.get("/admin/instruments")
async def admin_instrument_search(
    query: str = Query(..., min_length=1),
    scope: str | None = Query(None, pattern="^(krx|nxt|total)$"),
    market: str | None = Query(None, pattern="^(krx|nxt|total)$"),
) -> JSONResponse:
    try:
        payload = await dashboard_service.search_instruments(query=query, market_scope=_resolve_market_scope(scope=scope, market=market))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(payload)


@app.put("/admin/targets")
async def admin_upsert_target(payload: AdminTargetUpsertRequest) -> JSONResponse:
    try:
        result = await dashboard_service.upsert_collection_target(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@app.delete("/admin/targets/{target_id}")
async def admin_delete_target(target_id: str) -> JSONResponse:
    payload = await dashboard_service.delete_collection_target(target_id=target_id)
    status_code = 404 if payload["status"] == "not_found" else 200
    return JSONResponse(payload, status_code=status_code)


@app.get("/admin/events")
async def admin_recent_events(
    target_id: str | None = Query(None),
    symbol: str | None = Query(None),
    scope: str | None = Query(None, pattern="^(krx|nxt|total)$"),
    market: str | None = Query(None, pattern="^(krx|nxt|total)$"),
    event_name: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    try:
        payload = await dashboard_service.recent_runtime_events(
            target_id=target_id,
            symbol=symbol,
            market_scope=_resolve_market_scope(scope=scope, market=market) if (scope or market) else None,
            event_name=event_name,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(payload)


@app.get("/admin/events/stream")
async def admin_events_stream(
    request: Request,
    target_id: str | None = Query(None),
    symbol: str | None = Query(None),
    scope: str | None = Query(None, pattern="^(krx|nxt|total)$"),
    market: str | None = Query(None, pattern="^(krx|nxt|total)$"),
    event_name: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> StreamingResponse:
    """True push SSE stream for admin runtime events — no polling."""
    try:
        market_scope_filter = _resolve_market_scope(scope=scope, market=market) if (scope or market) else None
    except ValueError as exc:
        async def _error_gen() -> Any:
            yield f"event: upstream_error\ndata: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
        return StreamingResponse(_error_gen(), media_type="text/event-stream")

    normalized_symbol = symbol.strip() if symbol else None
    normalized_target_id = target_id.strip() if target_id else None
    normalized_event_name = event_name.strip().lower() if event_name else None

    async def event_generator() -> Any:
        # Immediately acknowledge the connection.
        yield "event: connected\ndata: {}\n\n"

        # Send the current buffer snapshot so the client sees existing events right away.
        try:
            initial = await dashboard_service.recent_runtime_events(
                target_id=normalized_target_id,
                symbol=normalized_symbol,
                market_scope=market_scope_filter,
                event_name=normalized_event_name,
                limit=limit,
            )
            initial_events = initial.get("recent_events") or []
            known_event_names: set[str] = set(initial.get("available_event_names") or [])
            if initial_events:
                batch = {
                    "new_events": initial_events,
                    "available_event_names": sorted(known_event_names),
                    "buffer_size": initial.get("buffer_size") or 0,
                    "captured_at": str(initial.get("captured_at") or ""),
                }
                yield f"event: events\ndata: {json.dumps(batch, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            yield f"event: upstream_error\ndata: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            known_event_names = set()

        # Stream new events as they are pushed by record_runtime_event().
        async with dashboard_service.subscribe_events() as queue:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Send a keep-alive comment to prevent proxy/browser timeouts.
                    yield ": heartbeat\n\n"
                    continue

                # Apply per-subscriber filters.
                if normalized_target_id and normalized_target_id not in event.matched_target_ids:
                    continue
                if normalized_symbol and event.symbol != normalized_symbol:
                    continue
                if market_scope_filter and event.market_scope != market_scope_filter:
                    continue
                if normalized_event_name and event.event_name != normalized_event_name:
                    continue

                known_event_names.add(event.event_name)
                serialized = jsonable_encoder(event)
                batch = {
                    "new_events": [serialized],
                    "available_event_names": sorted(known_event_names),
                    "buffer_size": 0,
                    "captured_at": serialized.get("published_at", ""),
                }
                yield f"event: events\ndata: {json.dumps(batch, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
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
            market_scope=payload.resolved_market_scope(),
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
    scope: str | None = Query(None, pattern="^(krx|nxt|total)$"),
    market: str | None = Query(None, pattern="^(krx|nxt|total)$"),
    interval: int = Query(..., ge=1, le=60),
) -> JSONResponse:
    if interval not in {1, 5, 10, 30, 60}:
        return JSONResponse({"error": "unsupported interval"}, status_code=400)

    market_scope: str | None = None
    try:
        market_scope = _resolve_market_scope(scope=scope, market=market)
        payload = await dashboard_service.fetch_price_chart(
            symbol=symbol,
            market_scope=market_scope,
            interval=interval,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except NotImplementedError as exc:
        return JSONResponse({"error": str(exc)}, status_code=501)
    except RuntimeError as exc:
        logger.warning(
            "collector price chart upstream failed",
            extra={"symbol": symbol, "market_scope": market_scope, "interval": interval},
        )
        return JSONResponse({"error": str(exc)}, status_code=502)
    except Exception:
        logger.exception(
            "collector price chart unexpected failure",
            extra={"symbol": symbol, "market_scope": market_scope, "interval": interval},
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
