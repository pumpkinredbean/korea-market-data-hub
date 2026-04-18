from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

from packages.contracts.admin import ControlPlaneSnapshot, EventTypeCatalogEntry, RecentRuntimeEvent
from packages.contracts.events import EventType
from packages.contracts.topics import DASHBOARD_EVENTS_TOPIC
from packages.domain.enums import AssetClass, InstrumentType, Provider, RuntimeState, Venue
from packages.domain.models import (
    CollectionTarget,
    CollectionTargetStatus,
    InstrumentRef,
    InstrumentSearchResult,
    RuntimeStatus,
    StorageBinding,
    build_canonical_symbol,
)


# KRX-only market selection scope.  Non-KXT providers pass ``""`` (empty)
# which means "not applicable".
SUPPORTED_MARKET_SCOPES = {"krx", "nxt", "total"}


@dataclass(frozen=True, slots=True)
class PermanentFailureMeta:
    """Captures KSXT KISSubscriptionError metadata for admin UI display."""

    reason: str
    rt_cd: str | None = None
    msg: str | None = None
    attempts: int | None = None

EVENT_TYPE_ALIASES: dict[str, str] = {
    "trade": "trade",
    "trade_price": "trade",
    "order_book_snapshot": "order_book_snapshot",
    "order_book": "order_book_snapshot",
    "program_trade": "program_trade",
}

EVENT_TYPE_DESCRIPTIONS: dict[EventType, str] = {
    EventType.TRADE: "실시간 체결 이벤트", 
    EventType.ORDER_BOOK_SNAPSHOT: "실시간 호가 스냅샷 이벤트",
    EventType.PROGRAM_TRADE: "프로그램 매매 집계 이벤트",
}

BOOTSTRAP_INSTRUMENTS: tuple[tuple[str, str], ...] = (
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("035420", "NAVER"),
    ("005380", "현대차"),
    ("035720", "카카오"),
)


