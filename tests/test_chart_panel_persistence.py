"""Persistence round-trip + legacy migration tests for admin-charts state."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class ChartsStateRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_with_new_shape_round_trips_through_snapshot(self) -> None:
        from packages.contracts.admin import (
            ChartInputSlot,
            ChartPanelBaseFeed,
            ChartPanelSpec,
            ChartSeriesBinding,
            IndicatorScriptSpec,
        )
        from src.indicator_runtime import ChartsStateStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "admin_charts.json"
            store = ChartsStateStore(path=path)

            panel_script = IndicatorScriptSpec(
                script_id="panel-s1",
                name="panel ind",
                source=(
                    "class P(HubIndicator):\n"
                    "    name='p'\n"
                    "    inputs=()\n"
                    "    def on_event(self, event): return None\n"
                ),
                class_name="P",
                builtin=False,
            )
            panel = ChartPanelSpec(
                panel_id="p-multi",
                chart_type="candle",
                symbol="BTCUSDT",
                w=12,
                h=14,
                title="cn",
                base_feed=ChartPanelBaseFeed(target_id="t-btc", event_name="ohlcv"),
                series_bindings=(
                    ChartSeriesBinding(
                        binding_id="b-mark",
                        indicator_ref="builtin.raw",
                        input_bindings=(
                            ChartInputSlot(slot_name="source", target_id="t-btc",
                                           event_name="mark_price", field_name="value"),
                        ),
                        param_values=(("field", "value"),),
                        output_name="value",
                        axis="right",
                        label="mark",
                    ),
                ),
                scripts=(panel_script,),
            )
            await store.upsert_panel(panel)

            store2 = ChartsStateStore(path=path)
            store2.load()
            panels = await store2.list_panels()
            self.assertEqual(len(panels), 1)
            reloaded = panels[0]
            self.assertEqual(reloaded.panel_id, "p-multi")
            self.assertIsNotNone(reloaded.base_feed)
            self.assertEqual(reloaded.base_feed.target_id, "t-btc")  # type: ignore[union-attr]
            self.assertEqual(len(reloaded.series_bindings), 1)
            b = reloaded.series_bindings[0]
            self.assertEqual(b.indicator_ref, "builtin.raw")
            self.assertEqual(b.output_name, "value")
            self.assertEqual(b.input_bindings[0].slot_name, "source")
            self.assertEqual(b.input_bindings[0].field_name, "value")
            self.assertEqual(dict(b.param_values), {"field": "value"})
            # Panel-scoped script propagated into the global script registry on upsert.
            scripts = {s.script_id: s for s in await store2.list_scripts()}
            self.assertIn("panel-s1", scripts)

    async def test_legacy_source_kind_raw_is_migrated_to_builtin_raw(self) -> None:
        """A pre-step36 snapshot with ``source_kind`` is migrated on load."""
        from src.indicator_runtime import ChartsStateStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "admin_charts.json"
            snapshot = {
                "panels": [
                    {
                        "panel_id": "legacy",
                        "chart_type": "line",
                        "symbol": "005930",
                        "x": 0, "y": 0, "w": 8, "h": 10,
                        "title": "legacy line",
                        "notes": None,
                        "series_bindings": [
                            {
                                "binding_id": "b-old",
                                "source_kind": "raw",
                                "target_id": "tgt-1",
                                "symbol": "005930",
                                "event_name": "trade",
                                "field_name": "price",
                                "axis": "left",
                                "color": "",
                                "label": "trade",
                                "visible": True,
                            }
                        ],
                    }
                ],
                "scripts": [],
                "instances": [],
                "schema_version": "v1",
            }
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            store = ChartsStateStore(path=path)
            store.load()
            panels = await store.list_panels()
            self.assertEqual(len(panels), 1)
            b = panels[0].series_bindings[0]
            self.assertEqual(b.indicator_ref, "builtin.raw")
            self.assertEqual(b.input_bindings[0].slot_name, "source")
            self.assertEqual(b.input_bindings[0].target_id, "tgt-1")
            self.assertEqual(b.input_bindings[0].event_name, "trade")
            self.assertEqual(b.input_bindings[0].field_name, "price")
            self.assertEqual(dict(b.param_values), {"field": "price"})

    async def test_legacy_source_kind_builtin_is_migrated(self) -> None:
        from src.indicator_runtime import ChartsStateStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "admin_charts.json"
            snapshot = {
                "panels": [
                    {
                        "panel_id": "legacy-obi",
                        "chart_type": "line",
                        "symbol": "BTCUSDT",
                        "x": 0, "y": 0, "w": 6, "h": 6,
                        "series_bindings": [
                            {
                                "binding_id": "b-obi",
                                "source_kind": "builtin",
                                "target_id": "builtin.obi",
                                "output_name": "obi",
                                "axis": "left",
                                "label": "OBI",
                                "visible": True,
                            }
                        ],
                    }
                ],
                "scripts": [], "instances": [], "schema_version": "v1",
            }
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            store = ChartsStateStore(path=path)
            store.load()
            panels = await store.list_panels()
            b = panels[0].series_bindings[0]
            self.assertEqual(b.indicator_ref, "builtin.obi")
            self.assertEqual(b.output_name, "obi")

    async def test_delete_cascade_drops_instances_referencing_script(self) -> None:
        from packages.contracts.admin import IndicatorInstanceSpec, IndicatorScriptSpec
        from src.indicator_runtime import ChartsStateStore

        with tempfile.TemporaryDirectory() as tmp:
            store = ChartsStateStore(path=Path(tmp) / "s.json")
            await store.upsert_script(IndicatorScriptSpec(
                script_id="s1", name="n",
                source="class X(HubIndicator):\n    name='x'\n    inputs=()\n    def on_event(self, event): return None\n",
                class_name="X",
            ))
            await store.upsert_instance(IndicatorInstanceSpec(instance_id="i1", script_id="s1", symbol="T"))
            self.assertTrue(await store.delete_script("s1"))
            self.assertEqual(await store.list_instances(), [])

    async def test_builtin_script_cannot_be_deleted(self) -> None:
        from src.indicator_runtime import ChartsStateStore

        with tempfile.TemporaryDirectory() as tmp:
            store = ChartsStateStore(path=Path(tmp) / "s.json")
            self.assertFalse(await store.delete_script("builtin.obi"))
            self.assertFalse(await store.delete_script("builtin.raw"))


if __name__ == "__main__":
    unittest.main()
