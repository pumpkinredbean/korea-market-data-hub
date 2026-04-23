"""Canonical per-event field schema for indicator inspector field selectors.

The dashboard / admin Charts inspector cascades:

  target  →  allowed events (intersection of indicator-slot events ∩
             source capability events)
  event   →  allowed numeric fields, resolved with the priority chain
             (canonical → runtime-decl → sampled payload → field_hints)

This module owns the *canonical* layer.  It is intentionally minimal:
each entry only lists keys we reasonably expect to find in the live
payload regardless of provider, and only those that make sense as a
scalar y-axis projection.  Provider-specific extensions are surfaced via
sampled payload fields at runtime; static :class:`IndicatorInputDecl`
``field_hints`` remain the *final* fallback.
"""

from __future__ import annotations

from typing import Final

from .events import EventType


# Canonical field map.  Keys are :class:`EventType` *values* (the wire
# string used by the rest of the system) so JSON consumers can match
# without knowing about Python enums.  Values are tuples of canonical
# scalar field names in display order.
CANONICAL_EVENT_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    EventType.TRADE.value: ("price", "quantity"),
    EventType.ORDER_BOOK_SNAPSHOT.value: (
        # Order book is structural, not scalar — but a few canonical
        # roll-ups are commonly projected.
        "best_bid", "best_ask", "mid_price", "spread",
    ),
    EventType.PROGRAM_TRADE.value: (
        "sell_quantity",
        "buy_quantity",
        "net_buy_quantity",
        "sell_notional",
        "buy_notional",
        "net_buy_notional",
        "program_sell_depth",
        "program_buy_depth",
    ),
    EventType.TICKER.value: (
        "last", "best_bid", "best_ask", "mid_price",
    ),
    EventType.OHLCV.value: ("open", "high", "low", "close", "volume"),
    EventType.MARK_PRICE.value: ("mark_price", "index_price"),
    EventType.FUNDING_RATE.value: ("rate", "funding_rate", "next_funding_time"),
    EventType.OPEN_INTEREST.value: ("open_interest", "value"),
}


def canonical_fields_for_event(event_name: str) -> tuple[str, ...]:
    """Return the canonical scalar field tuple for ``event_name`` or ``()``."""
    if not event_name:
        return ()
    return CANONICAL_EVENT_FIELDS.get(str(event_name), ())


def canonical_event_field_schema() -> dict[str, list[str]]:
    """JSON-friendly snapshot of :data:`CANONICAL_EVENT_FIELDS`.

    Lists are returned (not tuples) so the dict serialises identically
    via :func:`json.dumps` and FastAPI's ``jsonable_encoder``.
    """
    return {k: list(v) for k, v in CANONICAL_EVENT_FIELDS.items()}
