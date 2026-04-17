from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from ksxt import (
    BarTimeframe as KSXTBarTimeframe,
    InstrumentRef as KSXTInstrumentRef,
    KISClient,
    MarketBar as KSXTMarketBar,
    Venue as KSXTVenue,
)

from packages.adapters.base import MarketDataEvent
from packages.adapters.kis.adapter import KISMarketDataAdapter
from packages.contracts import ChannelType, EventType
from packages.domain.enums import Venue
from packages.domain.models import InstrumentRef

logger = logging.getLogger(__name__)


TRADE_PRICE_RENAME_MAP = {
    "STCK_CNTG_HOUR": "체결시각",
    "STCK_PRPR": "현재가",
}

PROGRAM_TRADE_RENAME_MAP = {
    "STCK_CNTG_HOUR": "체결시각",
    "SELN_CNQN": "프로그램매도체결량",
    "SELN_TR_PBMN": "프로그램매도거래대금",
    "SHNU_CNQN": "프로그램매수체결량",
    "SHNU_TR_PBMN": "프로그램매수거래대금",
    "NTBY_CNQN": "프로그램순매수체결량",
    "NTBY_TR_PBMN": "프로그램순매수거래대금",
    "SELN_RSQN": "매도호가잔량",
    "SHNU_RSQN": "매수호가잔량",
    "WHOL_NTBY_QTY": "전체순매수호가잔량",
}

ORDER_BOOK_RENAME_MAP = {
    "BSOP_HOUR": "호가시각",
    **{f"ASKP{level}": f"매도호가{level}" for level in range(1, 11)},
    **{f"BIDP{level}": f"매수호가{level}" for level in range(1, 11)},
    **{f"ASKP_RSQN{level}": f"매도잔량{level}" for level in range(1, 11)},
    **{f"BIDP_RSQN{level}": f"매수잔량{level}" for level in range(1, 11)},
    "TOTAL_ASKP_RSQN": "총매도잔량",
    "TOTAL_BIDP_RSQN": "총매수잔량",
}


SUPPORTED_MARKET_SCOPES = {"krx", "nxt", "total"}


@dataclass(frozen=True, slots=True)
class DashboardStreamKey:
    symbol: str
    market_scope: str


@dataclass(frozen=True, slots=True)
class RuntimeTargetRegistration:
    owner_id: str
    stream_key: DashboardStreamKey
    event_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DashboardEventContext:
    stream_key: DashboardStreamKey
    event_name: str
    payload: dict[str, Any]


@dataclass(slots=True)
class _UpstreamPlan:
    subscriptions: list[Any]
    requested_event_names_by_key: dict[DashboardStreamKey, set[str]] = field(default_factory=dict)


ALL_EVENT_NAMES: tuple[str, ...] = tuple(event_type.value for event_type in EventType)
CHANNEL_TYPE_BY_EVENT_NAME: dict[str, ChannelType] = {
    EventType.TRADE.value: ChannelType.TRADE,
    EventType.ORDER_BOOK_SNAPSHOT.value: ChannelType.ORDER_BOOK_SNAPSHOT,
    EventType.PROGRAM_TRADE.value: ChannelType.PROGRAM_TRADE,
}


