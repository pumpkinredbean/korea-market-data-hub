"""Shared admin/control-plane contracts for catalog, targeting, and runtime views."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from packages.domain.models import (
    CollectionTarget,
    CollectionTargetStatus,
    InstrumentSearchResult,
    RuntimeStatus,
    StorageBinding,
)

from .events import EventType


@dataclass(frozen=True, slots=True)
class EventTypeCatalogEntry:
    """Catalog entry describing one supported canonical event type."""

    event_type: EventType
    topic_name: str
    description: str
    owner_service: str = "collector"


@dataclass(frozen=True, slots=True)
class SourceCapability:
    """Capability descriptor for one (provider, venue, asset_class, instrument_type)
    source advertised to admin UI / control-plane consumers.

    ``label`` is a UI-friendly preset name (e.g. ``"KRX Equity (KXT)"``,
    ``"Binance Spot (CCXT)"``).  ``supported_event_types`` is the union
    of canonical event types this source can stream — admin UI uses this
    to filter the event-type checkboxes per selected source.
    """

    provider: str
    venue: str
    asset_class: str
    instrument_type: str
    label: str
    supported_event_types: tuple[str, ...]
    market_scope_required: bool = False


@dataclass(frozen=True, slots=True)
class SourceRuntimeStatus:
    """Provider-level logical runtime status row.

    Surfaces ``kxt``/``ccxt`` as first-class logical runtime units in the
    admin/control-plane snapshot.  The Docker container lifecycle is
    tracked separately — this row describes whether the provider's
    publication side is currently enabled and how many of its targets
    are actively streaming.
    """

    provider: str
    state: str
    enabled: bool
    active_target_count: int
    last_error: str | None = None
    observed_at: datetime | None = None
    schema_version: str = "v1"


@dataclass(frozen=True, slots=True)
class ControlPlaneSnapshot:
    """API- or Kafka-ready snapshot of the first admin/control-plane surface."""

    captured_at: datetime
    source_service: str
    event_type_catalog: tuple[EventTypeCatalogEntry, ...] = ()
    instrument_results: tuple[InstrumentSearchResult, ...] = ()
    collection_targets: tuple[CollectionTarget, ...] = ()
    storage_bindings: tuple[StorageBinding, ...] = ()
    runtime_status: tuple[RuntimeStatus, ...] = ()
    collection_target_status: tuple[CollectionTargetStatus, ...] = ()
    # Capability matrix per (provider, venue, asset_class, instrument_type).
    # Replaces the previous global-static event-type catalog as the source
    # of truth for "what events can I subscribe to for this source?".
    source_capabilities: tuple[SourceCapability, ...] = ()
    # KSXT realtime session-level state (IDLE/CONNECTING/HEALTHY/DEGRADED/CLOSED).
    # Surfaces a separate admin-UI banner from the collector_offline state.
    session_state: str | None = None
    # Provider-level logical runtime rows (kxt, ccxt).  Additive; does
    # not replace ``runtime_status``.
    source_runtime_status: tuple[SourceRuntimeStatus, ...] = ()
    schema_version: str = "v1"


@dataclass(frozen=True, slots=True)
class RecentRuntimeEvent:
    """Bounded operator-facing event sample from current runtime activity.

    ``provider``/``canonical_symbol``/``instrument_type``/``raw_symbol``
    are additive multiprovider fields.  Provider values are externally
    normalised (``kxt`` or ``ccxt``); ``ccxt_pro`` is collapsed at the
    boundary.  ``raw_symbol`` is the venue-native identifier kept
    separate from the unified/display ``symbol``.
    """

    event_id: str
    topic_name: str
    event_name: str
    symbol: str
    market_scope: str
    published_at: datetime
    matched_target_ids: tuple[str, ...] = ()
    payload: dict[str, Any] | None = None
    schema_version: str = "v1"
    provider: str | None = None
    canonical_symbol: str | None = None
    instrument_type: str | None = None
    raw_symbol: str | None = None


# ─── Admin Charts + Indicator Runtime (step 1) ─────────────────────────────


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """Single series datum emitted by an indicator.

    ``timestamp`` is an ISO-8601 UTC string (no timezone suffix required;
    the receiver interprets it as UTC).  ``value`` is a plain number.
    ``meta`` is an optional dict for per-point annotations (e.g. the
    top-N used for an OBI computation); it must be JSON-serialisable.
    """

    timestamp: str
    value: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChartInputSlot:
    """One declaration-driven input slot on a :class:`ChartSeriesBinding`.

    ``slot_name`` is the declared slot identifier (e.g. ``"stream"`` for
    builtin.raw or ``"orderbook"`` for builtin.obi).  The remaining
    fields identify which live feed fills this slot.
    """

    slot_name: str
    target_id: str = ""
    event_name: str = ""
    time_field_name: str = ""
    field_name: str = ""


@dataclass(frozen=True, slots=True)
class ChartPanelBaseFeed:
    """Candle base feed selector on a :class:`ChartPanelSpec`.

    Candle panels render OHLCV from the raw event stream identified by
    ``target_id`` + ``event_name``; overlay series live in
    ``series_bindings``.
    """

    target_id: str = ""
    event_name: str = "ohlcv"
    time_field_name: str = ""


@dataclass(frozen=True, slots=True)
class IndicatorInputDecl:
    """Declaration for one input slot of an indicator."""

    slot_name: str
    event_names: tuple[str, ...] = ()
    field_hints: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True, slots=True)
class IndicatorParamDecl:
    """Declaration for one parameter of an indicator.

    ``kind`` ∈ {"int","float","str","bool","enum"}.
    """

    name: str
    kind: str
    default: Any = None
    min: Any = None
    max: Any = None
    choices: tuple = ()
    label: str = ""
    help: str = ""


@dataclass(frozen=True, slots=True)
class IndicatorOutputDecl:
    """Declaration for one output stream of an indicator.

    ``kind`` ∈ {"line","histogram","marker"}.  Exactly one output
    should carry ``is_primary=True``.
    """

    name: str
    kind: str = "line"
    label: str = ""
    is_primary: bool = False


@dataclass(frozen=True, slots=True)
class IndicatorDeclaration:
    """Declaration driving the inspector form for an indicator."""

    inputs: tuple[IndicatorInputDecl, ...] = ()
    params: tuple[IndicatorParamDecl, ...] = ()
    outputs: tuple[IndicatorOutputDecl, ...] = ()


@dataclass(frozen=True, slots=True)
class ChartSeriesBinding:
    """Indicator-first per-series binding inside a :class:`ChartPanelSpec`.

    * ``indicator_ref`` selects the indicator (``"builtin.raw"``,
      ``"builtin.obi"`` or a persisted script_id).
    * ``instance_id`` links to a live ``IndicatorInstanceSpec`` when
      the indicator requires runtime activation (custom scripts);
      empty for pure passthrough (``builtin.raw``).
    * ``input_bindings`` fills the declared input slots with
      (slot_name, target_id, event_name, field_name).
    * ``param_values`` is a tuple of (name, value) pairs preserving
      frozen-dataclass hashability; the API boundary converts to/from
      dict via :func:`param_values_as_dict` / :func:`param_values_from_dict`.
    * ``output_name`` selects which declared output to render.
    """

    binding_id: str
    indicator_ref: str = ""
    instance_id: str = ""
    input_bindings: tuple[ChartInputSlot, ...] = ()
    param_values: tuple[tuple[str, Any], ...] = ()
    output_name: str = ""
    axis: str = "left"
    color: str = ""
    label: str = ""
    visible: bool = True


def param_values_as_dict(values: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    return {k: v for (k, v) in values}


def param_values_from_dict(d: dict[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    if not d:
        return ()
    return tuple((str(k), v) for k, v in d.items())


@dataclass(frozen=True, slots=True)
class ChartPanelSpec:
    """Admin-UI persisted panel descriptor (indicator-first).

    ``chart_type`` is ``"line"`` or ``"candle"``.  Candle panels use
    ``base_feed`` for the OHLCV base stream and treat ``series_bindings``
    as overlay lines.  Line panels render every binding as a line.
    Per-panel ``scripts`` and ``instances`` allow authoring indicators
    inside a panel; the global registry still provides built-ins.
    """

    panel_id: str
    chart_type: str
    symbol: str = ""
    x: int = 0
    y: int = 0
    w: int = 12
    h: int = 14
    title: str | None = None
    notes: str | None = None
    series_bindings: tuple[ChartSeriesBinding, ...] = ()
    base_feed: ChartPanelBaseFeed | None = None
    scripts: tuple["IndicatorScriptSpec", ...] = ()
    instances: tuple["IndicatorInstanceSpec", ...] = ()


@dataclass(frozen=True, slots=True)
class IndicatorScriptSpec:
    """Persisted user-authored indicator script.

    ``source`` is raw Python text that defines exactly one subclass of
    ``HubIndicator``.  ``class_name`` records which top-level class the
    runtime should instantiate.  ``builtin`` is true for indicators
    shipped with the hub (e.g. ``obi``) whose source is pinned and
    whose validation step is skipped.  ``declaration`` is optional and
    drives the inspector form; when absent the runtime synthesises one
    from the indicator's ``inputs`` class attribute.
    """

    script_id: str
    name: str
    source: str
    class_name: str
    builtin: bool = False
    description: str | None = None
    declaration: IndicatorDeclaration | None = None


@dataclass(frozen=True, slots=True)
class IndicatorInstanceSpec:
    """Runtime activation record for one indicator on one symbol."""

    instance_id: str
    script_id: str
    symbol: str
    market_scope: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class IndicatorOutputEnvelope:
    """Fan-out envelope emitted by the indicator runtime."""

    instance_id: str
    script_id: str
    name: str
    symbol: str
    market_scope: str
    output_kind: str
    published_at: datetime
    point: SeriesPoint
    schema_version: str = "v1"
