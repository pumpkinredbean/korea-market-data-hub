"""Adapter layer entrypoints for broker or venue integrations."""

from .base import (
    Adapter,
    MarketDataAdapter,
    MarketDataEvent,
    OrderBookSnapshotEvent,
    ProgramTradeEvent,
    TradeEvent,
)
from .ccxt import (
    BinanceLiveAdapter,
    BinanceOrderBookSnapshot,
    BinanceTrade,
    CCXTAdapterStub,
    CCXTProAdapterStub,
    build_binance_live_adapter,
    exchange_id_for,
    to_unified_symbol,
)
from .kxt import KXTAdapterStub
from .registry import ProviderRegistration, ProviderRegistry, build_default_registry

__all__ = [
    "Adapter",
    "BinanceLiveAdapter",
    "BinanceOrderBookSnapshot",
    "BinanceTrade",
    "CCXTAdapterStub",
    "CCXTProAdapterStub",
    "KXTAdapterStub",
    "MarketDataAdapter",
    "MarketDataEvent",
    "OrderBookSnapshotEvent",
    "ProgramTradeEvent",
    "ProviderRegistration",
    "ProviderRegistry",
    "TradeEvent",
    "build_binance_live_adapter",
    "build_default_registry",
    "exchange_id_for",
    "to_unified_symbol",
]
