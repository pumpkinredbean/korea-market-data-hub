"""Hub-owned contract tests for the KSXT-backed CollectorRuntime.

Scope (hub-E, decision H5 = (C) 부분 재작성):

KSXT 소유 영역의 프레임 파싱, subscribe/unsubscribe 프로토콜, 재접속 backoff,
approval_key 발급 관련 테스트는 모두 폐기되었다 (hub-B 에서 삭제 후 재작성하지
않음 — blockers.md KSXT-FOLLOWUP-1/-2 및 hub-B 리포트 §8.1 분류 (a) 7건 참조).

본 파일은 hub가 사용자에게 약속하는 관찰 가능한 행동(계약)만 방어한다:
  (1) market_scope=nxt 요청 시 KRX venue 로 fallback 하며 scope_fallback 신호 + WARN
  (2) market_scope=total 요청 시 동일
  (3) fetch_bars 에 전달되는 end anchor 가 KST 15:30 임
  (4) Subscription.events() 가 KISSubscriptionError 방출 시 target 영구 실패 +
      자동 재subscribe 0건 (addendum §A 반패턴 회귀 방어)
  (5) subscribe ack watchdog 타임아웃 시 target permanent_failure 마킹 +
      다른 target 은 영향 없이 계속 동작

모두 real KSXT 네트워크 호출 없이 mock / monkeypatch / spy 로 동작한다.
"""
from __future__ import annotations

import asyncio
import logging
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# hub-B restored probe (H4): session recovery contract — kept from prior file.
# ---------------------------------------------------------------------------


class CollectorRuntimeImportTests(unittest.TestCase):
    def test_runtime_module_imports_ksxt_session(self) -> None:
        # Bare import — the runtime module should not pull the legacy
        # packages.adapters.kis websocket adapter anymore.
        from apps.collector import runtime as runtime_module

        self.assertTrue(hasattr(runtime_module, "CollectorRuntime"))
        self.assertTrue(hasattr(runtime_module, "SUPPORTED_MARKET_SCOPES"))
        # Confirm the legacy WS supervisor symbols have been removed — these
        # are exit criteria from the hub-B migration packet.
        for removed in (
            "_run_upstream_session",
            "_BASE_RECONNECT_DELAY",
            "_MAX_RECONNECT_DELAY",
            "_broadcast_recovery",
            "_broadcast_failure",
        ):
            self.assertFalse(
                hasattr(runtime_module, removed),
                f"{removed} must be removed from collector runtime (hub-B exit criteria)",
            )

    def test_runtime_uses_ksxt_session(self) -> None:
        from ksxt import KISRealtimeSession, RealtimeState

        # Sanity: public exports referenced by the runtime exist.
        self.assertTrue(hasattr(KISRealtimeSession, "subscribe"))
        self.assertTrue(hasattr(RealtimeState, "HEALTHY"))


class SessionRecoveryPropagationTests(unittest.IsolatedAsyncioTestCase):
    """Restored hub-B regression probe (H4 decision).

    Pin the contract that ``CollectorControlPlaneService``'s publication-error
    state is cleared when ``on_recovery`` fires and that a ``session_recovered``
    meta event is delivered to subscribers.
    """

    async def test_session_failure_broadcasts_to_all_targets_and_recovery_clears_errors(self) -> None:
        from src.collector_control_plane import CollectorControlPlaneService

        started: list[dict[str, object]] = []
        stopped: list[dict[str, object]] = []
        active_owners: set[str] = set()

        async def fake_start(**kwargs: object) -> dict[str, object]:
            started.append(kwargs)
            active_owners.add(str(kwargs["owner_id"]))
            return {"subscription_id": kwargs["owner_id"], "status": "started"}

        async def fake_stop(*, subscription_id: str) -> dict[str, object]:
            stopped.append({"subscription_id": subscription_id})
            active_owners.discard(subscription_id)
            return {"subscription_id": subscription_id, "status": "stopped"}

        service = CollectorControlPlaneService(
            service_name="collector",
            default_symbol="005930",
            default_market_scope="krx",
            start_publication=fake_start,
            stop_publication=fake_stop,
            is_publication_active=lambda owner_id: owner_id in active_owners,
        )

        upsert_a = await service.upsert_target(
            target_id=None,
            symbol="005930",
            market_scope="krx",
            event_types=["trade"],
            enabled=True,
        )
        upsert_b = await service.upsert_target(
            target_id=None,
            symbol="000660",
            market_scope="krx",
            event_types=["trade"],
            enabled=True,
        )
        target_id_a = upsert_a["target"].target_id  # type: ignore[attr-defined]
        target_id_b = upsert_b["target"].target_id  # type: ignore[attr-defined]

        await service.record_publication_failure(
            symbol="005930", market_scope="krx", error="upstream disconnected"
        )
        await service.record_publication_failure(
            symbol="000660", market_scope="krx", error="upstream disconnected"
        )
        snapshot_degraded = await service.snapshot()
        statuses_degraded = {s.target_id: s for s in snapshot_degraded.collection_target_status}
        self.assertEqual(statuses_degraded[target_id_a].last_error, "upstream disconnected")
        self.assertEqual(statuses_degraded[target_id_b].last_error, "upstream disconnected")

        async with service.subscribe_meta_events() as meta_queue:
            await service.clear_all_publication_errors()
            await service.broadcast_session_recovered()
            meta_event = await asyncio.wait_for(meta_queue.get(), timeout=1.0)

        self.assertEqual(meta_event[0], "session_recovered")
        self.assertIn("observed_at", meta_event[1])

        snapshot_recovered = await service.snapshot()
        statuses_recovered = {s.target_id: s for s in snapshot_recovered.collection_target_status}
        self.assertIsNone(statuses_recovered[target_id_a].last_error)
        self.assertIsNone(statuses_recovered[target_id_b].last_error)

        events_snapshot = await service.recent_events(limit=50)
        self.assertEqual(events_snapshot["recent_events"], ())


