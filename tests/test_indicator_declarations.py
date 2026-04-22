"""Indicator declaration coverage tests (step 36)."""
from __future__ import annotations

import unittest


class IndicatorDeclarationTests(unittest.TestCase):
    def test_builtin_scripts_have_declarations(self) -> None:
        from src.indicator_runtime import BUILTIN_SCRIPTS

        by_id = {s.script_id: s for s in BUILTIN_SCRIPTS}
        self.assertIn("builtin.raw", by_id)
        self.assertIn("builtin.obi", by_id)
        for script in BUILTIN_SCRIPTS:
            self.assertIsNotNone(script.declaration, f"{script.script_id} missing declaration")
            decl = script.declaration
            self.assertGreaterEqual(len(decl.inputs), 1)
            self.assertGreaterEqual(len(decl.outputs), 1)
            primaries = [o for o in decl.outputs if o.is_primary]
            self.assertEqual(len(primaries), 1, f"{script.script_id} must have exactly one primary output")

    def test_raw_passthrough_field_param_is_enum(self) -> None:
        from src.indicator_runtime import BUILTIN_SCRIPTS

        raw = next(s for s in BUILTIN_SCRIPTS if s.script_id == "builtin.raw")
        decl = raw.declaration
        assert decl is not None
        field_params = [p for p in decl.params if p.name == "field"]
        self.assertEqual(len(field_params), 1)
        self.assertEqual(field_params[0].kind, "enum")
        self.assertIn("close", field_params[0].choices)
        self.assertIn("price", field_params[0].choices)
        # Single source slot accepting the four raw event types.
        slot = decl.inputs[0]
        self.assertEqual(slot.slot_name, "source")
        for ev in ("ohlcv", "trade", "mark_price", "funding_rate"):
            self.assertIn(ev, slot.event_names)

    def test_obi_declaration_matches_top_n_param(self) -> None:
        from src.indicator_runtime import BUILTIN_SCRIPTS

        obi = next(s for s in BUILTIN_SCRIPTS if s.script_id == "builtin.obi")
        decl = obi.declaration
        assert decl is not None
        self.assertEqual(decl.inputs[0].event_names, ("order_book_snapshot",))
        param_names = {p.name for p in decl.params}
        self.assertIn("top_n", param_names)
        self.assertEqual(decl.outputs[0].name, "obi")


if __name__ == "__main__":
    unittest.main()
