from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from kxt import (
    BarTimeframe as KSXTBarTimeframe,
    InstrumentRef as KSXTInstrumentRef,
    KISClient,
    KISRealtimeSession,
    KISSubscriptionError,
    MarketBar as KSXTMarketBar,
    OrderBookEvent as KSXTOrderBookEvent,
    OrderBookSnapshot as KSXTOrderBookSnapshot,
    RealtimeState,
    RealtimeSessionConfig,
    StreamKind,
    Subscription as KSXTSubscription,
    Trade as KSXTTrade,
    TradeEvent as KSXTTradeEvent,
    Venue as KSXTVenue,
)

from packages.contracts import EventType

logger = logging.getLogger(__name__)


SUPPORTED_MARKET_SCOPES = {"krx", "nxt", "total"}

# Event type names as used by the admin UI / control plane.
_EVENT_TRADE = EventType.TRADE.value
_EVENT_ORDER_BOOK = EventType.ORDER_BOOK_SNAPSHOT.value
_EVENT_PROGRAM_TRADE = EventType.PROGRAM_TRADE.value

# Map admin event names to KSXT StreamKind.  ``program_trade`` is not exposed by
# the KSXT realtime session (only trades + order_book).  Targets requesting
# program_trade will be flagged as permanent failures for that channel — this
# is a scoped limitation of KSXT v0.1.0 noted in hub-B report §8.
_STREAM_KIND_BY_EVENT_NAME: dict[str, StreamKind] = {
    _EVENT_TRADE: StreamKind.trades,
    _EVENT_ORDER_BOOK: StreamKind.order_book,
}

ALL_EVENT_NAMES: tuple[str, ...] = tuple(event_type.value for event_type in EventType)

_KST = timezone(timedelta(hours=9))


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
class _ChannelKey:
    symbol: str
    market_scope: str
    event_name: str  # trade | order_book_snapshot | program_trade


@dataclass(slots=True)
class _ChannelEntry:
    subscription: KSXTSubscription | None
    task: asyncio.Task[None] | None
    owners: set[str]
    permanent_failure: bool = False
    events_seen: bool = False
    ack_watchdog: asyncio.Task[None] | None = None


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