class CollectorControlPlaneService:
    """In-memory collector-owned admin/control-plane state for the first slice."""

    def __init__(
        self,
        *,
        service_name: str,
        default_symbol: str,
        default_market_scope: str,
        start_publication: Callable[..., Awaitable[dict[str, object]]],
        stop_publication: Callable[..., Awaitable[dict[str, object]]],
        is_publication_active: Callable[[str], bool],
    ):
        self._service_name = service_name
        self._default_symbol = default_symbol.strip() or "005930"
        self._default_market_scope = self._normalize_market_scope(default_market_scope or "krx")
        self._start_publication = start_publication
        self._stop_publication = stop_publication
        self._is_publication_active = is_publication_active
        self._lock = asyncio.Lock()
        self._targets: dict[str, CollectionTarget] = {}
        self._target_errors: dict[str, str | None] = {}
        self._target_permanent_failures: dict[str, PermanentFailureMeta] = {}
        self._last_search_results: tuple[InstrumentSearchResult, ...] = ()
        self._service_state = RuntimeState.STARTING
        self._last_service_error: str | None = None
        self._last_event_at_by_target: dict[str, datetime] = {}
        self._recent_events: deque[RecentRuntimeEvent] = deque(maxlen=250)
        self._event_subscribers: list[asyncio.Queue[RecentRuntimeEvent]] = []
        # Meta subscribers receive SSE-ready tuples (event_type, payload) for
        # session-level signals (session_recovered, session_state_changed).
        self._meta_subscribers: list[asyncio.Queue[tuple[str, dict[str, Any]]]] = []
        self._session_state: str | None = None

    async def mark_running(self) -> None:
        async with self._lock:
            self._service_state = RuntimeState.RUNNING
            self._last_service_error = None

    async def mark_stopping(self) -> None:
        async with self._lock:
            self._service_state = RuntimeState.STOPPING

    async def mark_stopped(self) -> None:
        async with self._lock:
            self._service_state = RuntimeState.STOPPED

    async def snapshot(self) -> ControlPlaneSnapshot:
        async with self._lock:
            targets = tuple(self._targets.values())
            search_results = self._last_search_results
            target_errors = dict(self._target_errors)
            permanent_failures = dict(self._target_permanent_failures)
            service_state = self._service_state
            service_error = self._last_service_error
            last_event_at_by_target = dict(self._last_event_at_by_target)
            session_state = self._session_state

        statuses = tuple(
            self._build_target_status(
                target,
                target_errors.get(target.target_id),
                last_event_at=last_event_at_by_target.get(target.target_id),
                permanent=permanent_failures.get(target.target_id),
            )
            for target in targets
        )
        active_target_ids = tuple(target.target_id for target in targets if self._is_publication_active(target.target_id))
        degraded_error = next((status.last_error for status in statuses if status.last_error), None)
        runtime_state = RuntimeState.DEGRADED if degraded_error else service_state
        runtime_error = degraded_error or service_error

        return ControlPlaneSnapshot(
            captured_at=datetime.utcnow(),
            source_service=self._service_name,
            event_type_catalog=self._event_type_catalog(),
            instrument_results=search_results,
            collection_targets=targets,
            storage_bindings=self._storage_bindings(),
            runtime_status=(
                RuntimeStatus(
                    component="collector-control-plane",
                    state=runtime_state,
                    observed_at=datetime.utcnow(),
                    active_collection_target_ids=active_target_ids,
                    active_storage_binding_ids=(),
                    last_error=runtime_error,
                ),
            ),
            collection_target_status=statuses,
            session_state=session_state,
        )

    async def search_instruments(self, *, query: str, market_scope: str | None = None, limit: int = 10, provider: str | Provider | None = None) -> tuple[InstrumentSearchResult, ...]:
        normalized_query = query.strip()
        resolved_provider = self._normalize_provider(provider)
        resolved_market_scope = self._normalize_market_scope(
            market_scope if market_scope is not None else self._default_market_scope,
            provider=resolved_provider,
        )
        async with self._lock:
            target_symbols = tuple(target.instrument.symbol for target in self._targets.values())
        results = self._search_catalog(
            normalized_query,
            resolved_market_scope,
            target_symbols=target_symbols,
            limit=max(1, min(limit, 50)),
            provider=resolved_provider,
        )
        async with self._lock:
            self._last_search_results = results
        return results

    async def upsert_target(
        self,
        *,
        target_id: str | None,
        symbol: str,
        market_scope: str,
        event_types: list[str] | tuple[str, ...],
        enabled: bool,
        provider: str | Provider | None = None,
        instrument_type: str | InstrumentType | None = None,
    ) -> dict[str, object]:
        normalized_symbol = symbol.strip()
        if not normalized_symbol:
            raise ValueError("symbol is required")

        resolved_provider = self._normalize_provider(provider)
        resolved_instrument_type = self._normalize_instrument_type(
            instrument_type,
            provider=resolved_provider,
        )
        resolved_market_scope = self._normalize_market_scope(market_scope, provider=resolved_provider)
        normalized_event_types = self._normalize_event_types(event_types)
        requested_target_id = (target_id or "").strip()

        async with self._lock:
            existing_target_ids = [
                existing.target_id
                for existing in self._targets.values()
                if existing.instrument.symbol == normalized_symbol
                and existing.market_scope == resolved_market_scope
                and (existing.provider or Provider.KXT) == resolved_provider
                and (existing.instrument.instrument_type or InstrumentType.EQUITY)
                == (resolved_instrument_type or InstrumentType.EQUITY)
            ]

        resolved_target_id = requested_target_id or (existing_target_ids[0] if existing_target_ids else uuid.uuid4().hex)
        instrument_ref = self._build_instrument_ref(
            normalized_symbol,
            provider=resolved_provider,
            instrument_type=resolved_instrument_type,
        )
        target = CollectionTarget(
            target_id=resolved_target_id,
            instrument=instrument_ref,
            market_scope=resolved_market_scope,
            event_types=normalized_event_types,
            enabled=enabled,
            provider=resolved_provider,
            canonical_symbol=instrument_ref.canonical_symbol,
        )

        duplicate_target_ids = [existing_target_id for existing_target_id in existing_target_ids if existing_target_id != resolved_target_id]

        async with self._lock:
            self._targets[resolved_target_id] = target
            self._target_errors[resolved_target_id] = None
            self._target_permanent_failures.pop(resolved_target_id, None)
            for duplicate_target_id in duplicate_target_ids:
                self._targets.pop(duplicate_target_id, None)
                self._target_errors.pop(duplicate_target_id, None)
                self._target_permanent_failures.pop(duplicate_target_id, None)

        for duplicate_target_id in duplicate_target_ids:
            with contextlib.suppress(Exception):
                await self._stop_publication(subscription_id=duplicate_target_id)

        apply_error: str | None = None
        if enabled:
            try:
                await self._start_publication(
                    symbol=normalized_symbol,
                    market_scope=resolved_market_scope,
                    owner_id=resolved_target_id,
                    event_types=normalized_event_types,
                    provider=resolved_provider.value,
                    instrument_type=resolved_instrument_type.value if resolved_instrument_type else None,
                    canonical_symbol=instrument_ref.canonical_symbol,
                )
            except TypeError:
                # Backwards compatibility with start_publication callbacks that
                # do not yet accept provider-aware kwargs (e.g. older tests).
                await self._start_publication(
                    symbol=normalized_symbol,
                    market_scope=resolved_market_scope,
                    owner_id=resolved_target_id,
                    event_types=normalized_event_types,
                )
            except Exception as exc:
                apply_error = str(exc)
        else:
            try:
                await self._stop_publication(subscription_id=resolved_target_id)
            except Exception as exc:
                apply_error = str(exc)

        async with self._lock:
            self._target_errors[resolved_target_id] = apply_error
            self._last_service_error = apply_error

        return {
            "target": target,
            "status": self._build_target_status(target, apply_error),
            "applied": apply_error is None,
            "warning": apply_error,
            "deduplicated_target_ids": tuple(duplicate_target_ids),
        }

    async def delete_target(self, *, target_id: str) -> dict[str, object]:
        resolved_target_id = target_id.strip()
        async with self._lock:
            target = self._targets.pop(resolved_target_id, None)
            self._target_errors.pop(resolved_target_id, None)
            self._last_event_at_by_target.pop(resolved_target_id, None)
            self._target_permanent_failures.pop(resolved_target_id, None)

        if target is None:
            return {"target_id": resolved_target_id, "status": "not_found"}

        warning: str | None = None
        try:
            await self._stop_publication(subscription_id=resolved_target_id)
        except Exception as exc:
            warning = str(exc)

        return {
            "target_id": resolved_target_id,
            "status": "deleted",
            "warning": warning,
            "removed_target": target,
        }

    async def record_runtime_event(
        self,
        *,
        symbol: str,
        market_scope: str,
        event_name: str,
        payload: dict[str, Any],
        topic_name: str = DASHBOARD_EVENTS_TOPIC,
        provider: str | Provider | None = None,
        canonical_symbol: str | None = None,
    ) -> None:
        published_at = datetime.utcnow()
        normalized_symbol = symbol.strip()
        resolved_provider = self._normalize_provider(provider)
        normalized_market_scope = self._normalize_market_scope(market_scope, provider=resolved_provider)
        normalized_event_name = self._normalize_event_name(event_name)

        async with self._lock:
            matched_target_ids = tuple(
                target.target_id
                for target in self._targets.values()
                if target.enabled
                and self._target_matches_event(
                    target,
                    symbol=normalized_symbol,
                    market_scope=normalized_market_scope,
                    event_name=normalized_event_name,
                    provider=resolved_provider,
                    canonical_symbol=canonical_symbol,
                )
            )
            for target_id in matched_target_ids:
                self._last_event_at_by_target[target_id] = published_at
                self._target_errors[target_id] = None
            if matched_target_ids:
                self._last_service_error = None
            event = RecentRuntimeEvent(
                event_id=uuid.uuid4().hex,
                topic_name=topic_name,
                event_name=normalized_event_name,
                symbol=normalized_symbol,
                market_scope=normalized_market_scope,
                published_at=published_at,
                matched_target_ids=matched_target_ids,
                payload=payload,
                provider=resolved_provider.value,
                canonical_symbol=canonical_symbol,
            )
            self._recent_events.appendleft(event)
            subscribers = list(self._event_subscribers)

        # Notify subscribers outside the lock so no subscriber can block record_runtime_event.
        for queue in subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe_events(self) -> AsyncIterator[asyncio.Queue[RecentRuntimeEvent]]:
        """Async context manager yielding a queue that receives every new RecentRuntimeEvent.

        Events are delivered immediately when record_runtime_event() is called.
        The queue is bounded (maxsize=500); events are silently dropped for slow consumers.
        """
        queue: asyncio.Queue[RecentRuntimeEvent] = asyncio.Queue(maxsize=500)
        async with self._lock:
            self._event_subscribers.append(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                with contextlib.suppress(ValueError):
                    self._event_subscribers.remove(queue)

    async def record_publication_failure(self, *, symbol: str, market_scope: str, error: str) -> None:
        normalized_symbol = symbol.strip()
        normalized_market_scope = self._normalize_market_scope(market_scope)

        async with self._lock:
            matched_target_ids = tuple(
                target.target_id
                for target in self._targets.values()
                if target.instrument.symbol == normalized_symbol and target.market_scope == normalized_market_scope
            )
            for target_id in matched_target_ids:
                self._target_errors[target_id] = error
            self._last_service_error = error

    async def clear_publication_errors(self, *, symbol: str, market_scope: str) -> None:
        """Clear per-target error state for a symbol/market_scope pair.

        Called when a new upstream session is established so stale errors set by a
        previous session failure are removed before fresh events start arriving.
        Only clears errors for targets that currently have one; targets without an
        error are left untouched.
        """
        normalized_symbol = symbol.strip()
        normalized_market_scope = self._normalize_market_scope(market_scope)

        async with self._lock:
            matched_target_ids = tuple(
                target.target_id
                for target in self._targets.values()
                if target.instrument.symbol == normalized_symbol and target.market_scope == normalized_market_scope
            )
            cleared_any = False
            for target_id in matched_target_ids:
                if self._target_errors.get(target_id):
                    self._target_errors[target_id] = None
                    cleared_any = True
            if cleared_any:
                self._last_service_error = None

    async def clear_all_publication_errors(self) -> int:
        """Clear transient per-target error state across the board.

        Called when the KSXT session reports ``on_recovery`` so stale errors
        set by a previous cycle failure are cleared before fresh events start
        arriving.  Permanent failures are intentionally left untouched.

        Returns the number of targets whose transient error was actually
        cleared so recovery paths can be observed in logs.
        """
        async with self._lock:
            cleared = 0
            for target_id in list(self._target_errors.keys()):
                if self._target_errors.get(target_id):
                    self._target_errors[target_id] = None
                    cleared += 1
            self._last_service_error = None
        logger.info(
            "control_plane.clear_all_publication_errors cleared=%d",
            cleared,
        )
        return cleared

    async def mark_target_permanent_failure(self, *, target_id: str, reason: str, rt_cd: str | None, msg: str | None, attempts: int | None) -> None:
        resolved = target_id.strip()
        async with self._lock:
            meta = PermanentFailureMeta(reason=reason, rt_cd=rt_cd, msg=msg, attempts=attempts)
            self._target_permanent_failures[resolved] = meta
            # Mirror into last_error so existing panels still show something.
            self._target_errors[resolved] = f"permanent_failure:{reason}"

    async def clear_target_permanent_failure(self, *, target_id: str) -> None:
        resolved = target_id.strip()
        async with self._lock:
            self._target_permanent_failures.pop(resolved, None)
            if self._target_errors.get(resolved, "").startswith("permanent_failure:"):
                self._target_errors[resolved] = None

    async def mark_session_state(self, *, state: str | None) -> None:
        async with self._lock:
            self._session_state = state
            subscribers = list(self._meta_subscribers)
        payload = {"state": state, "observed_at": datetime.utcnow().isoformat()}
        delivered = 0
        dropped = 0
        for queue in subscribers:
            try:
                queue.put_nowait(("session_state_changed", payload))
                delivered += 1
            except asyncio.QueueFull:
                dropped += 1
        logger.info(
            "control_plane.mark_session_state state=%s subscribers=%d delivered=%d dropped=%d",
            state,
            len(subscribers),
            delivered,
            dropped,
        )

    async def broadcast_session_recovered(self) -> None:
        async with self._lock:
            subscribers = list(self._meta_subscribers)
        payload = {"observed_at": datetime.utcnow().isoformat()}
        delivered = 0
        dropped = 0
        for queue in subscribers:
            try:
                queue.put_nowait(("session_recovered", payload))
                delivered += 1
            except asyncio.QueueFull:
                dropped += 1
        logger.info(
            "control_plane.broadcast_session_recovered subscribers=%d delivered=%d dropped=%d",
            len(subscribers),
            delivered,
            dropped,
        )

    @asynccontextmanager
    async def subscribe_meta_events(self) -> AsyncIterator[asyncio.Queue[tuple[str, dict[str, Any]]]]:
        """Subscribe to session-level meta events (session_recovered, session_state_changed)."""
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._meta_subscribers.append(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                with contextlib.suppress(ValueError):
                    self._meta_subscribers.remove(queue)

    async def recent_events(
        self,
        *,
        target_id: str | None = None,
        symbol: str | None = None,
        market_scope: str | None = None,
        event_name: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        requested_target_id = (target_id or "").strip()
        requested_symbol = (symbol or "").strip()
        requested_market_scope = self._normalize_market_scope(market_scope) if market_scope else None
        requested_event_name = self._normalize_event_name(event_name) if event_name else ""
        resolved_limit = max(1, min(limit, 200))

        async with self._lock:
            events = tuple(self._recent_events)
            targets = tuple(self._targets.values())

        filtered_events: list[RecentRuntimeEvent] = []
        for event in events:
            if requested_target_id and requested_target_id not in event.matched_target_ids:
                continue
            if requested_symbol and event.symbol != requested_symbol:
                continue
            if requested_market_scope and event.market_scope != requested_market_scope:
                continue
            if requested_event_name and event.event_name != requested_event_name:
                continue
            filtered_events.append(event)
            if len(filtered_events) >= resolved_limit:
                break

        filtered_target_ids = {target_id for event in filtered_events for target_id in event.matched_target_ids}
        target_options = tuple(
            {
                "target_id": target.target_id,
                "symbol": target.instrument.symbol,
                "market_scope": target.market_scope,
                "enabled": target.enabled,
            }
            for target in targets
            if not filtered_target_ids or target.target_id in filtered_target_ids
        )

        return {
            "captured_at": datetime.utcnow(),
            "filters": {
                "target_id": requested_target_id or None,
                "symbol": requested_symbol or None,
                "market_scope": requested_market_scope,
                "event_name": requested_event_name or None,
                "limit": resolved_limit,
            },
            "available_event_names": tuple(sorted({event.event_name for event in events})),
            "recent_events": tuple(filtered_events),
            "target_options": target_options,
            "buffer_size": len(events),
            "schema_version": "v1",
        }

    def _event_type_catalog(self) -> tuple[EventTypeCatalogEntry, ...]:
        return tuple(
            EventTypeCatalogEntry(
                event_type=event_type,
                topic_name=DASHBOARD_EVENTS_TOPIC,
                description=EVENT_TYPE_DESCRIPTIONS[event_type],
            )
            for event_type in EventType
        )

    def _storage_bindings(self) -> tuple[StorageBinding, ...]:
        return ()

    def _build_target_status(self, target: CollectionTarget, last_error: str | None, *, last_event_at: datetime | None = None, permanent: PermanentFailureMeta | None = None) -> CollectionTargetStatus:
        is_active = self._is_publication_active(target.target_id)
        if permanent is not None:
            state = RuntimeState.ERROR
        elif last_error:
            state = RuntimeState.ERROR
        elif target.enabled and is_active:
            state = RuntimeState.RUNNING
        else:
            state = RuntimeState.STOPPED

        return CollectionTargetStatus(
            target_id=target.target_id,
            state=state,
            observed_at=datetime.utcnow(),
            last_event_at=last_event_at,
            last_error=last_error,
            permanent_failure=permanent is not None,
            failure_reason=permanent.reason if permanent is not None else None,
            failure_rt_cd=permanent.rt_cd if permanent is not None else None,
            failure_msg=permanent.msg if permanent is not None else None,
            failure_attempts=permanent.attempts if permanent is not None else None,
        )

    @staticmethod
    def _is_krx_symbol(query: str) -> bool:
        """Return True if the query looks like a bare 6-digit KRX stock code."""
        return len(query) == 6 and query.isdigit()

    def _search_catalog(
        self,
        query: str,
        market_scope: str,
        *,
        target_symbols: tuple[str, ...],
        limit: int,
        provider: Provider = Provider.KXT,
    ) -> tuple[InstrumentSearchResult, ...]:
        normalized_query = query.strip().lower()
        seen_symbols: set[str] = set()
        catalog_entries = list(BOOTSTRAP_INSTRUMENTS)
        if self._default_symbol not in {symbol for symbol, _name in catalog_entries}:
            catalog_entries.append((self._default_symbol, f"종목 {self._default_symbol}"))
        for target_symbol in target_symbols:
            if target_symbol not in {symbol for symbol, _name in catalog_entries}:
                catalog_entries.append((target_symbol, f"종목 {target_symbol}"))

        # If the query is itself a 6-digit symbol not already in the catalog,
        # inject it as a direct match so arbitrary KRX codes are always selectable.
        if self._is_krx_symbol(query) and query not in {s for s, _ in catalog_entries}:
            catalog_entries.insert(0, (query, f"종목 {query}"))

        results: list[InstrumentSearchResult] = []
        for symbol, display_name in catalog_entries:
            if normalized_query and normalized_query not in symbol.lower() and normalized_query not in display_name.lower():
                continue
            results.append(
                self._build_search_result(
                    symbol=symbol,
                    display_name=display_name,
                    market_scope=market_scope,
                    provider=provider,
                )
            )
            seen_symbols.add(symbol)
            if len(results) >= limit:
                return tuple(results)

        return tuple(results)

    def _build_search_result(
        self,
        *,
        symbol: str,
        display_name: str,
        market_scope: str,
        provider: Provider = Provider.KXT,
    ) -> InstrumentSearchResult:
        instrument_ref = self._build_instrument_ref(symbol, provider=provider)
        return InstrumentSearchResult(
            instrument=instrument_ref,
            display_name=display_name,
            market_scope=market_scope,
            provider_instrument_id=symbol,
            venue_code="KRX" if provider == Provider.KXT else None,
            is_active=True,
            provider=provider,
            canonical_symbol=instrument_ref.canonical_symbol,
        )

    def _build_instrument_ref(
        self,
        symbol: str,
        *,
        provider: Provider = Provider.KXT,
        instrument_type: InstrumentType | None = None,
    ) -> InstrumentRef:
        # KXT today means KRX equity; other providers default to crypto spot
        # semantics unless the caller overrides instrument_type.
        if provider == Provider.KXT:
            venue = Venue.KRX
            asset_class = AssetClass.EQUITY
            resolved_instrument_type = instrument_type or InstrumentType.EQUITY
        else:
            venue = Venue.BINANCE
            asset_class = AssetClass.CRYPTO
            resolved_instrument_type = instrument_type or InstrumentType.SPOT

        canonical = build_canonical_symbol(
            provider=provider,
            venue=venue,
            instrument_type=resolved_instrument_type,
            symbol=symbol,
        )
        return InstrumentRef(
            symbol=symbol,
            instrument_id=symbol,
            venue=venue,
            asset_class=asset_class,
            instrument_type=resolved_instrument_type,
            provider=provider,
            canonical_symbol=canonical,
        )

    def _normalize_event_types(self, event_types: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        available = {event_type.value for event_type in EventType}
        normalized = tuple(
            dict.fromkeys(
                self._normalize_event_name(event_type)
                for event_type in event_types
                if str(event_type).strip()
            )
        )
        if not normalized:
            raise ValueError("at least one event_type is required")
        invalid = [event_type for event_type in normalized if event_type not in available]
        if invalid:
            raise ValueError(f"unsupported event_types: {', '.join(invalid)}")
        return normalized

    def _normalize_event_name(self, event_name: str | None) -> str:
        normalized = str(event_name or "").strip().lower()
        return EVENT_TYPE_ALIASES.get(normalized, normalized)

    @staticmethod
    def _target_matches_event(
        target: CollectionTarget,
        *,
        symbol: str,
        market_scope: str,
        event_name: str,
        provider: Provider,
        canonical_symbol: str | None,
    ) -> bool:
        if event_name not in target.event_types:
            return False
        # Canonical-symbol comparison disambiguates spot vs perpetual on
        # the same base symbol (e.g. Binance BTCUSDT spot vs USDT-perp).
        if canonical_symbol and target.canonical_symbol:
            return canonical_symbol == target.canonical_symbol
        target_provider = target.provider or Provider.KXT
        if target_provider != provider:
            return False
        if target.instrument.symbol != symbol:
            return False
        if target_provider == Provider.KXT:
            return target.market_scope == market_scope
        # Non-KXT providers store empty market_scope; symbol+provider is enough.
        return True

    def _normalize_market_scope(self, market_scope: str | None, *, provider: Provider = Provider.KXT) -> str:
        """Normalise KRX selection scope.

        For non-KXT providers market_scope is a KRX-only concept; we store
        it as the empty string so the field never carries ``krx|nxt|total``
        semantics on crypto targets (see H23).
        """

        if provider != Provider.KXT:
            normalized = (market_scope or "").strip().lower()
            return normalized  # empty string == not applicable
        normalized = (market_scope or "").strip().lower()
        if normalized not in SUPPORTED_MARKET_SCOPES:
            raise ValueError(f"unsupported market scope: {market_scope}")
        return normalized

    def _normalize_provider(self, provider: str | Provider | None) -> Provider:
        if provider is None or provider == "":
            return Provider.KXT
        if isinstance(provider, Provider):
            return provider
        try:
            return Provider(str(provider).strip().lower())
        except ValueError as exc:
            raise ValueError(f"unsupported provider: {provider}") from exc

    def _normalize_instrument_type(
        self,
        instrument_type: str | InstrumentType | None,
        *,
        provider: Provider,
    ) -> InstrumentType:
        if instrument_type is None or instrument_type == "":
            return InstrumentType.EQUITY if provider == Provider.KXT else InstrumentType.SPOT
        if isinstance(instrument_type, InstrumentType):
            return instrument_type
        try:
            return InstrumentType(str(instrument_type).strip().lower())
        except ValueError as exc:
            raise ValueError(f"unsupported instrument_type: {instrument_type}") from exc
