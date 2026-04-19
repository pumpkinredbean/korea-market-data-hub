"""Tests for CollectorControlPlaneService instrument search behavior.

Specifically verifies that arbitrary 6-digit KRX stock codes (like 034020)
are returned as selectable results even when not in the bootstrap catalog.
"""
from __future__ import annotations

import asyncio
import unittest
from typing import Any


async def _noop_start(**_: Any) -> dict[str, object]:
    return {}


async def _noop_stop(**_: Any) -> dict[str, object]:
    return {}


def _noop_is_active(_: str) -> bool:
    return False


def _make_service(default_symbol: str = "005930") -> "CollectorControlPlaneService":  # noqa: F821
    from src.collector_control_plane import CollectorControlPlaneService

    return CollectorControlPlaneService(
        service_name="test",
        default_symbol=default_symbol,
        default_market_scope="krx",
        start_publication=_noop_start,
        stop_publication=_noop_stop,
        is_publication_active=_noop_is_active,
    )


class InstrumentSearchDirectSymbolTests(unittest.IsolatedAsyncioTestCase):
    """034020 (한국전력) is not in BOOTSTRAP_INSTRUMENTS but must be findable."""

    async def test_direct_6digit_symbol_returns_result(self) -> None:
        svc = _make_service()
        results = await svc.search_instruments(query="034020", market_scope="krx")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].instrument.symbol, "034020")

    async def test_direct_6digit_symbol_is_first_result(self) -> None:
        """Even if partial matches exist, exact 6-digit hit comes first."""
        svc = _make_service()
        results = await svc.search_instruments(query="034020", market_scope="krx")
        self.assertEqual(results[0].instrument.symbol, "034020")

    async def test_bootstrap_symbol_still_found(self) -> None:
        svc = _make_service()
        results = await svc.search_instruments(query="005930", market_scope="krx")
        symbols = [r.instrument.symbol for r in results]
        self.assertIn("005930", symbols)

    async def test_name_based_search_still_works(self) -> None:
        svc = _make_service()
        results = await svc.search_instruments(query="삼성전자", market_scope="krx")
        symbols = [r.instrument.symbol for r in results]
        self.assertIn("005930", symbols)

    async def test_non_6digit_query_not_injected(self) -> None:
        """A short or non-numeric query must not create a phantom entry."""
        svc = _make_service()
        results = await svc.search_instruments(query="abc", market_scope="krx")
        symbols = [r.instrument.symbol for r in results]
        self.assertNotIn("abc", symbols)

    async def test_already_bootstrap_symbol_not_duplicated(self) -> None:
        """If a 6-digit query is already in bootstrap, no duplicate is added."""
        svc = _make_service()
        results = await svc.search_instruments(query="005930", market_scope="krx")
        symbols = [r.instrument.symbol for r in results]
        self.assertEqual(symbols.count("005930"), 1)

    async def test_target_symbol_search_still_works(self) -> None:
        """Symbols added via upsert_target should also appear in search."""
        svc = _make_service()
        await svc.upsert_target(
            target_id=None,
            symbol="247540",
            market_scope="krx",
            event_types=["trade"],
            enabled=False,
        )
        results = await svc.search_instruments(query="247540", market_scope="krx")
        symbols = [r.instrument.symbol for r in results]
        self.assertIn("247540", symbols)


if __name__ == "__main__":
    unittest.main()
