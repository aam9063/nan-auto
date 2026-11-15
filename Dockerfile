# NaN Auto — single container for NaN Cloud Spaces: gateway + dashboard.
# The FastAPI gateway serves the Vite-built SPA (same origin, relative /v1).
# The container must listen on $PORT (Spaces container port, default 3000).

# syntax=docker/dockerfile:1

# ---- Stage 1: build the dashboard (React + Vite + Tailwind, pnpm) ----
FROM node:24-alpine AS web-build
WORKDIR /build/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
# GATEWAY_URL only affects the dev proxy, never the production build.
RUN pnpm build

# ---- Stage 2: install the Python project with uv (locked, no dev group) ----
FROM python:3.14-slim AS server-build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY server/pyproject.toml server/uv.lock ./server/
RUN cd server && uv sync --frozen --no-dev --no-install-project
COPY server/ ./server/
# The module-level `app = create_app()` loads config/models.yaml on import.
COPY config/ /app/config/
RUN cd server && uv sync --frozen --no-dev
# Sanity: the package and its venv resolve.
RUN /app/server/.venv/bin/python -c "import nan_auto.gateway.app"

# ---- Stage 3: runtime ----
FROM python:3.14-slim
RUN useradd --create-home appuser
WORKDIR /app/server
# Editable install keeps `nan_auto` pointing at ./src, so the module-resolved
# config path (<root>/config) and the dashboard path (<root>/web/dist) hold.
COPY --from=server-build /app/server /app/server
COPY config/ /app/config/
COPY --from=web-build /build/web/dist /app/web/dist
ENV PATH="/app/server/.venv/bin:$PATH" \
    PORT=3000 \
    DECISION_DB_PATH=/app/server/dev_decisions.sqlite
USER appuser
EXPOSE 3000
CMD ["python", "serve.py"]
