# AGENTS.md

## Commands
- Local: `uvicorn apps.api_web.app:app --reload`
- Compose: `docker compose up api-web --build`

## Scope
- This directory is only the deploy/runtime FastAPI entrypoint.
- `app.py` should stay a thin re-export of `src.web_app.app`.
- Real routes, HTML, streaming endpoints, and dashboard behavior belong in `src/web_app.py`.

## Always
- Confirm `src/web_app.py` is still the implementation source before editing here.
- Keep the public export name `app` stable.
- Prefer import-path and bootstrap fixes only.

## Ask First
- Changing the `apps.api_web.app:app` import path.
- Adding another app factory or parallel web entrypoint.

## Never
- Copy dashboard HTML, JS, or business logic into this directory.
- Bypass `src.web_app` with temporary forked behavior.
