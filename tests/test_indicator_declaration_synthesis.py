"""Synthesis of IndicatorDeclaration for custom user-uploaded scripts (H36)."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path


class _FakeIndicatorWithInputs:
    """Minimum viable runtime indicator stand-in (class attr inputs)."""

    inputs = ("trade",)

    def update(self, event):  # pragma: no cover - shape only
        return 0.0


class _FakeIndicatorNoInputs:
    """Custom indicator missing the optional ``inputs`` class attr."""

    def update(self, event):  # pragma: no cover - shape only
        return 0.0


class SynthesizeIndicatorDeclarationTests(unittest.TestCase):
    def test_synthesis_uses_class_inputs_tuple(self) -> None:
        from src.indicator_runtime import synthesize_indicator_declaration

        decl = synthesize_indicator_declaration(
            _FakeIndicatorWithInputs(), class_name="Foo"
        )
        self.assertEqual(len(decl.inputs), 1)
        slot = decl.inputs[0]
        self.assertEqual(slot.slot_name, "primary")
        self.assertEqual(slot.event_names, ("trade",))
        self.assertTrue(slot.required)
        self.assertEqual(decl.params, ())
        self.assertEqual(len(decl.outputs), 1)
        out = decl.outputs[0]
        self.assertEqual(out.name, "value")
        self.assertEqual(out.kind, "line")
        self.assertEqual(out.label, "Foo")
        self.assertTrue(out.is_primary)

    def test_synthesis_defaults_to_trade_when_inputs_missing(self) -> None:
        from src.indicator_runtime import synthesize_indicator_declaration

        decl = synthesize_indicator_declaration(_FakeIndicatorNoInputs())
        self.assertEqual(len(decl.inputs), 1)
        self.assertEqual(decl.inputs[0].event_names, ("trade",))
        self.assertEqual(decl.inputs[0].slot_name, "primary")

    def test_end_to_end_round_trip_through_charts_state_store(self) -> None:
        from packages.contracts.admin import IndicatorScriptSpec
        from src.indicator_runtime import (
            ChartsStateStore,
            synthesize_indicator_declaration,
            validate_and_instantiate,
        )

        source = (
            "class MyInd(HubIndicator):\n"
            "    name = 'my'\n"
            "    inputs = ('trade',)\n"
            "    def on_event(self, event):\n"
            "        return None\n"
        )
        instance, resolved = validate_and_instantiate(source, class_name="MyInd")
        decl = synthesize_indicator_declaration(instance, class_name=resolved)
        spec = IndicatorScriptSpec(
            script_id="custom-1",
            name="custom 1",
            source=source,
            class_name=resolved,
            builtin=False,
            declaration=decl,
        )

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "admin_charts.json"
                store = ChartsStateStore(path=path)
                await store.upsert_script(spec)

                store2 = ChartsStateStore(path=path)
                store2.load()
                scripts = {s.script_id: s for s in await store2.list_scripts()}
                self.assertIn("custom-1", scripts)
                reloaded = scripts["custom-1"]
                self.assertIsNotNone(reloaded.declaration)
                self.assertGreaterEqual(len(reloaded.declaration.inputs), 1)
                primaries = [
                    o for o in reloaded.declaration.outputs if o.is_primary
                ]
                self.assertEqual(len(primaries), 1)

        asyncio.run(_run())

    def test_dict_declaration_rehydrates_through_script_spec_from_entry(self) -> None:
        from packages.contracts.admin import IndicatorDeclaration
        from src.indicator_runtime import (
            _script_spec_from_entry,
            synthesize_indicator_declaration,
        )

        decl = synthesize_indicator_declaration(
            _FakeIndicatorWithInputs(), class_name="Foo"
        )
        decl_dict = asdict(decl)
        entry = {
            "script_id": "rt-1",
            "name": "rt 1",
            "source": "class X(HubIndicator):\n    pass\n",
            "class_name": "X",
            "builtin": False,
            "description": None,
            "declaration": decl_dict,
        }
        spec = _script_spec_from_entry(entry)
        self.assertIsNotNone(spec)
        self.assertIsInstance(spec.declaration, IndicatorDeclaration)
        self.assertEqual(len(spec.declaration.inputs), 1)
        self.assertEqual(spec.declaration.inputs[0].slot_name, "primary")
        self.assertEqual(spec.declaration.inputs[0].event_names, ("trade",))
        primaries = [o for o in spec.declaration.outputs if o.is_primary]
        self.assertEqual(len(primaries), 1)
        self.assertEqual(primaries[0].name, "value")


if __name__ == "__main__":
    unittest.main()
