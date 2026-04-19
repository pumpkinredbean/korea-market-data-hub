# Admin Control-Plane Foundation

## Purpose

This document defines the first concrete admin/control-plane model surface so the project can add admin APIs later without redesigning core concepts when storage binding arrives.

The shape is intentionally aligned with the current runtime reality:

- `collector` remains the only upstream owner
- `api-web` remains a downstream viewer, not the control-plane owner
- Docker runtime stays valid as-is
- Kafka/Redpanda stays in the path for reactive monitoring and future admin event fan-out

## Five core concepts

### 1. Event type catalog

Represents the supported canonical event families the collector can expose and downstream systems can reason about.

- code surface: `packages.contracts.admin.EventTypeCatalogEntry`
- current source enum: `packages.contracts.events.EventType`
- purpose: requirement **(1) inspect supported event types**

### 2. Instrument search result / registry surface

Represents a searchable, stable instrument discovery result without making the web viewer the source of truth.

- code surface: `packages.domain.models.InstrumentSearchResult`
- purpose: requirement **(2) search valid instruments**

This is deliberately a registry/search surface, not a live subscription object.

### 3. Collection target

Represents the collector-owned declaration of what should be streamed upstream for a given instrument + `market_scope` + event-type set.

- code surface: `packages.domain.models.CollectionTarget`
- purpose: requirement **(3) monitor what is currently being collected** and future collector-side configuration

`market_scope` is the request-selection concept. It is not a venue model.

### 4. Storage binding

Represents a mapping rule from incoming event families to a storage backend/destination, optionally narrowed to one collection target.

- code surface: `packages.domain.models.StorageBinding`
- purpose: requirement **(4) configure how incoming events map into storage**

This keeps storage mapping separate from collection intent so storage can be added later without rewriting collection semantics.

### 5. Runtime status

Represents both service-level and per-target runtime state for reactive admin monitoring.

- code surface:
  - `packages.domain.models.RuntimeStatus`
  - `packages.domain.models.CollectionTargetStatus`
- shared state enum: `packages.domain.enums.RuntimeState`

## Aggregate snapshot surface

`packages.contracts.admin.ControlPlaneSnapshot` is the first API-/Kafka-ready aggregate envelope. It gathers:

- event type catalog
- instrument search results
- collection targets
- storage bindings
- service runtime status
- per-target runtime status

This gives later admin APIs a clean top-level response/document shape without forcing the repo to build the full admin UI now.

## Kafka path

The repo now reserves `packages.contracts.topics.CONTROL_PLANE_EVENTS_TOPIC`:

- topic: `market.control-plane-events.v1`
- intended owner: `collector` or a future dedicated admin/control-plane service
- intended consumers: admin API/viewers, runtime monitors, storage-routing observers

This preserves the current rule that the browser/web path is a viewer only, while keeping reactive control-plane state on the broker path.

## Why this avoids later redesign

The model separates four concerns that often get incorrectly fused:

1. what event families exist (`EventTypeCatalogEntry`)
2. what instruments can be referenced (`InstrumentSearchResult`)
3. what the collector should actively collect (`CollectionTarget`)
4. how collected events should land in storage (`StorageBinding`)

Because storage binding is independent from collection targeting, later persistence work can be added without redefining the meaning of "currently collected" or inventing a replacement for `market_scope`.

## Current implementation scope

The repo now includes the first end-to-end admin/control-plane slice.

- collector-owned in-memory control-plane state/service
- collector admin HTTP endpoints for snapshot, instrument search, target upsert, and target delete
- api-web relay endpoints and a dedicated `/admin` page separated from the viewer dashboard
- storage binding snapshot surface present, but binding CRUD/runtime still deferred
- no secrets or `.env` exposure

This is still intentionally narrow: persistence is not added yet, and the target `event_types` set is currently modeled/stored more completely than it is enforced by the live runtime path.
