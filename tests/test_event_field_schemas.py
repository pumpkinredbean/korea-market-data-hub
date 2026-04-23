"""Tests for the canonical event-field schema (drives admin charts inspector)."""
from __future__ import annotations

import unittest


class CanonicalEventSchemaTests(unittest.TestCase):
    def test_module_exports_expected_helpers(self) -> None:
        from packages.contracts.event_schemas import (
            CANONICAL_EVENT_FIELDS,
            canonical_event_field_schema,
            canonical_fields_for_event,
        )

        self.assertIsInstance(CANONICAL_EVENT_FIELDS, dict)
        self.assertTrue(callable(canonical_event_field_schema))
        self.assertTrue(callable(canonical_fields_for_event))

    def test_every_event_type_has_a_schema(self) -> None:
        from packages.contracts.event_schemas import CANONICAL_EVENT_FIELDS
        from packages.contracts.events import EventType

        for et in EventType:
            self.assertIn(
                et.value, CANONICAL_EVENT_FIELDS,
                f"missing canonical field schema for {et.value}",
            )

    def test_core_event_field_contents(self) -> None:
        from packages.contracts.event_schemas import canonical_fields_for_event

        # Trade
        trade = canonical_fields_for_event("trade")
        self.assertIn("price", trade)
        self.assertIn("quantity", trade)
        # OHLCV
        ohlcv = canonical_fields_for_event("ohlcv")
        for f in ("open", "high", "low", "close", "volume"):
            self.assertIn(f, ohlcv)
        # Funding rate must surface 'rate' (the runtime field) for the
        # raw passthrough flow.
        self.assertIn("rate", canonical_fields_for_event("funding_rate"))
        # Mark price
        self.assertIn("mark_price", canonical_fields_for_event("mark_price"))
        # Unknown event yields empty tuple, never throws.
        self.assertEqual(canonical_fields_for_event(""), ())
        self.assertEqual(canonical_fields_for_event("does-not-exist"), ())

    def test_jsonable_snapshot_is_dict_of_lists(self) -> None:
        from packages.contracts.event_schemas import canonical_event_field_schema

        snap = canonical_event_field_schema()
        self.assertIsInstance(snap, dict)
        for k, v in snap.items():
            self.assertIsInstance(k, str)
            self.assertIsInstance(v, list)
            for entry in v:
                self.assertIsInstance(entry, str)


class EventSchemasEndpointTests(unittest.TestCase):
    def test_collector_endpoint_returns_canonical_schema(self) -> None:
        from fastapi.testclient import TestClient

        from apps.collector.service import app
        from packages.contracts.event_schemas import canonical_event_field_schema

        with TestClient(app) as client:
            resp = client.get("/admin/charts/event-schemas")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertIn("schemas", body)
            self.assertEqual(body["schemas"], canonical_event_field_schema())


if __name__ == "__main__":
    unittest.main()
