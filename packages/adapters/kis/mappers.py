"""KIS-specific mapping helpers kept outside the broker-neutral contracts layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from packages.contracts import CanonicalEvent, ChannelType, EventType, SubscriptionSpec
from packages.domain.models import OrderBookSnapshot, ProgramTrade, QuoteLevel, Trade


KIS_DOMESTIC_STOCK_TRADE_COLUMNS: tuple[str, ...] = (
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "STCK_PRPR",
    "PRDY_VRSS_SIGN",
    "PRDY_VRSS",
    "PRDY_CTRT",
    "WGHN_AVRG_STCK_PRC",
    "STCK_OPRC",
    "STCK_HGPR",
    "STCK_LWPR",
    "ASKP1",
    "BIDP1",
    "CNTG_VOL",
    "ACML_VOL",
    "ACML_TR_PBMN",
)

KIS_DOMESTIC_STOCK_ORDER_BOOK_COLUMNS: tuple[str, ...] = (
    "MKSC_SHRN_ISCD",
    "BSOP_HOUR",
    "HOUR_CLS_CODE",
    "ASKP1",
    "ASKP2",
    "ASKP3",
    "ASKP4",
    "ASKP5",
    "ASKP6",
    "ASKP7",
    "ASKP8",
    "ASKP9",
    "ASKP10",
    "BIDP1",
    "BIDP2",
    "BIDP3",
    "BIDP4",
    "BIDP5",
    "BIDP6",
    "BIDP7",
    "BIDP8",
    "BIDP9",
    "BIDP10",
    "ASKP_RSQN1",
    "ASKP_RSQN2",
    "ASKP_RSQN3",
    "ASKP_RSQN4",
    "ASKP_RSQN5",
    "ASKP_RSQN6",
    "ASKP_RSQN7",
    "ASKP_RSQN8",
    "ASKP_RSQN9",
    "ASKP_RSQN10",
    "BIDP_RSQN1",
    "BIDP_RSQN2",
    "BIDP_RSQN3",
    "BIDP_RSQN4",
    "BIDP_RSQN5",
    "BIDP_RSQN6",
    "BIDP_RSQN7",
    "BIDP_RSQN8",
    "BIDP_RSQN9",
    "BIDP_RSQN10",
    "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN",
    "OVTM_TOTAL_ASKP_RSQN",
    "OVTM_TOTAL_BIDP_RSQN",
    "ANTC_CNPR",
    "ANTC_CNQN",
    "ANTC_VOL",
    "ANTC_CNTG_VRSS",
    "ANTC_CNTG_VRSS_SIGN",
    "ANTC_CNTG_PRDY_CTRT",
    "ACML_VOL",
    "TOTAL_ASKP_RSQN_ICDC",
    "TOTAL_BIDP_RSQN_ICDC",
    "OVTM_TOTAL_ASKP_ICDC",
    "OVTM_TOTAL_BIDP_ICDC",
    "STCK_DEAL_CLS_CODE",
)

KIS_DOMESTIC_STOCK_PROGRAM_TRADE_COLUMNS: tuple[str, ...] = (
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "SELN_CNQN",
    "SELN_TR_PBMN",
    "SHNU_CNQN",
    "SHNU_TR_PBMN",
    "NTBY_CNQN",
    "NTBY_TR_PBMN",
    "SELN_RSQN",
    "SHNU_RSQN",
    "WHOL_NTBY_QTY",
)


KIS_TR_ID_BY_CHANNEL_AND_MARKET: dict[tuple[ChannelType, str], str] = {
    (ChannelType.TRADE, "krx"): "H0STCNT0",
    (ChannelType.TRADE, "nxt"): "H0NXCNT0",
    (ChannelType.TRADE, "total"): "H0UNCNT0",
    (ChannelType.ORDER_BOOK_SNAPSHOT, "krx"): "H0STASP0",
    (ChannelType.ORDER_BOOK_SNAPSHOT, "nxt"): "H0NXASP0",
    (ChannelType.ORDER_BOOK_SNAPSHOT, "total"): "H0UNASP0",
    (ChannelType.PROGRAM_TRADE, "krx"): "H0STPGM0",
    (ChannelType.PROGRAM_TRADE, "nxt"): "H0NXPGM0",
    (ChannelType.PROGRAM_TRADE, "total"): "H0UNPGM0",
}

EVENT_TYPE_BY_CHANNEL: dict[ChannelType, EventType] = {
    ChannelType.TRADE: EventType.TRADE,
    ChannelType.ORDER_BOOK_SNAPSHOT: EventType.ORDER_BOOK_SNAPSHOT,
    ChannelType.PROGRAM_TRADE: EventType.PROGRAM_TRADE,
}

KIS_REALTIME_COLUMNS_BY_TR_ID: dict[str, tuple[str, ...]] = {
    "H0STCNT0": KIS_DOMESTIC_STOCK_TRADE_COLUMNS,
    "H0NXCNT0": KIS_DOMESTIC_STOCK_TRADE_COLUMNS,
    "H0UNCNT0": KIS_DOMESTIC_STOCK_TRADE_COLUMNS,
    "H0STASP0": KIS_DOMESTIC_STOCK_ORDER_BOOK_COLUMNS,
    "H0NXASP0": KIS_DOMESTIC_STOCK_ORDER_BOOK_COLUMNS,
    "H0UNASP0": KIS_DOMESTIC_STOCK_ORDER_BOOK_COLUMNS,
    "H0STPGM0": KIS_DOMESTIC_STOCK_PROGRAM_TRADE_COLUMNS,
    "H0NXPGM0": KIS_DOMESTIC_STOCK_PROGRAM_TRADE_COLUMNS,
    "H0UNPGM0": KIS_DOMESTIC_STOCK_PROGRAM_TRADE_COLUMNS,
}


@dataclass(frozen=True, slots=True)
class KISSubscriptionBinding:
    """Resolved KIS subscription values for one broker-neutral spec."""

    spec: SubscriptionSpec
    tr_id: str
    tr_key: str
    market: str


@dataclass(frozen=True, slots=True)
class KISRealtimeRow:
    """Parsed KIS websocket row with broker-specific metadata preserved."""

    binding: KISSubscriptionBinding
    tr_id: str
    received_at: datetime
    raw_message: str
    values: tuple[str, ...]
    fields: dict[str, str]


def resolve_market(subscription: SubscriptionSpec) -> str:
    """Infer the KIS market discriminator from generic subscription options."""

    market = str(subscription.options.get("market") or "krx").strip().lower()
    if not market:
        return "krx"
    return market


def resolve_subscription_binding(subscription: SubscriptionSpec) -> KISSubscriptionBinding:
    """Translate a broker-neutral spec into a KIS realtime subscription tuple."""

    market = resolve_market(subscription)
    tr_id = KIS_TR_ID_BY_CHANNEL_AND_MARKET[(subscription.channel_type, market)]
    return KISSubscriptionBinding(
        spec=subscription,
        tr_id=tr_id,
        tr_key=subscription.instrument.symbol,
        market=market,
    )


def resolve_event_type(subscription: SubscriptionSpec) -> EventType:
    """Map a subscription channel to the canonical event family."""

    return EVENT_TYPE_BY_CHANNEL[subscription.channel_type]


def resolve_realtime_columns(tr_id: str) -> tuple[str, ...]:
    """Return the known leading raw columns for a supported realtime TR."""

    try:
        return KIS_REALTIME_COLUMNS_BY_TR_ID[tr_id]
    except KeyError as exc:
        raise KeyError(f"Unsupported realtime TR ID: {tr_id}") from exc


def map_trade_event(row: KISRealtimeRow) -> CanonicalEvent[Trade]:
    """Map a KIS domestic stock trade row into the canonical trade contract."""

    occurred_at = _parse_kis_trade_timestamp(row.fields.get("STCK_CNTG_HOUR", ""), row.received_at)
    price = _parse_decimal(row.fields.get("STCK_PRPR"), field_name="STCK_PRPR")
    quantity = _parse_decimal(row.fields.get("CNTG_VOL"), default=Decimal("0"))

    payload = Trade(
        instrument=row.binding.spec.instrument,
        occurred_at=occurred_at,
        price=price,
        quantity=quantity,
        side=None,
        trade_id=_build_trade_id(row),
        sequence=row.fields.get("ACML_VOL") or None,
    )
    return CanonicalEvent(
        event_type=resolve_event_type(row.binding.spec),
        provider="kis",
        occurred_at=occurred_at,
        received_at=row.received_at,
        payload=payload,
        raw_payload={
            "tr_id": row.tr_id,
            "market": row.binding.market,
            "fields": row.fields,
            "values": row.values,
        },
    )


def map_order_book_event(row: KISRealtimeRow) -> CanonicalEvent[OrderBookSnapshot]:
    occurred_at = _parse_kis_trade_timestamp(row.fields.get("BSOP_HOUR", ""), row.received_at)
    asks = tuple(_build_quote_level(row, side="ASKP", quantity_side="ASKP_RSQN", level=level) for level in range(1, 11))
    bids = tuple(_build_quote_level(row, side="BIDP", quantity_side="BIDP_RSQN", level=level) for level in range(1, 11))

    return CanonicalEvent(
        event_type=resolve_event_type(row.binding.spec),
        provider="kis",
        occurred_at=occurred_at,
        received_at=row.received_at,
        payload=OrderBookSnapshot(
            instrument=row.binding.spec.instrument,
            occurred_at=occurred_at,
            asks=asks,
            bids=bids,
        ),
        raw_payload={
            "tr_id": row.tr_id,
            "market": row.binding.market,
            "fields": row.fields,
            "values": row.values,
        },
    )


def map_program_trade_event(row: KISRealtimeRow) -> CanonicalEvent[ProgramTrade]:
    occurred_at = _parse_kis_trade_timestamp(row.fields.get("STCK_CNTG_HOUR", ""), row.received_at)
    return CanonicalEvent(
        event_type=resolve_event_type(row.binding.spec),
        provider="kis",
        occurred_at=occurred_at,
        received_at=row.received_at,
        payload=ProgramTrade(
            instrument=row.binding.spec.instrument,
            occurred_at=occurred_at,
            sell_quantity=_parse_decimal(row.fields.get("SELN_CNQN"), default=Decimal("0")),
            buy_quantity=_parse_decimal(row.fields.get("SHNU_CNQN"), default=Decimal("0")),
            net_buy_quantity=_parse_decimal(row.fields.get("NTBY_CNQN"), default=Decimal("0")),
            sell_notional=_parse_decimal(row.fields.get("SELN_TR_PBMN"), default=Decimal("0")),
            buy_notional=_parse_decimal(row.fields.get("SHNU_TR_PBMN"), default=Decimal("0")),
            net_buy_notional=_parse_decimal(row.fields.get("NTBY_TR_PBMN"), default=Decimal("0")),
            program_sell_depth=_parse_decimal(row.fields.get("SELN_RSQN"), default=Decimal("0")),
            program_buy_depth=_parse_decimal(row.fields.get("SHNU_RSQN"), default=Decimal("0")),
        ),
        raw_payload={
            "tr_id": row.tr_id,
            "market": row.binding.market,
            "fields": row.fields,
            "values": row.values,
        },
    )


def _parse_kis_trade_timestamp(time_text: str, received_at: datetime) -> datetime:
    normalized = time_text.strip()
    if len(normalized) != 6 or not normalized.isdigit():
        return received_at

    return received_at.replace(
        hour=int(normalized[0:2]),
        minute=int(normalized[2:4]),
        second=int(normalized[4:6]),
        microsecond=0,
    )


def _parse_decimal(
    value: str | None,
    *,
    field_name: str | None = None,
    default: Decimal | None = None,
) -> Decimal:
    text = str(value or "").strip()
    if not text:
        if default is not None:
            return default
        raise ValueError(f"Missing numeric KIS field: {field_name or 'unknown'}")

    try:
        return Decimal(text)
    except InvalidOperation as exc:
        if default is not None:
            return default
        raise ValueError(f"Invalid numeric KIS field: {field_name or 'unknown'}={text}") from exc


def _build_trade_id(row: KISRealtimeRow) -> str:
    symbol = row.binding.spec.instrument.symbol
    time_text = row.fields.get("STCK_CNTG_HOUR") or "unknown"
    price = row.fields.get("STCK_PRPR") or "unknown"
    quantity = row.fields.get("CNTG_VOL") or "unknown"
    return f"{symbol}:{time_text}:{price}:{quantity}"


def _build_quote_level(row: KISRealtimeRow, *, side: str, quantity_side: str, level: int) -> QuoteLevel:
    return QuoteLevel(
        price=_parse_decimal(row.fields.get(f"{side}{level}"), default=Decimal("0")),
        quantity=_parse_decimal(row.fields.get(f"{quantity_side}{level}"), default=Decimal("0")),
    )
