# NaN Auto

> One OpenAI-compatible endpoint. Clef decides, per request, which NaN model answers and with how much reasoning.

`model: "nan-auto"` in any OpenAI-compatible client (Cursor, OpenCode, Cline, Open WebUI, OpenAI SDK): change only the base URL. Behind it, the gateway extracts deterministic features, filters by capability, asks **Clef** about the sematics, scores candidates and streams the answer with fallback.

## Status

Hackathon build (#2 NaN Hackathon "Decide con Clef", November 13–15, 2026). Full design lives in [nan-auto.md](./nan-auto.md); methodology (ODD, RDD, Git) in its section 15.

## Layout

```
nan-auto/
  server/           # Python 3.14 + uv + FastAPI + Pydantic v2
    src/nan_auto/   # gateway, registry, router_core, clef_client, schema
    tests/          # pytest + pytest-asyncio + respx, fixtures/clef/
  web/              # React 19 + TSX + Vite + Tailwind CSS v4 (pnpm)
  eval/             # RouterBench-style benchmark and offline policy evaluation
  config/           # models.yaml (capabilities/tiers), profiles.yaml (weights)
  odd/tasks/        # ODD feature documents
  docs/             # project documentation
  .githooks/        # pre-push protection for main/dev
```

## Quickstart — server

```bash
cd server
uv sync
uv run uvicorn nan_auto.gateway.app:app --port 8000
```

Configuration uses a local `.env` (copy `.env.example`); the key is never committed.

## Quickstart — web

```bash
pnpm --dir web install
pnpm --dir web dev   # proxies /v1 to http://localhost:8000
```

## Checks

```bash
cd server && uv run pytest && uv run ruff check . && uv run pyright
pnpm --dir web lint && pnpm --dir web typecheck && pnpm --dir web build
```

## Branches

- `main` — stable, demo-ready. Merged by hand from `dev`.
- `dev` — integration. PRs land here from `feature/*`, `fix/*`, `chore/*`.
- Direct pushes to `main`/`dev` are blocked by `.githooks/pre-push`.