def _to_native_number(value: Any) -> int | float | str | None:
    if value is None:
        return None
    if hasattr(value, "to_integral_value"):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _rename_fields(fields: dict[str, Any], rename_map: dict[str, str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for source_key, target_key in rename_map.items():
        payload[target_key] = _to_native_number(fields.get(source_key))
    return payload


def _format_dashboard_event(event: MarketDataEvent) -> tuple[str, dict[str, Any]]:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    fields = raw_payload.get("fields") if isinstance(raw_payload.get("fields"), dict) else {}

    if event.event_type.value == "trade":
        payload = _rename_fields(fields, TRADE_PRICE_RENAME_MAP)
        payload["체결시각"] = event.occurred_at.strftime("%H:%M:%S")
        payload["received_at"] = event.received_at.isoformat()
        return "trade_price", payload

    if event.event_type.value == "order_book_snapshot":
        payload = _rename_fields(fields, ORDER_BOOK_RENAME_MAP)
        payload["received_at"] = event.received_at.isoformat()
        return "order_book", payload

    payload = _rename_fields(fields, PROGRAM_TRADE_RENAME_MAP)
    payload["체결시각"] = event.occurred_at.strftime("%H:%M:%S")
    payload["received_at"] = event.received_at.isoformat()
    return "program_trade", payload


_KST = timezone(timedelta(hours=9))


def _decimal_to_float(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _decimal_to_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _format_market_bars(bars: tuple[KSXTMarketBar, ...]) -> list[dict[str, Any]]:
    """Format KSXT MarketBar tuple into dashboard candle dicts (shape-only).

    Bucketing is performed by the KSXT client via ``interval_minutes``; this
    function does not re-aggregate rows.
    """

    formatted: list[dict[str, Any]] = []
    for bar in bars:
        opened_at = bar.opened_at
        if opened_at.tzinfo is None:
            opened_at_kst = opened_at.replace(tzinfo=_KST)
        else:
            opened_at_kst = opened_at.astimezone(_KST)

        formatted.append(
            {
                "time": int(opened_at_kst.timestamp()),
                "label": opened_at_kst.strftime("%H:%M"),
                "session_date": opened_at_kst.date().isoformat(),
                "source_time": opened_at_kst.strftime("%H%M%S"),
                "open": _decimal_to_float(bar.open),
                "high": _decimal_to_float(bar.high),
                "low": _decimal_to_float(bar.low),
                "close": _decimal_to_float(bar.close),
                "volume": _decimal_to_int(bar.volume),
            }
        )
    return formatted[-120:]


_BASE_RECONNECT_DELAY: float = 1.0
_MAX_RECONNECT_DELAY: float = 30.0


class CollectorRuntime:
    """Collector-owned live runtime for dashboard consumers."""

    def __init__(
        self,
        settings: Any,
        *,
        on_event: Callable[..., Awaitable[None]] | None = None,
        on_failure: Callable[..., Awaitable[None]] | None = None,
        on_recovery: Callable[..., Awaitable[None]] | None = None,
    ):
        self._adapter = KISMarketDataAdapter(settings)
        self._chart_client = KISClient(
            app_key=settings.app_key,
            app_secret=settings.app_secret,
            sandbox=False,
        )
        self._on_event = on_event
        self._on_failure = on_failure
        self._on_recovery = on_recovery
        self._lock = asyncio.Lock()
        self._refresh_event = asyncio.Event()
        self._registrations_by_owner: dict[str, RuntimeTargetRegistration] = {}
        self._upstream_task: asyncio.Task[None] | None = None
        self._closed = False

    async def aclose(self) -> None:
        self._closed = True
        self._refresh_event.set()
        async with self._lock:
            upstream_task = self._upstream_task
            self._upstream_task = None
            self._registrations_by_owner.clear()

        if upstream_task is not None:
            upstream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await upstream_task
        await self._chart_client.aclose()

    async def register_target(
        self,
        *,
        owner_id: str,
        symbol: str,
        market_scope: str,
        event_types: tuple[str, ...] | list[str] | None = None,
    ) -> RuntimeTargetRegistration:
        normalized_owner_id = owner_id.strip()
        if not normalized_owner_id:
            raise ValueError("owner_id is required")

        stream_key = self._build_stream_key(symbol=symbol, market_scope=market_scope)
        normalized_event_types = self._normalize_event_types(event_types)
        registration = RuntimeTargetRegistration(
            owner_id=normalized_owner_id,
            stream_key=stream_key,
            event_types=normalized_event_types,
        )

        async with self._lock:
            self._registrations_by_owner[normalized_owner_id] = registration
            self._ensure_upstream_task_locked()
            self._refresh_event.set()

        return registration

    async def unregister_target(self, *, owner_id: str) -> RuntimeTargetRegistration | None:
        normalized_owner_id = owner_id.strip()
        async with self._lock:
            registration = self._registrations_by_owner.pop(normalized_owner_id, None)
            if registration is not None:
                self._refresh_event.set()
        return registration

    def is_target_active(self, owner_id: str) -> bool:
        registration = self._registrations_by_owner.get(owner_id)
        return registration is not None and self._upstream_task is not None and not self._upstream_task.done()

    def _ensure_upstream_task_locked(self) -> None:
        if self._upstream_task is None or self._upstream_task.done():
            self._upstream_task = asyncio.create_task(self._run_upstream_session())

    async def _run_upstream_session(self) -> None:
        reconnect_delay: float = 0.0
        while not self._closed:
            refresh_event = self._refresh_event
            refresh_event.clear()

            plan = await self._build_upstream_plan()
            if not plan.subscriptions:
                await refresh_event.wait()
                continue

            try:
                auth = await self._adapter.auth.issue_realtime_credentials()
                # Signal recovery to clear any stale per-target error state before
                # the new session starts delivering events.
                await self._broadcast_recovery()
                async for row in self._adapter.realtime.stream_subscriptions_rows_until(
                    plan.subscriptions,
                    auth,
                    until=refresh_event,
                ):
                    event = self._adapter.map_dashboard_row(row)
                    published_event_name, payload = _format_dashboard_event(event)
                    event_context = _DashboardEventContext(
                        stream_key=DashboardStreamKey(
                            symbol=row.binding.spec.instrument.symbol,
                            market_scope=row.binding.market,
                        ),
                        event_name=event.event_type.value,
                        payload=payload,
                    )
                    if event_context.event_name not in plan.requested_event_names_by_key.get(event_context.stream_key, set()):
                        continue
                    await self._publish_event(event_context, published_event_name=published_event_name)
                # Clean session end (refresh requested or no subscriptions left); reset backoff.
                reconnect_delay = 0.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._broadcast_failure(exc)
                if self._closed:
                    return
                # Bounded exponential backoff: start at _BASE_RECONNECT_DELAY, double on
                # each successive failure, cap at _MAX_RECONNECT_DELAY.  A user-triggered
                # refresh (register/unregister) sets refresh_event and breaks the wait
                # immediately so mutations are never delayed by the backoff.
                reconnect_delay = min(
                    _BASE_RECONNECT_DELAY if reconnect_delay == 0.0 else reconnect_delay * 2,
                    _MAX_RECONNECT_DELAY,
                )
                try:
                    await asyncio.wait_for(refresh_event.wait(), timeout=reconnect_delay)
                except TimeoutError:
                    pass

    async def _build_upstream_plan(self) -> _UpstreamPlan:
        async with self._lock:
            registrations = tuple(self._registrations_by_owner.values())

        requested_event_names_by_key: dict[DashboardStreamKey, set[str]] = {}
        for registration in registrations:
            requested_event_names_by_key.setdefault(registration.stream_key, set()).update(registration.event_types)

        subscriptions: list[Any] = []
        for stream_key, event_names in requested_event_names_by_key.items():
            instrument = InstrumentRef(symbol=stream_key.symbol, instrument_id=stream_key.symbol, venue=Venue.KRX)
            for event_name in sorted(event_names):
                channel_type = CHANNEL_TYPE_BY_EVENT_NAME[event_name]
                subscriptions.append(
                    self._adapter.build_subscription_spec(
                        instrument=instrument,
                        channel_type=channel_type,
                        market=stream_key.market_scope,
                    )
                )

        return _UpstreamPlan(subscriptions=subscriptions, requested_event_names_by_key=requested_event_names_by_key)

    async def _publish_event(self, event_context: _DashboardEventContext, *, published_event_name: str) -> None:
        if self._on_event is None:
            return
        await self._on_event(
            symbol=event_context.stream_key.symbol,
            market_scope=event_context.stream_key.market_scope,
            event_name=published_event_name,
            payload=dict(event_context.payload),
        )

    async def _broadcast_failure(self, exc: BaseException) -> None:
        if self._on_failure is None:
            return
        async with self._lock:
            stream_keys = tuple({registration.stream_key for registration in self._registrations_by_owner.values()})
        for stream_key in stream_keys:
            await self._on_failure(
                symbol=stream_key.symbol,
                market_scope=stream_key.market_scope,
                error=str(exc),
            )

    async def _broadcast_recovery(self) -> None:
        """Notify the service layer that a new session has started so stale per-target
        error state can be cleared before events begin arriving again."""
        if self._on_recovery is None:
            return
        async with self._lock:
            stream_keys = tuple({registration.stream_key for registration in self._registrations_by_owner.values()})
        for stream_key in stream_keys:
            await self._on_recovery(
                symbol=stream_key.symbol,
                market_scope=stream_key.market_scope,
            )

    def _build_stream_key(self, *, symbol: str, market_scope: str) -> DashboardStreamKey:
        normalized_market_scope = market_scope.strip().lower()
        if normalized_market_scope not in SUPPORTED_MARKET_SCOPES:
            raise ValueError(f"unsupported market scope: {market_scope}")
        normalized_symbol = symbol.strip()
        if not normalized_symbol:
            raise ValueError("symbol is required")
        return DashboardStreamKey(symbol=normalized_symbol, market_scope=normalized_market_scope)

    def _normalize_event_types(self, event_types: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if event_types is None:
            return ALL_EVENT_NAMES

        normalized = []
        seen: set[str] = set()
        for event_type in event_types:
            candidate = str(event_type or "").strip().lower()
            if candidate not in CHANNEL_TYPE_BY_EVENT_NAME:
                raise ValueError(f"unsupported event type: {event_type}")
            if candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        if not normalized:
            raise ValueError("at least one event type is required")
        return tuple(normalized)

    async def fetch_price_chart(self, *, symbol: str, market_scope: str, interval: int) -> dict[str, Any]:
        normalized_scope = (market_scope or "").strip().lower() or "krx"
        scope_fallback = normalized_scope != "krx"
        if scope_fallback:
            logger.warning(
                "KSXT does not differentiate market_scope=%s; requesting KRX bars",
                normalized_scope,
            )

        instrument = KSXTInstrumentRef(symbol=symbol, venue=KSXTVenue.KRX)
        # Match the legacy hub behavior of anchoring intraday requests to the
        # KST 15:30 session close so late-session calls still cover the full
        # trading day.
        end = datetime.now(_KST).replace(hour=15, minute=30, second=0, microsecond=0)
        bars = await self._chart_client.fetch_bars(
            instrument,
            timeframe=KSXTBarTimeframe.MINUTE,
            end=end,
            interval_minutes=interval,
        )
        candles = _format_market_bars(bars)
        session_date = (
            candles[-1]["session_date"]
            if candles
            else datetime.now(_KST).date().isoformat()
        )
        return {
            "symbol": symbol,
            "market_scope": market_scope,
            "market": market_scope,
            "interval": interval,
            "candles": candles,
            "session_date": session_date,
            "source": "ksxt:KISClient.fetch_bars",
            "tr_id": "FHKST03010200",
            "scope_fallback": scope_fallback,
        }
