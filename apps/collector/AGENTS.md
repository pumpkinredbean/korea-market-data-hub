# AGENTS.md

## Commands
- Local: `python -m apps.collector.service`
- Compose: `docker compose up collector --build`

## Scope
- This is currently a placeholder service that prints heartbeat events in a loop.
- Settings come from `packages.shared.config.load_service_settings("collector")`.
- Topic constants and event building come from `packages.shared.events`.

## Always
- Preserve the placeholder reality in code and docs.
- Keep `source="apps.collector.service"` and similar identifiers traceable.
- Reuse shared settings and topic constants instead of local parsing or hardcoded strings.
- Keep the stdout heartbeat path available as a minimal boot check unless intentionally replaced.

## Ask First
- Adding real polling, Kafka producers, DB writes, or external integrations.
- Adding new event contracts beyond the current placeholder heartbeat.

## Never
- Parse collector-specific env separately from `packages/shared/config.py`.
- Claim delivery, persistence, or pipeline guarantees that do not exist yet.