def _number(value: Any) -> int | float | str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _format_trade_event(event: KSXTTradeEvent | KSXTTrade) -> tuple[str, dict[str, Any]]:
    occurred_at = event.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at_kst = occurred_at.replace(tzinfo=timezone.utc).astimezone(_KST)
    else:
        occurred_at_kst = occurred_at.astimezone(_KST)
    payload = {
        "체결시각": occurred_at_kst.strftime("%H:%M:%S"),
        "현재가": _number(event.price),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    return "trade_price", payload


def _format_order_book_event(event: KSXTOrderBookEvent | KSXTOrderBookSnapshot) -> tuple[str, dict[str, Any]]:
    occurred_at = event.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at_kst = occurred_at.replace(tzinfo=timezone.utc).astimezone(_KST)
    else:
        occurred_at_kst = occurred_at.astimezone(_KST)
    payload: dict[str, Any] = {
        "호가시각": occurred_at_kst.strftime("%H%M%S"),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    for index, level in enumerate(event.asks[:10], start=1):
        payload[f"매도호가{index}"] = _number(level.price)
        payload[f"매도잔량{index}"] = _number(level.quantity)
    for index, level in enumerate(event.bids[:10], start=1):
        payload[f"매수호가{index}"] = _number(level.price)
        payload[f"매수잔량{index}"] = _number(level.quantity)
    payload["총매도잔량"] = _number(event.total_ask_quantity)
    payload["총매수잔량"] = _number(event.total_bid_quantity)
    return "order_book", payload


def _format_market_bars(bars: tuple[KSXTMarketBar, ...]) -> list[dict[str, Any]]:
    """Format KSXT MarketBar tuple into dashboard candle dicts (shape-only)."""

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


class CollectorRuntime:
    """Collector-owned live runtime driven by the KSXT ``KISRealtimeSession``.

    This runtime owns a single :class:`KISRealtimeSession` and maps admin
    collection targets onto per-``(symbol, event_type)`` KSXT subscriptions.
    Subscription retries, reconnects, and permanent-failure classification
    are delegated to KSXT itself; the collector only consumes events and
    surfaces session/subscription signals to the admin control plane.
    """

    _SESSION_READY_TIMEOUT: float = 10.0
    _SUBSCRIBE_ACK_TIMEOUT: float = 10.0

    def __init__(
        self,
        settings: Any,
        *,
        on_event: Callable[..., Awaitable[None]] | None = None,
        on_failure: Callable[..., Awaitable[None]] | None = None,
        on_recovery: Callable[..., Awaitable[None]] | None = None,
        on_session_state_change: Callable[..., Awaitable[None]] | None = None,
        on_permanent_failure: Callable[..., Awaitable[None]] | None = None,
    ):
        self._client = KISClient(
            app_key=settings.app_key,
            app_secret=settings.app_secret,
            sandbox=False,
        )
        # Access the underlying transport so we can build a session with
        # our own state/recovery callbacks.  ``client.realtime`` constructs
        # a session without callbacks; for the hub we need the ctor path.
        # Private access is documented in the hub-B migration report.
        self._session = KISRealtimeSession(
            self._client._transport,  # noqa: SLF001 — documented private access
            config=RealtimeSessionConfig(),
            on_state_change=self._handle_state_change,
            on_recovery=self._handle_recovery,
        )
        self._on_event = on_event
        self._on_failure = on_failure
        self._on_recovery = on_recovery
        self._on_session_state_change = on_session_state_change
        self._on_permanent_failure = on_permanent_failure
        self._lock = asyncio.Lock()
        self._registrations_by_owner: dict[str, RuntimeTargetRegistration] = {}
        self._channels: dict[_ChannelKey, _ChannelEntry] = {}
        self._closed = False

    # ---- lifecycle ------------------------------------------------------

    async def aclose(self) -> None:
        self._closed = True
        async with self._lock:
            channels = tuple(self._channels.values())
            self._channels.clear()
            self._registrations_by_owner.clear()

        for entry in channels:
            task = entry.task
            subscription = entry.subscription
            watchdog = entry.ack_watchdog
            if watchdog is not None:
                watchdog.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await watchdog
            if subscription is not None:
                with contextlib.suppress(Exception):
                    await subscription.aclose()
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        with contextlib.suppress(Exception):
            await self._session.aclose()
        with contextlib.suppress(Exception):
            await self._client.aclose()

    # ---- registrations --------------------------------------------------

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

        await self._wait_session_ready(timeout=self._SESSION_READY_TIMEOUT)

        # Snapshot previous registration (if any) and compute diffs outside
        # the channel-lock so we can safely invoke async KSXT ops.
        async with self._lock:
            previous = self._registrations_by_owner.get(normalized_owner_id)
            self._registrations_by_owner[normalized_owner_id] = registration

            previous_channels = (
                {_ChannelKey(stream_key.symbol, stream_key.market_scope, ev) for ev in previous.event_types}
                if previous is not None
                else set()
            )
            new_channels = {_ChannelKey(stream_key.symbol, stream_key.market_scope, ev) for ev in normalized_event_types}
            to_add = new_channels - previous_channels
            to_remove = previous_channels - new_channels

        for channel_key in to_add:
            await self._acquire_channel(channel_key, owner_id=normalized_owner_id)
        for channel_key in to_remove:
            await self._release_channel(channel_key, owner_id=normalized_owner_id)

        return registration

    async def unregister_target(self, *, owner_id: str) -> RuntimeTargetRegistration | None:
        normalized_owner_id = owner_id.strip()
        async with self._lock:
            registration = self._registrations_by_owner.pop(normalized_owner_id, None)
            to_release = (
                {
                    _ChannelKey(registration.stream_key.symbol, registration.stream_key.market_scope, ev)
                    for ev in registration.event_types
                }
                if registration is not None
                else set()
            )
        for channel_key in to_release:
            await self._release_channel(channel_key, owner_id=normalized_owner_id)
        return registration

    def is_target_active(self, owner_id: str) -> bool:
        return owner_id in self._registrations_by_owner and not self._closed

    # ---- channel management --------------------------------------------

    async def _acquire_channel(self, channel_key: _ChannelKey, *, owner_id: str) -> None:
        async with self._lock:
            entry = self._channels.get(channel_key)
            if entry is not None:
                entry.owners.add(owner_id)
                already_permanent = entry.permanent_failure
            else:
                entry = _ChannelEntry(subscription=None, task=None, owners={owner_id})
                self._channels[channel_key] = entry
                already_permanent = False

        if already_permanent:
            # Surface the existing permanent failure so the new owner also
            # shows up as permanently failed in the admin UI.
            await self._dispatch_permanent_failure(
                channel_key,
                reason="unsupported_by_ksxt" if _STREAM_KIND_BY_EVENT_NAME.get(channel_key.event_name) is None else "previously_failed",
                rt_cd=None,
                msg=None,
                attempts=None,
            )
            return

        if entry.subscription is not None:
            # Channel already streaming — nothing else to do.
            return

        stream_kind = _STREAM_KIND_BY_EVENT_NAME.get(channel_key.event_name)
        if stream_kind is None:
            # Event type not supported by KSXT realtime (e.g., program_trade).
            # Mark a synthetic permanent failure so admin UI reflects reality
            # rather than leaving the target in a pending state forever.
            logger.warning(
                "event_type=%s is not supported by KSXT realtime; marking target as permanent failure",
                channel_key.event_name,
            )
            entry.permanent_failure = True
            await self._dispatch_permanent_failure(
                channel_key,
                reason="unsupported_by_ksxt",
                rt_cd=None,
                msg=f"KSXT realtime does not expose {channel_key.event_name} stream",
                attempts=0,
            )
            return

        instrument = KSXTInstrumentRef(symbol=channel_key.symbol, venue=KSXTVenue.KRX)
        try:
            sub = await self._session.subscribe(stream_kind, instrument)
        except KISSubscriptionError as exc:
            entry.permanent_failure = True
            await self._dispatch_permanent_failure(
                channel_key,
                reason=exc.reason,
                rt_cd=exc.rt_cd,
                msg=exc.msg,
                attempts=exc.attempts,
            )
            return
        except Exception as exc:
            logger.exception("subscribe failed for %s", channel_key)
            await self._dispatch_failure(channel_key, error=str(exc))
            return

        async with self._lock:
            entry.subscription = sub
            entry.task = asyncio.create_task(
                self._consume_subscription(channel_key, sub),
                name=f"ksxt-sub-{channel_key.symbol}-{channel_key.event_name}",
            )
            entry.ack_watchdog = asyncio.create_task(
                self._watch_subscription_ack(channel_key),
                name=f"ksxt-ack-watchdog-{channel_key.symbol}-{channel_key.event_name}",
            )
        logger.info(
            "KSXT subscribe sent tr_type=1 symbol=%s event=%s tr_id=%s",
            channel_key.symbol,
            channel_key.event_name,
            stream_kind.value if hasattr(stream_kind, "value") else stream_kind,
        )

    async def _release_channel(self, channel_key: _ChannelKey, *, owner_id: str) -> None:
        async with self._lock:
            entry = self._channels.get(channel_key)
            if entry is None:
                return
            entry.owners.discard(owner_id)
            if entry.owners:
                return
            # No owners remain — tear down.
            self._channels.pop(channel_key, None)
            task = entry.task
            subscription = entry.subscription
            watchdog = entry.ack_watchdog

        if watchdog is not None:
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watchdog
        if subscription is not None:
            with contextlib.suppress(Exception):
                await subscription.aclose()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _consume_subscription(self, channel_key: _ChannelKey, subscription: KSXTSubscription) -> None:
        try:
            async for event in subscription.events():
                # First event arrival implies the KIS server accepted the
                # subscription; cancel any outstanding ack watchdog.
                async with self._lock:
                    entry = self._channels.get(channel_key)
                    if entry is not None and not entry.events_seen:
                        entry.events_seen = True
                        logger.info(
                            "KSXT subscribe acked via event symbol=%s event=%s",
                            channel_key.symbol,
                            channel_key.event_name,
                        )
                try:
                    if isinstance(event, (KSXTTradeEvent, KSXTTrade)):
                        published_event_name, payload = _format_trade_event(event)
                        event_name_canonical = _EVENT_TRADE
                    elif isinstance(event, (KSXTOrderBookEvent, KSXTOrderBookSnapshot)):
                        published_event_name, payload = _format_order_book_event(event)
                        event_name_canonical = _EVENT_ORDER_BOOK
                    else:
                        logger.debug("dropping unknown KSXT event type: %r", type(event))
                        continue
                    # Filter to only publish the canonical event_name a target asked for.
                    if event_name_canonical != channel_key.event_name:
                        continue
                    await self._publish_event(
                        symbol=channel_key.symbol,
                        market_scope=channel_key.market_scope,
                        event_name=published_event_name,
                        payload=payload,
                    )
                except Exception:
                    logger.exception("failed to publish event for %s", channel_key)
        except asyncio.CancelledError:
            raise
        except KISSubscriptionError as exc:
            logger.warning(
                "KSXT subscription permanently failed: %s rt_cd=%s msg=%s attempts=%s",
                channel_key,
                exc.rt_cd,
                exc.msg,
                exc.attempts,
            )
            async with self._lock:
                entry = self._channels.get(channel_key)
                if entry is not None:
                    entry.permanent_failure = True
                    entry.subscription = None
                    watchdog = entry.ack_watchdog
                    entry.ack_watchdog = None
                else:
                    watchdog = None
            if watchdog is not None:
                watchdog.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await watchdog
            await self._dispatch_permanent_failure(
                channel_key,
                reason=exc.reason,
                rt_cd=exc.rt_cd,
                msg=exc.msg,
                attempts=exc.attempts,
            )
        except Exception as exc:
            logger.exception("subscription consumer crashed: %s", channel_key)
            await self._dispatch_failure(channel_key, error=str(exc))

    # ---- session-level callbacks ---------------------------------------

    async def _watch_subscription_ack(self, channel_key: _ChannelKey) -> None:
        """Ack watchdog: defense-in-depth vs KSXT silently-swallowed ack timeout.

        KSXT ``session.subscribe`` (see ksxt/clients/kis/realtime/session.py:130-141)
        silently swallows the internal ack timeout, returning a pending
        Subscription without surfacing the failure. This watchdog gives the
        subscription ``_SUBSCRIBE_ACK_TIMEOUT`` seconds to either receive its
        first event or raise ``KISSubscriptionError``. If neither happens, we
        mark a hub-level permanent failure with ``reason='subscribe_ack_timeout'``
        so the admin UI surfaces the stuck target instead of leaving it
        silently pending forever. Tracked as KSXT-FOLLOWUP-1 / -2.
        """
        try:
            await asyncio.sleep(self._SUBSCRIBE_ACK_TIMEOUT)
        except asyncio.CancelledError:
            return

        async with self._lock:
            entry = self._channels.get(channel_key)
            if entry is None:
                return
            if entry.events_seen or entry.permanent_failure:
                return
            entry.permanent_failure = True
            subscription = entry.subscription
            entry.subscription = None

        logger.warning(
            "KSXT subscribe ack timeout — marking permanent failure symbol=%s event=%s timeout=%.1fs",
            channel_key.symbol,
            channel_key.event_name,
            self._SUBSCRIBE_ACK_TIMEOUT,
        )
        await self._dispatch_permanent_failure(
            channel_key,
            reason="subscribe_ack_timeout",
            rt_cd=None,
            msg=(
                f"no KSXT subscribe ack within {self._SUBSCRIBE_ACK_TIMEOUT:.0f}s "
                "(no event received, no KISSubscriptionError raised)"
            ),
            attempts=1,
        )
        if subscription is not None:
            with contextlib.suppress(Exception):
                await subscription.aclose()

    async def _handle_state_change(self, old: RealtimeState, new: RealtimeState) -> None:
        logger.info("KSXT session state: %s -> %s", old.value, new.value)
        if self._on_session_state_change is None:
            return
        try:
            await self._on_session_state_change(state=new.value, previous=old.value)
        except Exception:
            logger.exception("on_session_state_change handler failed")

    async def _handle_recovery(self) -> None:
        logger.info("KSXT session recovery complete")
        if self._on_recovery is None:
            return
        try:
            await self._on_recovery()
        except Exception:
            logger.exception("on_recovery handler failed")

    # ---- session readiness ---------------------------------------------

    async def _wait_session_ready(self, *, timeout: float) -> None:
        """Poll ``session.state`` until HEALTHY or timeout.

        Triggers a session start on first call by invoking ``start()`` directly
        so ``subscribe(...)`` has a connection in flight rather than racing
        against a cold-start subscribe.
        """
        with contextlib.suppress(Exception):
            await self._session.start()
        deadline = asyncio.get_running_loop().time() + timeout
        while not self._closed:
            state = self._session.state
            if state == RealtimeState.HEALTHY:
                return
            if state == RealtimeState.CLOSED:
                raise RuntimeError("KSXT session is closed")
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning("KSXT session did not reach HEALTHY within %.1fs (state=%s)", timeout, state.value)
                return
            await asyncio.sleep(0.1)

    # ---- dispatch helpers ----------------------------------------------

    async def _publish_event(self, *, symbol: str, market_scope: str, event_name: str, payload: dict[str, Any]) -> None:
        if self._on_event is None:
            return
        try:
            await self._on_event(
                symbol=symbol,
                market_scope=market_scope,
                event_name=event_name,
                payload=payload,
            )
        except Exception:
            logger.exception("on_event handler failed for %s/%s", symbol, event_name)

    async def _dispatch_failure(self, channel_key: _ChannelKey, *, error: str) -> None:
        if self._on_failure is None:
            return
        try:
            await self._on_failure(
                symbol=channel_key.symbol,
                market_scope=channel_key.market_scope,
                error=error,
            )
        except Exception:
            logger.exception("on_failure handler failed for %s", channel_key)

    async def _dispatch_permanent_failure(
        self,
        channel_key: _ChannelKey,
        *,
        reason: str,
        rt_cd: str | None,
        msg: str | None,
        attempts: int | None,
    ) -> None:
        if self._on_permanent_failure is None:
            return
        async with self._lock:
            entry = self._channels.get(channel_key)
            owners = tuple(entry.owners) if entry is not None else ()
        try:
            await self._on_permanent_failure(
                symbol=channel_key.symbol,
                market_scope=channel_key.market_scope,
                event_name=channel_key.event_name,
                owner_ids=owners,
                reason=reason,
                rt_cd=rt_cd,
                msg=msg,
                attempts=attempts,
            )
        except Exception:
            logger.exception("on_permanent_failure handler failed for %s", channel_key)

    # ---- helpers --------------------------------------------------------

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

        normalized: list[str] = []
        seen: set[str] = set()
        allowed = set(ALL_EVENT_NAMES)
        for event_type in event_types:
            candidate = str(event_type or "").strip().lower()
            if candidate not in allowed:
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
        end = datetime.now(_KST).replace(hour=15, minute=30, second=0, microsecond=0)
        bars = await self._client.fetch_bars(
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
