# AGENTS.md

## Commands
- Setup: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Dashboard: `python web.py`
- FastAPI entrypoint: `uvicorn apps.api_web.app:app --reload`
- CLI watcher: `python main.py --symbol 005930 --market krx`
- Placeholder services: `python -m apps.collector.service` / `python -m apps.processor.service`
- Compose stack: `docker compose up --build`
- Compose check: `docker compose config`

## Repo reality
- Treat this repo as a monorepo with `apps/`, `packages/`, `src/`, and `compose.yaml` as the main navigation points.
- `apps/api_web/app.py` is a thin re-export entrypoint; the real FastAPI app lives in `src/web_app.py`.
- `apps/collector` and `apps/processor` are still placeholder heartbeat services, not full data pipeline services.
- Shared env loading and event helpers live in `packages/shared/config.py` and `packages/shared/events.py`.
- `src/` is the compatibility-heavy implementation layer with the real dashboard and KIS websocket logic.

## Architecture boundaries
- Put cross-service config, env defaults, topic constants, and event helpers in `packages/shared`.
- Put deploy/runtime-specific web entrypoint concerns in `apps/api_web`, not dashboard logic.
- Put real dashboard behavior, compatibility shims, and KIS-specific implementation in `src`.

## Always
- Read `README.md`, `compose.yaml`, and the relevant entrypoint before changing behavior.
- Check `src` before editing `apps/api_web`; most web behavior changes belong in `src/web_app.py`.
- Keep docs and examples public-safe: describe env keys and defaults only, never local secret values.
- If a contract is shared by multiple services, update `packages/shared` instead of duplicating it.

## Ask First
- Adding a new app or package.
- Renaming services, topics, env keys, ports, or compose dependencies.
- Breaking compatibility paths such as `web.py`, `main.py`, `apps.api_web.app:app`, or `src/config.py` re-exports.
- Turning placeholder collector/processor services into real pipeline components.

## Never
- Read from `.env` to copy real values into code, docs, tests, or examples.
- Treat `apps/api_web/app.py` as the place for dashboard business logic.
- Document collector or processor as production-ready when they only emit heartbeat placeholder events.
- Move shared config/event contracts into per-app one-off helpers.

## Verification
- Web path: `uvicorn apps.api_web.app:app --reload`
- Compatibility path: `python web.py`
- Placeholder services: `python -m apps.collector.service` and `python -m apps.processor.service`
