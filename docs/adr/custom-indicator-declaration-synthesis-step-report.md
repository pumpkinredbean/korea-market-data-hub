# Custom Indicator Declaration Synthesis — Step Report

> Repo-local mirror. There is no external vault path for this report; this
> document is the authoritative record alongside ADR H36 in
> `docs/adr/decisions.md`.

## Summary

Built-in indicators (`builtin.raw`, `builtin.obi`) shipped with hand-authored
`IndicatorDeclaration` values driving the admin inspector form, but custom
user-uploaded Python scripts were persisted with `declaration=None`, so the
inspector silently degraded to a blank state for them. This step adds
`synthesize_indicator_declaration(instance, *, class_name=None)` and wires
both upsert sites in `apps/collector/service.py` so every custom script gets
a minimum-viable declaration on first save. JSON round-trip is also closed:
`_script_spec_from_entry` now rehydrates a dict-shaped `declaration`, and
`ChartsStateStore.load()` routes script entries through that helper.

## What Changed

### src/indicator_runtime.py

- New public helper `synthesize_indicator_declaration(indicator, *, class_name=None)`:
  reads `indicator.inputs` (defaulting to `("trade",)` when missing or empty)
  and returns an `IndicatorDeclaration` with one `IndicatorInputDecl(slot_name="primary",
  event_names=..., required=True)`, no params, and one
  `IndicatorOutputDecl(name="value", kind="line", label=class_name or "value", is_primary=True)`.
- New private helper `_declaration_from_entry(entry)` rebuilds the nested
  `IndicatorInputDecl` / `IndicatorParamDecl` / `IndicatorOutputDecl`
  dataclasses from a dict, returning `None` (with a logged warning) on
  parse error.
- `_script_spec_from_entry` now accepts dict-shaped `declaration` payloads
  via `_declaration_from_entry`, preserving existing behavior when the
  declaration is already an `IndicatorDeclaration` instance.
- `ChartsStateStore.load()` now routes the `scripts` list through
  `_script_spec_from_entry`, so reloaded custom scripts retain their
  declarations rather than ending up with raw dicts.

### apps/collector/service.py

- Imports `synthesize_indicator_declaration` from `src.indicator_runtime`.
- `admin_charts_upsert_script` PUT handler now keeps the validated
  `instance` (was discarded as `_`) and constructs the persisted
  `IndicatorScriptSpec` with `declaration=synthesize_indicator_declaration(instance, class_name=resolved_class)`.
- The panel-scoped inline scripts parser does the same, attaching a
  synthesized declaration to each panel-local custom script.

### tests/

- New file `tests/test_indicator_declaration_synthesis.py` (4 tests):
  1. Synthesis from a fake indicator with `inputs = ("trade",)` produces
     the expected single primary input slot, empty params, and a single
     primary `line` output.
  2. Synthesis defaults to `("trade",)` when the indicator has no `inputs`
     class attribute.
  3. End-to-end round trip: a custom script validated by
     `validate_and_instantiate`, packaged with a synthesized declaration,
     persisted via `ChartsStateStore.upsert_script`, and reloaded via a
     fresh store + `load()`, retains a non-None `declaration` with ≥1
     input and exactly one primary output.
  4. JSON rehydration: a declaration converted via `dataclasses.asdict`
     and fed back through `_script_spec_from_entry` returns a real
     `IndicatorDeclaration` instance with the original slot/output shape.

### docs/adr/

- Appended ADR H36 to `docs/adr/decisions.md` and added this step report.

## Verification

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
103 passed, 58 warnings in 1.96s
```

```
$ docker compose config > /dev/null && echo COMPOSE_OK
COMPOSE_OK
```

## Files Changed

- `src/indicator_runtime.py`
- `apps/collector/service.py`
- `tests/test_indicator_declaration_synthesis.py`
- `docs/adr/decisions.md`
- `docs/adr/custom-indicator-declaration-synthesis-step-report.md`

## Notes

- **Numbering gap**: ADR identifiers H33–H35 are reserved for entries
  tracked only in the external vault. This repo log skips from H32 to H36
  by parent direction; the gap is intentional and acceptable.
- **Minimum-viable synthesis**: the synthesized declaration intentionally
  carries one input slot (`primary`), no params, and one primary `line`
  output (`value`). It exists to make the inspector form render at all
  for user scripts. Authors who need richer parameter UIs can still
  attach a hand-authored `IndicatorDeclaration` directly on
  `IndicatorScriptSpec` construction; synthesis only fills in when none
  is provided.
