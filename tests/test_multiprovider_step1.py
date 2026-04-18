"""Smoke tests for step 1 multiprovider plumbing.

Verifies that:

1. :mod:`packages.domain.enums` exposes ``Provider`` and the new
   ``InstrumentType.SPOT`` / ``InstrumentType.PERPETUAL`` members.
2. :func:`packages.domain.models.build_canonical_symbol` produces the
   documented multiprovider identity shape.
3. The provider registry wires KXT + CCXT + CCXT Pro entries.
4. The control-plane defaults KRX targets to ``Provider.KXT`` and
   populates a canonical_symbol on both the instrument and the target.
5. The runtime rejects non-KXT providers with ``NotImplementedError``
   (step 1 scope: KXT path only is wired end-to-end).
"""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any


class ProviderEnumTests(unittest.TestCase):
    def test_provider_enum_has_expected_members(self) -> None:
        from packages.domain.enums import Provider

        values = {member.value for member in Provider}
        self.assertEqual(values, {"kxt", "ccxt", "ccxt_pro", "other"})

    def test_instrument_type_has_spot_and_perpetual(self) -> None:
        from packages.domain.enums import InstrumentType

        values = {member.value for member in InstrumentType}
        self.assertIn("spot", values)
        self.assertIn("perpetual", values)
        self.assertIn("future", values)
        self.assertIn("option", values)
        # Legacy labels still present (backwards compatibility).
        self.assertIn("equity", values)


class CanonicalSymbolTests(unittest.TestCase):
    def test_build_canonical_symbol_kxt_equity(self) -> None:
        from packages.domain.enums import InstrumentType, Provider, Venue
        from packages.domain.models import build_canonical_symbol

        canonical = build_canonical_symbol(
            provider=Provider.KXT,
            venue=Venue.KRX,
            instrument_type=InstrumentType.EQUITY,
            symbol="005930",
        )
        self.assertEqual(canonical, "kxt:krx:equity:005930")

    def test_build_canonical_symbol_crypto_perpetual(self) -> None:
        from packages.domain.enums import InstrumentType, Provider, Venue
        from packages.domain.models import build_canonical_symbol

        canonical = build_canonical_symbol(
            provider=Provider.CCXT_PRO,
            venue=Venue.BINANCE,
            instrument_type=InstrumentType.PERPETUAL,
            symbol="BTCUSDT",
        )
        self.assertEqual(canonical, "ccxt_pro:binance:perpetual:BTCUSDT")

    def test_build_canonical_symbol_handles_missing_axes(self) -> None:
        from packages.domain.models import build_canonical_symbol

        canonical = build_canonical_symbol(
            provider=None,
            venue=None,
            instrument_type=None,
            symbol="SYM",
        )
        self.assertEqual(canonical, "unknown:unknown:unknown:SYM")


class ProviderRegistryTests(unittest.TestCase):
    def test_default_registry_has_kxt_ccxt_ccxt_pro(self) -> None:
        from packages.adapters import build_default_registry
        from packages.domain.enums import Provider

        registry = build_default_registry()
        providers = set(registry.providers())
        self.assertEqual(providers, {Provider.KXT, Provider.CCXT, Provider.CCXT_PRO})

    def test_registry_factories_build_stubs(self) -> None:
        from packages.adapters import build_default_registry
        from packages.domain.enums import Provider

        registry = build_default_registry()
        kxt = registry.require(Provider.KXT).factory()
        ccxt = registry.require(Provider.CCXT).factory()
        ccxt_pro = registry.require(Provider.CCXT_PRO).factory()
        self.assertEqual(kxt.adapter_id, "kxt")
        self.assertEqual(ccxt.adapter_id, "ccxt")
        self.assertEqual(ccxt_pro.adapter_id, "ccxt_pro")
        # Step 2: KXT and CCXT/CCXT Pro are now wired (CCXT Pro provides the
        # live BinanceLiveAdapter; CCXT REST descriptor stays for the axis).
        self.assertTrue(kxt.implemented)
        self.assertTrue(ccxt.implemented)
        self.assertTrue(ccxt_pro.implemented)


class ControlPlaneProviderWiringTests(unittest.IsolatedAsyncioTestCase):
    async def _make_service(self):
        from src.collector_control_plane import CollectorControlPlaneService

        async def _noop_start(**_: Any) -> dict[str, object]:
            return {}

        async def _noop_stop(**_: Any) -> dict[str, object]:
            return {}

        return CollectorControlPlaneService(
            service_name="test",
            default_symbol="005930",
            default_market_scope="krx",
            start_publication=_noop_start,
            stop_publication=_noop_stop,
            is_publication_active=lambda _owner_id: False,
        )

    async def test_upsert_target_defaults_to_kxt_and_populates_canonical_symbol(self) -> None:
        from packages.domain.enums import Provider

        svc = await self._make_service()
        result = await svc.upsert_target(
            target_id=None,
            symbol="005930",
            market_scope="krx",
            event_types=["trade"],
            enabled=False,
        )
        target = result["target"]
        # provider field defaults to KXT and canonical_symbol is populated.
        self.assertEqual(target.provider, Provider.KXT)
        self.assertEqual(target.canonical_symbol, "kxt:krx:equity:005930")
        self.assertEqual(target.instrument.provider, Provider.KXT)
        self.assertEqual(target.instrument.canonical_symbol, "kxt:krx:equity:005930")

    async def test_upsert_target_accepts_non_krx_provider_with_empty_scope(self) -> None:
        from packages.domain.enums import Provider

        svc = await self._make_service()
        result = await svc.upsert_target(
            target_id=None,
            symbol="BTCUSDT",
            market_scope="",  # not applicable for crypto providers
            event_types=["trade"],
            enabled=False,
            provider="ccxt_pro",
            instrument_type="perpetual",
        )
        target = result["target"]
        self.assertEqual(target.provider, Provider.CCXT_PRO)
        self.assertEqual(target.market_scope, "")
        self.assertEqual(target.canonical_symbol, "ccxt_pro:binance:perpetual:BTCUSDT")


class RuntimeProviderBranchTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_rejects_unknown_provider(self) -> None:
        from apps.collector.runtime import CollectorRuntime

        runtime = CollectorRuntime(SimpleNamespace(app_key="k", app_secret="s"))
        try:
            async def _noop_wait(**_):
                return None

            runtime._wait_session_ready = _noop_wait  # type: ignore[assignment]

            with self.assertRaises(NotImplementedError):
                await runtime.register_target(
                    owner_id="t-other",
                    symbol="BTCUSDT",
                    market_scope="",
                    event_types=("trade",),
                    provider="other",
                )
        finally:
            await runtime.aclose()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
