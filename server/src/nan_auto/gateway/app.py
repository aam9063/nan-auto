"""Minimal FastAPI application for the NaN Auto gateway scaffold."""

import httpx
from fastapi import FastAPI

from nan_auto.registry.catalog import CatalogClient, load_card
from nan_auto.registry.router import registry_router
from nan_auto.registry.state import Registry
from nan_auto.schema.alias import ROUTER_ALIASES
from nan_auto.settings import Settings


def default_registry() -> Registry:
    """Build the registry from the local card; fetch live only with a key.

    Without a configured key there is no network call at all (mock-friendly):
    the inventory degrades to the card. The refresh itself is lazy with a TTL
    and fails open, so a dead catalog never breaks the gateway.
    """
    card = load_card()
    settings = Settings()
    if settings.nan_key:
        client = CatalogClient(client=httpx.AsyncClient(), api_key=settings.nan_key)
        return Registry(card, fetcher=client.fetch)
    return Registry(card)


def create_app(registry: Registry | None = None) -> FastAPI:
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

    app.include_router(registry_router(registry or default_registry()))
    return app


app = create_app()
