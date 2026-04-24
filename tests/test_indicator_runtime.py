"""Unit tests for the admin-charts indicator runtime (step 1)."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class OBIMathTests(unittest.TestCase):
    def test_obi_value_from_order_book_snapshot(self) -> None:
        from src.indicator_runtime import OrderBookImbalanceIndicator

        indicator = OrderBookImbalanceIndicator(top_n=3)
        event = {
            "event_type": "order_book_snapshot",
            "symbol": "TEST",
            "market_scope": "",
            "payload": {
                "bids": [[100.0, 3.0], [99.5, 2.0], [99.0, 5.0]],
                "asks": [[100.5, 1.0], [101.0, 2.0], [101.5, 2.0]],
            },
            "published_at": "2025-01-01T00:00:00+00:00",
        }
        point = indicator.on_event(event)
        self.assertIsNotNone(point)
        assert point is not None  # for mypy
        bid = 3.0 + 2.0 + 5.0
        ask = 1.0 + 2.0 + 2.0
        expected = (bid - ask) / (bid + ask)
        self.assertAlmostEqual(point.value, expected, places=9)
        self.assertEqual(point.meta["top_n"], 3)
        self.assertEqual(point.timestamp, "2025-01-01T00:00:00+00:00")

    def test_obi_handles_dict_levels_and_quantity_key(self) -> None:
        from src.indicator_runtime import OrderBookImbalanceIndicator

        indicator = OrderBookImbalanceIndicator()
        event = {
            "event_type": "order_book_snapshot",
            "symbol": "T",
            "market_scope": "",
            "payload": {
                "bids": [{"price": 1, "quantity": 2}, {"price": 1, "quantity": 3}],
                "asks": [{"price": 2, "size": 1}, {"price": 2, "volume": 1}],
            },
            "published_at": "2025-01-01T00:00:00+00:00",
        }
        point = indicator.on_event(event)
        self.assertIsNotNone(point)
        assert point is not None
        self.assertAlmostEqual(point.value, (5 - 2) / (5 + 2), places=9)


class ScriptValidationTests(unittest.TestCase):
    def test_forbidden_import_rejected(self) -> None:
        from src.indicator_runtime import ScriptValidationError, validate_and_instantiate

        src = (
            "import os\n"
            "class Bad(HubIndicator):\n"
            "    name = 'bad'\n"
            "    inputs = ('trade',)\n"
            "    def on_event(self, event):\n"
            "        return None\n"
        )
        with self.assertRaises(ScriptValidationError) as ctx:
            validate_and_instantiate(src, class_name="Bad")
        self.assertTrue(any("forbidden" in e for e in ctx.exception.errors))

    def test_valid_script_activates_and_produces_point(self) -> None:
        from src.indicator_runtime import validate_and_instantiate, SeriesPoint

        src = (
            "class Doubler(HubIndicator):\n"
            "    name = 'doubler'\n"
            "    inputs = ('trade',)\n"
            "    def on_event(self, event):\n"
            "        price = event['payload'].get('price') or 0\n"
            "        return SeriesPoint(\n"
            "            timestamp=event['published_at'],\n"
            "            value=float(price) * 2.0,\n"
            "            meta={},\n"
            "        )\n"
        )
        instance, cls_name = validate_and_instantiate(src, class_name="Doubler")
        self.assertEqual(cls_name, "Doubler")
        sample = {
            "event_type": "trade",
            "symbol": "T",
            "market_scope": "",
            "payload": {"price": 10},
            "published_at": "2025-01-01T00:00:00+00:00",
        }
        out = instance.on_event(sample)
        self.assertIsInstance(out, SeriesPoint)
        self.assertEqual(out.value, 20.0)

    def test_attribute_access_to_dunder_import_rejected(self) -> None:
        from src.indicator_runtime import ScriptValidationError, validate_and_instantiate

        src = (
            "class Sneaky(HubIndicator):\n"
            "    name = 's'\n"
            "    inputs = ('trade',)\n"
            "    def on_event(self, event):\n"
            "        x = __import__('os')\n"
            "        return None\n"
        )
        with self.assertRaises(ScriptValidationError):
            validate_and_instantiate(src, class_name="Sneaky")


class ManagerIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_faulty_instance_isolated_from_healthy_one(self) -> None:
        # Direct test of the dispatch isolation: one indicator always
        # raises, the other is OBI.  The manager should mark the raiser
        # as error without touching the healthy one.
        from packages.contracts.admin import (
            IndicatorInstanceSpec,
            IndicatorScriptSpec,
        )
        from src.indicator_runtime import (
            ChartsStateStore,
            IndicatorRuntimeManager,
            OrderBookImbalanceIndicator,
            _InstanceRuntime,
            _normalize_event,
        )

        store = ChartsStateStore(path=Path(tempfile.mkdtemp()) / "s.json")
        manager = IndicatorRuntimeManager(control_plane=_StubControlPlane(), state_store=store)

        class _AlwaysRaise(type(OrderBookImbalanceIndicator).__bases__[0]):  # HubIndicator
            name = "raiser"
            inputs = ("order_book_snapshot",)
            output_kind = "line"

            def on_event(self, event):  # noqa: D401
                raise RuntimeError("boom")

        healthy = _InstanceRuntime(
            spec=IndicatorInstanceSpec(instance_id="healthy", script_id="builtin.obi", symbol=""),
            indicator=OrderBookImbalanceIndicator(top_n=3),
        )
        broken = _InstanceRuntime(
            spec=IndicatorInstanceSpec(instance_id="broken", script_id="x", symbol=""),
            indicator=_AlwaysRaise(),
        )
        async with manager._lock:
            manager._instances = {"healthy": healthy, "broken": broken}

        captured: list[Any] = []

        async def _capture(env):  # type: ignore[no-untyped-def]
            captured.append(env)

        manager._fanout = _capture  # type: ignore[assignment]

        event = type("RE", (), {})()
        event.event_name = "order_book_snapshot"
        event.symbol = ""
        event.market_scope = ""
        event.payload = {
            "bids": [[1.0, 2.0]],
            "asks": [[1.1, 1.0]],
        }
        event.published_at = datetime.now(timezone.utc)
        await manager._dispatch(event)

        self.assertEqual(broken.state, "error")
        self.assertIn("boom", broken.last_error or "")
        self.assertEqual(healthy.state, "running")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].instance_id, "healthy")


class _StubControlPlane:
    """Stand-in for CollectorControlPlaneService in manager tests."""

    def subscribe_events(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _cm():
            yield asyncio.Queue()

        return _cm()


class RawPassthroughTests(unittest.TestCase):
    def test_raw_passthrough_emits_series_point_for_trade_price(self) -> None:
        from src.indicator_runtime import RawPassthroughIndicator

        ind = RawPassthroughIndicator(field="price")
        out = ind.on_event({
            "event_type": "trade",
            "symbol": "BTCUSDT",
            "market_scope": "",
            "payload": {"price": 12345.6, "size": 0.1, "occurred_at": "2026-04-21T12:00:00+00:00"},
            "published_at": "2026-04-21T12:00:00+00:00",
        })
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.value, 12345.6)
        self.assertEqual(out.timestamp, "2026-04-21T12:00:00+00:00")
        self.assertEqual(out.meta["field"], "price")

    def test_raw_passthrough_defaults_field_per_event_type(self) -> None:
        from src.indicator_runtime import RawPassthroughIndicator

        ind = RawPassthroughIndicator()  # no field configured
        out = ind.on_event({
            "event_type": "ohlcv",
            "symbol": "BTC",
            "market_scope": "",
            "payload": {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
            "published_at": "2026-04-21T12:00:00+00:00",
        })
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.value, 1.5)

    def test_raw_passthrough_supports_dotted_payload_path(self) -> None:
        from src.indicator_runtime import RawPassthroughIndicator

        ind = RawPassthroughIndicator(field="raw.info.lastPrice")
        out = ind.on_event({
            "event_type": "trade",
            "symbol": "BTC/USDT",
            "market_scope": "",
            "payload": {
                "normalized": {"price": "123.4"},
                "raw": {"price": "123.3", "info": {"lastPrice": "123.45"}},
            },
            "published_at": "2026-04-21T12:00:00+00:00",
        })
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.value, 123.45)
        self.assertEqual(out.meta["field"], "raw.info.lastPrice")

    def test_raw_passthrough_uses_dotted_time_field(self) -> None:
        from src.indicator_runtime import RawPassthroughIndicator

        ind = RawPassthroughIndicator(field="raw.info.lastPrice", time_field="raw.info.T")
        out = ind.on_event({
            "event_type": "trade",
            "symbol": "BTC/USDT",
            "market_scope": "",
            "payload": {"raw": {"info": {"lastPrice": "123.45", "T": 1777000000000}}},
            "published_at": "2026-04-21T12:00:00+00:00",
        })
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.timestamp, "1777000000000")
        self.assertEqual(out.meta["time_field"], "raw.info.T")


if __name__ == "__main__":
    unittest.main()
