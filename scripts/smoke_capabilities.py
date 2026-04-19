"""Live/near-live smoke for expanded capabilities.

Attempts short real ccxt.pro sessions against Binance public WS for
spot ticker + perpetual mark_price + perpetual ticker.  Falls back to a
mock-driven hub traversal (adapter→runtime→publisher→control-plane
recent-events buffer) when the network path is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace


async def _try_live() -> list[dict]:
    from packages.adapters.ccxt import BinanceLiveAdapter

    adapter = BinanceLiveAdapter()
    captured: list[dict] = []

    async def _consume_one(gen, label):
        async for ev in gen:
            captured.append({
                "label": label,
                "class": type(ev).__name__,
                "snippet": str(ev)[:300],
            })
            return

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _consume_one(
                    adapter.stream_tickers(symbol="BTCUSDT", instrument_type="spot"),
                    "spot_ticker",
                ),
                _consume_one(
                    adapter.stream_mark_price(symbol="BTCUSDT", instrument_type="perpetual"),
                    "perp_mark_price",
                ),
                _consume_one(
                    adapter.stream_tickers(symbol="BTCUSDT", instrument_type="perpetual"),
                    "perp_ticker",
                ),
            ),
            timeout=20.0,
        )
    except Exception as exc:
        captured.append({"label": "live_error", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        try:
            await adapter.aclose()
        except Exception:
            pass
    return captured


async def _mock_hub_traversal() -> list[dict]:
    """Drive the full hub path with mocked adapter events and assert
    control-plane recent-events buffer ingested them end-to-end."""
    from apps.collector.runtime import CollectorRuntime
    from packages.adapters.ccxt import BinanceMarkPrice, BinanceTicker
    from src.collector_control_plane import CollectorControlPlaneService

    captured: list[dict] = []

    # Build control plane + runtime wired together.
    async def _start_publication(**_):
        return {}

    async def _stop_publication(**_):
        return {}

    active_ids: set[str] = set()

    control_plane = CollectorControlPlaneService(
        service_name="smoke",
        default_symbol="005930",
        default_market_scope="krx",
        start_publication=_start_publication,
        stop_publication=_stop_publication,
        is_publication_active=lambda owner_id: owner_id in active_ids,
    )

    async def on_event(**kwargs):
        # Forward to control-plane recent-events buffer end-to-end.
        await control_plane.record_runtime_event(
            symbol=kwargs.get("symbol"),
            market_scope=kwargs.get("market_scope") or "",
            event_name=kwargs.get("event_name"),
            payload=kwargs.get("payload") or {},
            provider=kwargs.get("provider"),
            canonical_symbol=kwargs.get("canonical_symbol"),
            instrument_type=kwargs.get("instrument_type"),
            raw_symbol=kwargs.get("raw_symbol"),
        )

    runtime = CollectorRuntime(
        SimpleNamespace(app_key="k", app_secret="s"),
        on_event=on_event,
    )

    async def _noop_wait(**_):
        return None

    runtime._wait_session_ready = _noop_wait  # type: ignore[assignment]

    # Upsert targets into control plane so matches happen.
    await control_plane.upsert_target(
        target_id="tk-spot",
        symbol="BTC/USDT",
        market_scope="",
        event_types=["ticker"],
        enabled=True,
        provider="ccxt",
        instrument_type="spot",
        raw_symbol="BTCUSDT",
    )
    await control_plane.upsert_target(
        target_id="mp-perp",
        symbol="BTC/USDT",
        market_scope="",
        event_types=["mark_price"],
        enabled=True,
        provider="ccxt",
        instrument_type="perpetual",
        raw_symbol="BTCUSDT",
    )

    # Fake adapter yielding exactly the two capability events we need.
    tick = BinanceTicker(
        symbol="BTC/USDT",
        instrument_type="spot",
        occurred_at=datetime.now(timezone.utc),
        last=Decimal("100000.5"),
        bid=Decimal("99999.5"),
        ask=Decimal("100001.5"),
        bid_size=Decimal("1"),
        ask_size=Decimal("2"),
        high=Decimal("101000"),
        low=Decimal("99000"),
        base_volume=Decimal("12"),
        quote_volume=Decimal("1200000"),
    )
    mark = BinanceMarkPrice(
        symbol="BTC/USDT:USDT",
        instrument_type="perpetual",
        occurred_at=datetime.now(timezone.utc),
        mark_price=Decimal("100123.45"),
        index_price=Decimal("100100.10"),
    )

    class _Adapter:
        adapter_id = "fake"

        async def stream_trades(self, **_):
            await asyncio.sleep(10)
            if False:
                yield

        async def stream_order_book_snapshots(self, **_):
            await asyncio.sleep(10)
            if False:
                yield

        async def stream_tickers(self, **_):
            yield tick
            await asyncio.sleep(10)

        async def stream_ohlcv(self, **_):
            await asyncio.sleep(10)
            if False:
                yield

        async def stream_mark_price(self, **_):
            yield mark
            await asyncio.sleep(10)

        async def poll_funding_rate(self, **_):
            await asyncio.sleep(10)
            if False:
                yield

        async def poll_open_interest(self, **_):
            await asyncio.sleep(10)
            if False:
                yield

        async def aclose(self):
            return None

    runtime._crypto_adapter = _Adapter()  # type: ignore[assignment]

    try:
        await runtime.register_target(
            owner_id="tk-spot",
            symbol="BTCUSDT",
            market_scope="",
            event_types=("ticker",),
            provider="ccxt",
            instrument_type="spot",
        )
        await runtime.register_target(
            owner_id="mp-perp",
            symbol="BTCUSDT",
            market_scope="",
            event_types=("mark_price",),
            provider="ccxt",
            instrument_type="perpetual",
        )

        # Wait for recent-events buffer to ingest both.
        for _ in range(100):
            recent = await control_plane.recent_events(limit=10)
            names = {ev.event_name for ev in recent["recent_events"]}
            if "ticker" in names and "mark_price" in names:
                break
            await asyncio.sleep(0.02)

        recent = await control_plane.recent_events(limit=10)
        for ev in recent["recent_events"]:
            captured.append({
                "event_name": ev.event_name,
                "provider": ev.provider,
                "instrument_type": ev.instrument_type,
                "canonical_symbol": ev.canonical_symbol,
                "symbol": ev.symbol,
                "matched_target_ids": list(ev.matched_target_ids),
            })
    finally:
        await runtime.aclose()
    return captured


async def main() -> int:
    print("=== attempting live ccxt.pro smoke ===")
    live_results = await _try_live()
    live_ok = sum(1 for r in live_results if "error" not in r)
    print(json.dumps(live_results, indent=2, default=str))
    print(f"live capability samples captured: {live_ok}")

    print("\n=== near-live hub traversal (mock adapter → runtime → control plane) ===")
    mock_results = await _mock_hub_traversal()
    print(json.dumps(mock_results, indent=2, default=str))
    event_names = {r["event_name"] for r in mock_results}
    assert "ticker" in event_names, "ticker missing from control-plane recent events"
    assert "mark_price" in event_names, "mark_price missing from control-plane recent events"
    print("PASS: spot ticker + perpetual mark_price traversed adapter→runtime→publisher→control-plane recent events")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