# ---------------------------------------------------------------------------
# hub-E new contract tests
# ---------------------------------------------------------------------------


def _fake_settings() -> SimpleNamespace:
    """Minimal kis_settings-shaped object. No network I/O is triggered on ctor."""

    return SimpleNamespace(app_key="test-key", app_secret="test-secret")


def _build_runtime(**callbacks):
    from apps.collector.runtime import CollectorRuntime

    return CollectorRuntime(_fake_settings(), **callbacks)


class FetchPriceChartScopeFallbackTests(unittest.IsolatedAsyncioTestCase):
    """hub-E tests #1, #2 — scope fallback signal + WARN log.

    Contract (hub-A precommit check 2): ``fetch_price_chart`` always routes
    through KSXT ``KRX`` venue (KSXT v0.1.0 exposes no NXT/TOTAL venue).  The
    response dict carries ``scope_fallback: bool`` — ``True`` iff the caller
    asked for a non-KRX scope — and a WARN log is emitted so downstream
    consumers / operations can surface the fallback.
    """

    async def _run_fallback_case(self, requested_scope: str) -> None:
        runtime = _build_runtime()
        try:
            # Spy on KSXT REST fetch_bars so zero network I/O escapes.
            spy = mock.AsyncMock(return_value=())
            runtime._client.fetch_bars = spy  # type: ignore[assignment]

            logger_name = "apps.collector.runtime"
            with self.assertLogs(logger_name, level=logging.WARNING) as cap:
                result = await runtime.fetch_price_chart(
                    symbol="005930",
                    market_scope=requested_scope,
                    interval=1,
                )

            # scope_fallback is a boolean (confirmed against runtime.py:670 —
            # ``scope_fallback = normalized_scope != "krx"``). hub-A-precommit
            # report §2 pins this shape.
            self.assertIs(result["scope_fallback"], True)
            # Response preserves the caller's requested scope for UI context.
            self.assertEqual(result["market_scope"], requested_scope)

            # Exactly one WARN line from the runtime logger about the fallback.
            fallback_warnings = [
                rec
                for rec in cap.records
                if rec.levelno == logging.WARNING
                and "market_scope" in rec.getMessage()
            ]
            self.assertEqual(len(fallback_warnings), 1, cap.output)

            # Spy confirms KSXT fetch_bars was invoked exactly once with a
            # KRX venue instrument (the fallback destination).
            spy.assert_awaited_once()
            call = spy.await_args
            assert call is not None
            instrument = call.args[0]
            self.assertEqual(instrument.symbol, "005930")
            self.assertEqual(instrument.venue.value, "KRX")
        finally:
            await runtime.aclose()

    async def test_scope_fallback_nxt_marks_krx_with_signal(self) -> None:
        await self._run_fallback_case("nxt")

    async def test_scope_fallback_total_marks_krx_with_signal(self) -> None:
        await self._run_fallback_case("total")

    async def test_scope_krx_does_not_trigger_fallback(self) -> None:
        """Baseline negative — krx requests must not flag fallback."""

        runtime = _build_runtime()
        try:
            runtime._client.fetch_bars = mock.AsyncMock(return_value=())  # type: ignore[assignment]
            result = await runtime.fetch_price_chart(
                symbol="005930", market_scope="krx", interval=1
            )
            self.assertIs(result["scope_fallback"], False)
        finally:
            await runtime.aclose()


