# AGENTS.md

## Commands
- Inspect shared service settings: `python -c "from packages.shared.config import load_service_settings; print(load_service_settings('collector'))"`
- Inspect event shape: `python -c "from packages.shared.events import build_service_event; print(build_service_event(event_type='x', source='y', payload={}))"`

## Scope
- `config.py` owns shared env loading, defaults, proxy-bypass behavior, and service settings loaders.
- `events.py` owns shared topic constants and the minimal service event envelope.
- Put code here only when it is used across multiple apps.

## Always
- Add new shared settings through the existing dataclass/loader flow.
- Keep topic constants and event helpers small and easy to import.
- Recheck README and compose-facing defaults when changing env defaults.
- Describe env keys and defaults only; keep wording safe for a public repo.

## Ask First
- Renaming env keys, topic names, default ports, or `.env` loading policy.
- Moving app-specific logic into this package.

## Never
- Add one-off app-specific knobs that are not truly shared.
- Put real credentials, secret defaults, or copied local env values in code or docs.
