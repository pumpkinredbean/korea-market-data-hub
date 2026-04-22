"""Contract tests for the indicator-first ChartPanelSpec model (step 36).

Asserts the new ``ChartSeriesBinding`` shape is in effect and the legacy
``source_kind`` / ``target_id`` / ``symbol`` / ``provider`` /
``event_name`` / ``field_name`` fields are gone.
"""

from __future__ import annotations

import dataclasses
import json
import unittest


class ChartPanelIndicatorFirstContractTests(unittest.TestCase):
    def test_series_bindings_default_empty_and_panel_defaults_large(self) -> None:
        from packages.contracts.admin import ChartPanelSpec

        spec = ChartPanelSpec(panel_id="p1", chart_type="line", symbol="005930")
        self.assertEqual(spec.series_bindings, ())
        self.assertIsNone(spec.base_feed)
        self.assertEqual(spec.scripts, ())
        self.assertEqual(spec.instances, ())
        # Larger first-run defaults.
        self.assertEqual(spec.w, 12)
        self.assertEqual(spec.h, 14)

    def test_panel_and_binding_are_frozen(self) -> None:
        from packages.contracts.admin import ChartPanelSpec, ChartSeriesBinding

        binding = ChartSeriesBinding(binding_id="b1", indicator_ref="builtin.raw")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            binding.axis = "right"  # type: ignore[misc]
        spec = ChartPanelSpec(panel_id="p1", chart_type="line")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            spec.symbol = "X"  # type: ignore[misc]

    def test_legacy_binding_fields_are_removed(self) -> None:
        from packages.contracts.admin import ChartSeriesBinding

        names = {f.name for f in dataclasses.fields(ChartSeriesBinding)}
        for legacy in {"source_kind", "target_id", "symbol", "provider", "event_name", "field_name"}:
            self.assertNotIn(legacy, names, f"deprecated field still present: {legacy}")
        # New canonical fields are present.
        for required in {"indicator_ref", "instance_id", "input_bindings", "param_values", "output_name"}:
            self.assertIn(required, names)

    def test_legacy_panel_fields_are_removed(self) -> None:
        from packages.contracts.admin import ChartPanelSpec

        names = {f.name for f in dataclasses.fields(ChartPanelSpec)}
        for legacy in {"source", "series_ref"}:
            self.assertNotIn(legacy, names)
        for required in {"base_feed", "scripts", "instances"}:
            self.assertIn(required, names)

    def test_input_bindings_param_values_output_name_round_trip(self) -> None:
        from packages.contracts.admin import (
            ChartInputSlot,
            ChartPanelBaseFeed,
            ChartPanelSpec,
            ChartSeriesBinding,
        )

        spec = ChartPanelSpec(
            panel_id="p1",
            chart_type="candle",
            symbol="BTCUSDT",
            base_feed=ChartPanelBaseFeed(target_id="t-btc", event_name="ohlcv"),
            series_bindings=(
                ChartSeriesBinding(
                    binding_id="b1",
                    indicator_ref="builtin.raw",
                    input_bindings=(
                        ChartInputSlot(slot_name="source", target_id="t-btc",
                                       event_name="trade", field_name="price"),
                    ),
                    param_values=(("field", "price"),),
                    output_name="value",
                    axis="right",
                    color="#ffb000",
                    label="trade.price",
                ),
            ),
        )
        roundtripped = json.loads(json.dumps(dataclasses.asdict(spec)))
        self.assertEqual(roundtripped["base_feed"]["target_id"], "t-btc")
        b = roundtripped["series_bindings"][0]
        self.assertEqual(b["indicator_ref"], "builtin.raw")
        self.assertEqual(b["output_name"], "value")
        self.assertEqual(b["input_bindings"][0]["slot_name"], "source")
        self.assertEqual(b["input_bindings"][0]["field_name"], "price")
        # param_values serialise as list-of-pairs.
        self.assertEqual(b["param_values"][0][0], "field")
        self.assertEqual(b["param_values"][0][1], "price")


if __name__ == "__main__":
    unittest.main()