class FetchPriceChartKstAnchorTests(unittest.IsolatedAsyncioTestCase):
    """hub-E test #3 — the ``end`` kwarg handed to KSXT is a KST 15:30 anchor."""

    async def test_kst_anchor_passed_to_ksxt_fetch_bars(self) -> None:
        runtime = _build_runtime()
        try:
            spy = mock.AsyncMock(return_value=())
            runtime._client.fetch_bars = spy  # type: ignore[assignment]

            await runtime.fetch_price_chart(
                symbol="005930", market_scope="krx", interval=1
            )

            spy.assert_awaited_once()
            call = spy.await_args
            assert call is not None
            end = call.kwargs["end"]
            self.assertIsInstance(end, datetime)
            # Timezone-aware and locked to Asia/Seoul (+09:00), not UTC.
            self.assertIsNotNone(end.tzinfo)
            self.assertEqual(end.utcoffset(), timedelta(hours=9))
            # KST wall-clock 15:30:00.000 — the KRX regular-session close.
            self.assertEqual((end.hour, end.minute, end.second, end.microsecond), (15, 30, 0, 0))
            # Equivalently: 15:30 KST == 06:30 UTC.
            self.assertEqual(end.astimezone(timezone.utc).hour, 6)
            self.assertEqual(end.astimezone(timezone.utc).minute, 30)
            # And the tz should round-trip through zoneinfo for Asia/Seoul.
            self.assertEqual(
                end.astimezone(ZoneInfo("Asia/Seoul")).utcoffset(),
                timedelta(hours=9),
            )
        finally:
            await runtime.aclose()


# ---------------------------------------------------------------------------
# Permanent failure + ack watchdog contract tests
# ---------------------------------------------------------------------------


class _FakeSubscription:
    """Minimal Subscription-shaped mock for runtime consumer loop.

    ``events()`` behaviour is driven by an async callable supplied at
    construction time.  ``aclose`` is a no-op awaitable.  ``closed`` is
    a plain attribute (runtime only reads it via the KSXT public
    ``subscription.aclose()`` path).
    """

    def __init__(self, event_stream):
        self._event_stream = event_stream
        self.closed = False
        self.aclose_calls = 0

    def events(self):
        return self._event_stream()

    async def aclose(self) -> None:
        self.aclose_calls += 1
        self.closed = True


class PermanentFailureContractTests(unittest.IsolatedAsyncioTestCase):
    """hub-E test #4 — KISSubscriptionError on the events() iterator.

    Pins the contract from addendum §A: the hub does NOT auto re-subscribe
    after a permanent failure.  ``session.subscribe`` must be called exactly
    once for the lifetime of the permanently-failed channel.
    """

    async def test_permanent_failure_marks_target_on_subscription_error(self) -> None:
        from ksxt import KISSubscriptionError, StreamKind, InstrumentRef as KSXTInstrumentRef, Venue as KSXTVenue

        permanent_calls: list[dict[str, object]] = []

        async def on_permanent_failure(**kwargs):
            permanent_calls.append(kwargs)

        runtime = _build_runtime(on_permanent_failure=on_permanent_failure)
        try:
            # Neutralise session readiness — no real WS start, no polling loop.
            async def _noop_wait(**_):
                return None

            runtime._wait_session_ready = _noop_wait  # type: ignore[assignment]

            # events() yields nothing and then raises the terminal KSXT error.
            async def _error_stream():
                raise KISSubscriptionError(
                    stream_kind=StreamKind.trades,
                    instrument=KSXTInstrumentRef(symbol="000000", venue=KSXTVenue.KRX),
                    reason="permanent_rt_cd",
                    rt_cd="9001",
                    msg="delisted",
                    attempts=5,
                )
                # Make this an async generator so `async for` works.
                yield  # pragma: no cover

            subscribe_spy = mock.AsyncMock(
                return_value=_FakeSubscription(_error_stream),
            )
            runtime._session.subscribe = subscribe_spy  # type: ignore[assignment]
            # Neutralise ack watchdog — unrelated to this path.
            from apps.collector.runtime import CollectorRuntime
            CollectorRuntime._SUBSCRIBE_ACK_TIMEOUT = 5.0  # keep default

            await runtime.register_target(
                owner_id="t-perm",
                symbol="000000",
                market_scope="krx",
                event_types=("trade",),
            )

            # Wait for the consumer task to process the error and dispatch.
            for _ in range(50):
                if permanent_calls:
                    break
                await asyncio.sleep(0.02)

            self.assertEqual(len(permanent_calls), 1, "on_permanent_failure expected exactly once")
            call = permanent_calls[0]
            self.assertEqual(call["reason"], "permanent_rt_cd")
            self.assertEqual(call["rt_cd"], "9001")
            self.assertEqual(call["msg"], "delisted")
            self.assertEqual(call["attempts"], 5)
            self.assertEqual(call["symbol"], "000000")
            self.assertEqual(call["event_name"], "trade")
            self.assertIn("t-perm", call["owner_ids"])

            # Addendum §A anti-pattern guard: NO auto re-subscribe.
            self.assertEqual(
                subscribe_spy.await_count,
                1,
                "hub must NOT auto re-subscribe after permanent failure",
            )
        finally:
            await runtime.aclose()


