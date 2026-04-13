"""Minimal broker-agnostic ingress event contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar


TPayload = TypeVar("TPayload")


class EventType(StrEnum):
    """Current broker-neutral ingress event types."""

    TRADE = "trade"
    ORDER_BOOK_SNAPSHOT = "order_book_snapshot"
    PROGRAM_TRADE = "program_trade"


@dataclass(frozen=True, slots=True)
class CanonicalEvent(Generic[TPayload]):
    """Transport wrapper for canonical ingress payloads."""

    event_type: EventType
    provider: str
    occurred_at: datetime
    received_at: datetime
    payload: TPayload
    schema_version: str = "v1"
    raw_payload: Any | None = None


CanonicalEventEnvelope = CanonicalEvent


@dataclass(frozen=True, slots=True)
class DashboardEventEnvelope:
    """Broker-neutral envelope for live dashboard fan-out events."""

    symbol: str
    market: str
    event_name: str
    payload: dict[str, Any]
    published_at: datetime
    schema_version: str = "v1"


@dataclass(frozen=True, slots=True)
class DashboardControlEnvelope:
    """Broker-neutral envelope for dashboard publication control."""

    action: str
    owner_id: str
    symbol: str
    market: str
    requested_at: datetime
    schema_version: str = "v1"
