"""KXT (KSXT-backed KIS) adapter boundary marker.

Step 1 of the multiprovider migration keeps the live KSXT runtime inside
``apps.collector.runtime.CollectorRuntime`` — this module exists so the
provider registry can reference a stable KXT adapter identity without
importing the collector runtime at module import time.  A fuller
``MarketDataAdapter`` implementation wrapping KSXT will move here in the
next step; for now we only expose a small descriptor object.
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.domain.enums import Provider, Venue


@dataclass(frozen=True, slots=True)
class KXTAdapterStub:
    """Lightweight descriptor for the KXT provider boundary.

    The real KXT streaming path currently lives in
    :class:`apps.collector.runtime.CollectorRuntime` and is selected by
    the collector when the target provider resolves to ``Provider.KXT``.
    """

    adapter_id: str = "kxt"
    provider: Provider = Provider.KXT
    default_venue: Venue = Venue.KRX
    implemented: bool = True
    notes: str = (
        "KSXT KISRealtimeSession-backed runtime is owned by "
        "apps.collector.runtime.CollectorRuntime in step 1."
    )


def build_kxt_adapter_stub() -> KXTAdapterStub:
    """Factory used by :func:`packages.adapters.registry.build_default_registry`."""

    return KXTAdapterStub()
