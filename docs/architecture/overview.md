# Architecture Overview

## Purpose

`korea-market-data-hub` is evolving into a reusable real-time market data hub stack for Korean equities and related workflows. The current live integration is KIS, but the long-term architecture is broker-agnostic.

## Current direction

- treat broker integrations as edge adapters, not as the system identity
- keep shared domain concepts, contracts, and downstream pipelines broker-neutral where practical
- separate ingress, normalization, processing, storage, and serving responsibilities
- document current committed direction only; exploratory notes stay outside this repo

## High-level flow

```text
broker adapter -> collector -> Kafka/Redpanda dashboard topic -> api/web -> browser
                    |                                          \
                    |                                           -> collector control API (start/stop)
                    \
                     -> processor -> analytical storage
```

Today, `korea-market-data-hub` already contains the monorepo skeleton for that shape:

- `apps/collector`: FastAPI collector service that owns the live KIS upstream/runtime, publishes dashboard events, exposes start/stop control endpoints, and relays price-chart fetches
- `apps/processor`: placeholder service for downstream processing-stage wiring
- `apps/api_web`: API and dashboard entrypoint
- `packages/shared`: current shared config and minimal event helpers
- `packages/domain`, `packages/contracts`, `packages/adapters`, `packages/infrastructure`: emerging broker-agnostic package boundaries
- `src/`: compatibility-heavy implementation layer, including current KIS-backed behavior

## Current reality

- KIS is the active adapter today
- `collector` is the working dashboard ingress owner with `/health`, dashboard publication control endpoints, and `/api/price-chart`; `processor` is still a placeholder runtime
- the repo already contains early broker-neutral scaffolding such as domain models and canonical event/topic packages
- compose wiring already reflects the intended platform shape with collector, processor, Redpanda, ClickHouse, and API/web services
- KRX-first dashboard live/runtime keeps collector as the only KIS upstream owner
- the smallest Kafka-backed live slice uses one broker-neutral dashboard topic (`market.dashboard-events.v1`) as the broadcast core
- collector publishes KIS-formatted dashboard events into that topic and no longer consumes that same topic for browser delivery
- `api/web` does not own the live KIS runtime; it requests collector start/stop for a symbol/market-scope, consumes dashboard events directly from Kafka/Redpanda, and relays them to browsers over SSE
- ClickHouse and richer processor stages remain outside this dashboard loop for now

## Design rules

- broker-specific auth, protocol details, and source quirks stay in adapter packages
- canonical event meaning should not depend on one provider's field names
- raw, canonical, and derived data should remain distinct
- serving models may optimize for query and UI needs, but should not redefine core event semantics

## Related docs

- `data-inventory.md`
- `adapter-boundary.md`
- `raw-canonical-derived.md`
- `package-layout.md`
- `../adr/ADR-001-broker-agnostic-core.md`
