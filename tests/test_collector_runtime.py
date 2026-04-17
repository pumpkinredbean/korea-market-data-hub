from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from apps.collector.runtime import CollectorRuntime, _BASE_RECONNECT_DELAY, _MAX_RECONNECT_DELAY
from packages.contracts import ChannelType, EventType, SubscriptionSpec
from packages.domain.enums import Venue
from packages.domain.models import InstrumentRef
from packages.adapters.kis.mappers import KISSubscriptionBinding
from packages.adapters.kis.realtime import KISRealtimeClient


class _FakeAuthProvider:
    async def issue_realtime_credentials(self) -> object:
        return object()


class _FakeRealtimeClient:
    def __init__(self) -> None:
        self.call_count = 0
        self.max_active_sessions = 0
        self._active_sessions = 0
        self.subscription_batches: list[list[SubscriptionSpec]] = []
        # Set to a non-None Exception to make the next session raise instead of stream.
        self.fail_next: Exception | None = None

    async def stream_subscriptions_rows_until(self, subscriptions, auth, *, until):
        self.call_count += 1
        self._active_sessions += 1
        self.max_active_sessions = max(self.max_active_sessions, self._active_sessions)
        self.subscription_batches.append(list(subscriptions))
        exc = self.fail_next
        self.fail_next = None
        try:
            if exc is not None:
                raise exc
            while not until.is_set():
                await asyncio.sleep(0.01)
            if False:
                yield None
        finally:
            self._active_sessions -= 1


class _FakeAdapter:
    def __init__(self) -> None:
        self.auth = _FakeAuthProvider()
        self.realtime = _FakeRealtimeClient()

    def build_subscription_spec(self, instrument, channel_type, **options):
        return SubscriptionSpec(instrument=instrument, channel_type=channel_type, options=dict(options))

    def map_dashboard_row(self, row):
        return SimpleNamespace(
            event_type=EventType.TRADE,
            raw_payload={"fields": {"STCK_CNTG_HOUR": "090000", "STCK_PRPR": "1000"}},
            occurred_at=datetime.now(timezone.utc),
            received_at=datetime.now(timezone.utc),
        )


class _FakeChartClient:
    async def aclose(self) -> None:
        return None


def _make_runtime(
    *,
    on_event=None,
    on_failure=None,
    on_recovery=None,
) -> tuple[CollectorRuntime, _FakeAdapter]:
    """Build a CollectorRuntime wired with a _FakeAdapter for testing."""
    runtime = CollectorRuntime(
        SimpleNamespace(ws_url="ws://example", rest_url="https://example", app_key="x", app_secret="y"),
        on_event=on_event,
        on_failure=on_failure,
        on_recovery=on_recovery,
    )
    fake_adapter = _FakeAdapter()
    runtime._adapter = fake_adapter
    runtime._chart_client = _FakeChartClient()
    return runtime, fake_adapter


async def _wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before timeout")


class CollectorRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_targets_share_one_upstream_task(self) -> None:
        events: list[dict[str, object]] = []
        failures: list[dict[str, object]] = []
        runtime, fake_adapter = _make_runtime(
            on_event=lambda **payload: _capture(events, payload),
            on_failure=lambda **payload: _capture(failures, payload),
        )

        await runtime.register_target(owner_id="target-1", symbol="005930", market_scope="krx", event_types=["trade"])
        await _wait_for(lambda: fake_adapter.realtime.call_count == 1)
        self.assertEqual(1, len(fake_adapter.realtime.subscription_batches[-1]))

        await runtime.register_target(owner_id="target-2", symbol="000660", market_scope="krx", event_types=["trade"])
        await _wait_for(lambda: fake_adapter.realtime.call_count == 2)
        self.assertEqual(2, len(fake_adapter.realtime.subscription_batches[-1]))
        self.assertEqual(1, fake_adapter.realtime.max_active_sessions)
        self.assertTrue(runtime.is_target_active("target-1"))
        self.assertTrue(runtime.is_target_active("target-2"))

        await runtime.aclose()
        self.assertEqual([], events)
        self.assertEqual([], failures)

    async def test_remove_target_while_live_refreshes_without_concurrent_sessions(self) -> None:
        """Unregistering a target while connected rebuilds the session with the remaining symbol only."""
        runtime, fake_adapter = _make_runtime()

        await runtime.register_target(owner_id="owner-a", symbol="005930", market_scope="krx", event_types=["trade"])
        await _wait_for(lambda: fake_adapter.realtime.call_count == 1)
        await runtime.register_target(owner_id="owner-b", symbol="000660", market_scope="krx", event_types=["trade"])
        await _wait_for(lambda: fake_adapter.realtime.call_count == 2)
        self.assertEqual(2, len(fake_adapter.realtime.subscription_batches[-1]))

        await runtime.unregister_target(owner_id="owner-b")
        await _wait_for(lambda: fake_adapter.realtime.call_count == 3)

        # Only one symbol left in the last subscription batch.
        self.assertEqual(1, len(fake_adapter.realtime.subscription_batches[-1]))
        # Never had more than one concurrent session.
        self.assertEqual(1, fake_adapter.realtime.max_active_sessions)

        await runtime.aclose()

    async def test_update_event_types_while_live_refreshes_session(self) -> None:
        """Re-registering an owner with different event types triggers a clean session rebuild."""
        runtime, fake_adapter = _make_runtime()

        await runtime.register_target(owner_id="owner-a", symbol="005930", market_scope="krx", event_types=["trade"])
        await _wait_for(lambda: fake_adapter.realtime.call_count == 1)
        first_batch = fake_adapter.realtime.subscription_batches[0]
        self.assertEqual(1, len(first_batch))

        # Re-register the same owner with an additional event type.
        await runtime.register_target(
            owner_id="owner-a",
            symbol="005930",
            market_scope="krx",
            event_types=["trade", "order_book_snapshot"],
        )
        await _wait_for(lambda: fake_adapter.realtime.call_count == 2)
        second_batch = fake_adapter.realtime.subscription_batches[1]
        # Two channel subscriptions for the same symbol now.
        self.assertEqual(2, len(second_batch))
        # No concurrent sessions at any point.
        self.assertEqual(1, fake_adapter.realtime.max_active_sessions)

        await runtime.aclose()

    async def test_session_failure_broadcasts_to_all_targets_and_recovery_clears_errors(self) -> None:
        """Session failure fans out errors; on_recovery fires for each registered stream key after rebuild."""
        failures: list[dict[str, object]] = []
        recoveries: list[dict[str, object]] = []
        runtime, fake_adapter = _make_runtime(
            on_failure=lambda **payload: _capture(failures, payload),
            on_recovery=lambda **payload: _capture(recoveries, payload),
        )

        await runtime.register_target(owner_id="owner-a", symbol="005930", market_scope="krx", event_types=["trade"])
        await _wait_for(lambda: fake_adapter.realtime.call_count == 1)
        await runtime.register_target(owner_id="owner-b", symbol="000660", market_scope="krx", event_types=["trade"])
        await _wait_for(lambda: fake_adapter.realtime.call_count == 2)

        # Inject a failure so the next reconnect attempt raises.
        fake_adapter.realtime.fail_next = RuntimeError("upstream disconnected")
        # Trigger refresh so the running session ends and a new one starts (and fails).
        runtime._refresh_event.set()

        await _wait_for(lambda: len(failures) >= 2, timeout=2.0)
        # Both registered stream keys should receive a failure notification.
        failed_symbols = {f["symbol"] for f in failures}
        self.assertIn("005930", failed_symbols)
        self.assertIn("000660", failed_symbols)

        # The subsequent successful reconnect should broadcast recovery for both keys.
        await _wait_for(lambda: fake_adapter.realtime.call_count >= 4, timeout=3.0)
        await _wait_for(lambda: len(recoveries) >= 2, timeout=2.0)
        recovered_symbols = {r["symbol"] for r in recoveries}
        self.assertIn("005930", recovered_symbols)
        self.assertIn("000660", recovered_symbols)

        await runtime.aclose()

    async def test_reconnect_backoff_constants_are_bounded(self) -> None:
        """Verify the backoff constants are within expected bounds so repeated failures don't spin fast."""
        self.assertGreaterEqual(_BASE_RECONNECT_DELAY, 1.0, "_BASE_RECONNECT_DELAY must be at least 1 second")
        self.assertLessEqual(_MAX_RECONNECT_DELAY, 60.0, "_MAX_RECONNECT_DELAY must be at most 60 seconds")
        self.assertLess(_BASE_RECONNECT_DELAY, _MAX_RECONNECT_DELAY, "BASE must be less than MAX")

    async def test_reconnect_backoff_grows_and_caps(self) -> None:
        """Simulate the backoff accumulation logic used inside _run_upstream_session."""
        delay = 0.0
        delays = []
        for _ in range(10):
            delay = min(
                _BASE_RECONNECT_DELAY if delay == 0.0 else delay * 2,
                _MAX_RECONNECT_DELAY,
            )
            delays.append(delay)

        self.assertEqual(delays[0], _BASE_RECONNECT_DELAY)
        self.assertTrue(all(d <= _MAX_RECONNECT_DELAY for d in delays), f"All delays must be ≤ {_MAX_RECONNECT_DELAY}: {delays}")
        self.assertEqual(delays[-1], _MAX_RECONNECT_DELAY, "Delay must saturate at MAX after enough doublings")

class KISRealtimeClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_realtime_rows_bind_by_symbol_with_shared_tr_id(self) -> None:
        client = KISRealtimeClient(SimpleNamespace(ws_url="ws://example"))
        instrument_a = InstrumentRef(symbol="005930", instrument_id="005930", venue=Venue.KRX)
        instrument_b = InstrumentRef(symbol="000660", instrument_id="000660", venue=Venue.KRX)
        binding_a = KISSubscriptionBinding(
            spec=SubscriptionSpec(instrument=instrument_a, channel_type=ChannelType.TRADE, options={"market": "krx"}),
            tr_id="H0STCNT0",
            tr_key="005930",
            market="krx",
        )
        binding_b = KISSubscriptionBinding(
            spec=SubscriptionSpec(instrument=instrument_b, channel_type=ChannelType.TRADE, options={"market": "krx"}),
            tr_id="H0STCNT0",
            tr_key="000660",
            market="krx",
        )

        raw = "0|H0STCNT0|2|005930^090000^1000^0^0^0^0^0^0^0^0^0^10^10^10000^000660^090001^2000^0^0^0^0^0^0^0^0^0^20^20^20000"
        rows = await client._parse_many_message(
            raw=raw,
            bindings_by_subscription_key={("H0STCNT0", "005930"): binding_a, ("H0STCNT0", "000660"): binding_b},
            ws=SimpleNamespace(pong=_noop_pong),
        )

        assert rows is not None
        self.assertEqual(["005930", "000660"], [row.binding.tr_key for row in rows])
        self.assertEqual(["005930", "000660"], [row.fields["MKSC_SHRN_ISCD"] for row in rows])

    def test_connect_disables_protocol_ping(self) -> None:
        """KIS uses app-level PINGPONG; protocol-level ping must be disabled to avoid keepalive timeout."""
        import inspect
        client = KISRealtimeClient(SimpleNamespace(ws_url="ws://example"))
        # Inspect the connect() call_args by examining the source — we verify the constants
        # exported from the module rather than actually opening a socket.
        import websockets
        # The connect() method should pass ping_interval=None and ping_timeout=None.
        # We can't call client.connect() without a real server, so we verify the method
        # body returns a context manager built without protocol-level ping args.
        import ast, textwrap
        source = textwrap.dedent(inspect.getsource(client.connect))
        tree = ast.parse(source)
        found_ping_interval_none = False
        found_ping_timeout_none = False
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword):
                if node.arg == "ping_interval" and isinstance(node.value, ast.Constant) and node.value.value is None:
                    found_ping_interval_none = True
                if node.arg == "ping_timeout" and isinstance(node.value, ast.Constant) and node.value.value is None:
                    found_ping_timeout_none = True
        self.assertTrue(found_ping_interval_none, "connect() must pass ping_interval=None to disable protocol ping")
        self.assertTrue(found_ping_timeout_none, "connect() must pass ping_timeout=None to disable protocol ping")


async def _capture(bucket: list[dict[str, object]], payload: dict[str, object]) -> None:
    bucket.append(payload)


async def _noop_pong(_raw) -> None:
    return None