class SubscribeAckTimeoutWatchdogTests(unittest.IsolatedAsyncioTestCase):
    """hub-E test #5 — ack watchdog flips target to permanent failure.

    Defense-in-depth against KSXT-FOLLOWUP-1 (session.subscribe silently
    swallows its internal ack timeout).  A second concurrently-registered
    target must keep functioning — the watchdog is strictly per-channel.
    """

    async def test_subscribe_ack_timeout_marks_pending(self) -> None:
        from apps.collector.runtime import CollectorRuntime

        permanent_calls: list[dict[str, object]] = []
        events_received: list[dict[str, object]] = []

        async def on_permanent_failure(**kwargs):
            permanent_calls.append(kwargs)

        async def on_event(**kwargs):
            events_received.append(kwargs)

        # Shrink the ack watchdog for this test (spec: 10s → 0.1s).
        original_ack_timeout = CollectorRuntime._SUBSCRIBE_ACK_TIMEOUT
        CollectorRuntime._SUBSCRIBE_ACK_TIMEOUT = 0.1

        runtime = _build_runtime(
            on_event=on_event,
            on_permanent_failure=on_permanent_failure,
        )
        try:

            async def _noop_wait(**_):
                return None

            runtime._wait_session_ready = _noop_wait  # type: ignore[assignment]

            # Target A: events() never yields. Watchdog should fire.
            async def _silent_stream():
                await asyncio.Event().wait()  # park forever
                yield  # pragma: no cover

            # Target B: events() yields one KSXTTradeEvent then parks.
            from ksxt import (
                InstrumentRef as KSXTInstrumentRef,
                TradeEvent as KSXTTradeEvent,
                Venue as KSXTVenue,
            )

            async def _healthy_stream():
                trade = KSXTTradeEvent(
                    instrument=KSXTInstrumentRef(symbol="005930", venue=KSXTVenue.KRX),
                    occurred_at=datetime.now(timezone.utc),
                    price=Decimal("70000"),
                    quantity=Decimal("1"),
                    side="buy",
                )
                yield trade
                await asyncio.Event().wait()

            silent_sub = _FakeSubscription(_silent_stream)
            healthy_sub = _FakeSubscription(_healthy_stream)

            async def fake_subscribe(stream_kind, instrument):
                if instrument.symbol == "999999":
                    return silent_sub
                return healthy_sub

            runtime._session.subscribe = mock.AsyncMock(side_effect=fake_subscribe)  # type: ignore[assignment]

            await runtime.register_target(
                owner_id="t-silent",
                symbol="999999",
                market_scope="krx",
                event_types=("trade",),
            )
            await runtime.register_target(
                owner_id="t-healthy",
                symbol="005930",
                market_scope="krx",
                event_types=("trade",),
            )

            # Wait up to 2s for the 0.1s watchdog to dispatch.
            for _ in range(100):
                if permanent_calls and events_received:
                    break
                await asyncio.sleep(0.02)

            self.assertTrue(
                permanent_calls,
                "subscribe_ack_timeout watchdog must dispatch permanent failure",
            )
            silent_call = next(
                (c for c in permanent_calls if c.get("symbol") == "999999"), None
            )
            self.assertIsNotNone(silent_call, "silent target must be marked permanent")
            assert silent_call is not None
            self.assertEqual(silent_call["reason"], "subscribe_ack_timeout")
            self.assertEqual(silent_call["attempts"], 1)
            self.assertIn("t-silent", silent_call["owner_ids"])

            # Healthy target must NOT be marked permanently failed, and its
            # published event must have landed on the on_event hook.
            healthy_calls = [c for c in permanent_calls if c.get("symbol") == "005930"]
            self.assertEqual(healthy_calls, [], "healthy target must not be permanently failed")
            self.assertTrue(
                any(ev.get("symbol") == "005930" for ev in events_received),
                "healthy target must continue to receive events during ack timeout on peer",
            )
        finally:
            await runtime.aclose()
            CollectorRuntime._SUBSCRIBE_ACK_TIMEOUT = original_ack_timeout


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
