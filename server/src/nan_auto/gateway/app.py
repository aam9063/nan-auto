"""Minimal FastAPI application for the NaN Auto gateway scaffold."""

from fastapi import FastAPI

from nan_auto.schema.alias import ROUTER_ALIASES


def create_app() -> FastAPI:
    """Build the gateway application."""
    app = FastAPI(title="NaN Auto", version="0.1.0")
    model_ids = list(ROUTER_ALIASES)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    def models() -> dict:
        # Scaffold placeholder: per spec section 13, the registry will merge
        # /v1/models from NaN with config/models.yaml at startup (NAU-03).
        payload = [
            {"id": model_id, "object": "model", "owned_by": "nan-auto"} for model_id in model_ids
        ]
        return {"object": "list", "data": payload}

    return app


app = create_app()
