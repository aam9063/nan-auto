"""Minimal FastAPI application for the NaN Auto gateway scaffold."""

import httpx
from fastapi import FastAPI

from nan_auto.clef_client.base import DecisionProvider
from nan_auto.clef_client.mock import MockDecisionProvider
from nan_auto.clef_client.nan import NanDecisionProvider
from nan_auto.gateway.log_store import DecisionLogStore
from nan_auto.gateway.proxy import proxy_router
from nan_auto.registry.catalog import CatalogClient, load_card
from nan_auto.registry.router import registry_router
from nan_auto.registry.state import Registry
from nan_auto.schema.alias import ROUTER_ALIASES
from nan_auto.settings import Settings


def default_registry(settings: Settings | None = None) -> Registry:
    """Build the registry from the local card; fetch live only with a key.

    Without a configured key there is no network call at all (mock-friendly):
    the inventory degrades to the card. The refresh itself is lazy with a TTL
    and fails open, so a dead catalog never breaks the gateway.
    """
    card = load_card()
    settings = settings or Settings()
    if settings.nan_key:
        client = CatalogClient(client=default_client(settings), api_key=settings.nan_key)
        return Registry(card, fetcher=client.fetch)
    return Registry(card)


def default_client(settings: Settings) -> httpx.AsyncClient:
    """The shared upstream client, with a timeout that fits token generation.

    Without this, httpx's 5 s default turns any slow-but-healthy completion into
    a transport error and a 502. The connect timeout stays short so an
    unreachable host fails fast.
    """
    return httpx.AsyncClient(timeout=httpx.Timeout(settings.upstream_timeout_s, connect=10.0))


def default_provider(settings: Settings, client: httpx.AsyncClient) -> DecisionProvider:
    """Pick the decision provider named by `CLEF_PROVIDER` (spec 6.1)."""
    if settings.clef_provider == "nan":
        return NanDecisionProvider(
            client=client,
            api_key=settings.nan_key,
            url=f"{settings.nan_base_url}/systemone",
            max_in_flight=settings.clef_max_in_flight,
            timeout=settings.decision_timeout_ms / 1000,
        )
    return MockDecisionProvider()


def create_app(
    registry: Registry | None = None,
    *,
    provider: DecisionProvider | None = None,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    store: DecisionLogStore | None = None,
) -> FastAPI:
    """Build the gateway application.

    `store`, when given, receives one decision-log row per routed request
    (NAU-13 T6); without it, decision logging stays off.
    """
    settings = settings or Settings()
    registry = registry or default_registry(settings)
    client = client or default_client(settings)
    provider = provider or default_provider(settings, client)
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

    app.include_router(registry_router(registry))
    app.include_router(proxy_router(registry, provider, settings, client, store))
    return app


app = create_app()
