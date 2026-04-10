# AGENTS.md

## Commands
- Local: `python -m apps.processor.service`
- Compose: `docker compose up processor --build`

## Scope
- This is currently a placeholder service that prints heartbeat events in a loop.
- Settings come from `packages.shared.config.load_service_settings("processor")`.
- It uses `packages.shared.events.PROCESSED_EVENTS_TOPIC` and shared event helpers.

## Always
- Preserve the placeholder status in code and docs.
- Keep service name, `source`, and topic usage internally consistent.
- Move shared collector/processor contracts into `packages/shared`.
- Keep new runtime assumptions aligned with compose defaults for Redpanda and ClickHouse.

## Ask First
- Adding real stream transforms, storage logic, consumer groups, or pipeline semantics.
- Changing message schemas or topic contracts.

## Never
- Recreate collector/shared config logic inside this service.
- Claim durability, correctness, or persistence guarantees that are not implemented.
