"""Tests for GET /v1/registry: merged inventory plus state, no secrets (NAU-03-T6)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient
from test_registry_catalog import CHAT_IDS, URL, live_catalog

from nan_auto.gateway.app import create_app
from nan_auto.registry.catalog import CatalogClient, CatalogEntry, load_card, parse_catalog
from nan_auto.registry.errors import CatalogUnavailable
from nan_auto.registry.state import Registry

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)


async def stub_fetch() -> tuple[CatalogEntry, ...]:
    return parse_catalog(live_catalog())


def make_app(**registry_kwargs: Any):
    registry = Registry(load_card(), fetcher=stub_fetch, **registry_kwargs)
    return create_app(registry=registry), registry


def test_registry_endpoint_serves_the_merged_inventory() -> None:
    app, _ = make_app()
    response = TestClient(app).get("/v1/registry")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    ids = [item["id"] for item in body["data"]]
    assert sorted(ids) == sorted(CHAT_IDS)


def test_registry_endpoint_reports_card_metadata() -> None:
    app, _ = make_app()
    data = {item["id"]: item for item in TestClient(app).get("/v1/registry").json()["data"]}
    qwen = data["qwen3.6"]
    assert qwen["input"] == ["text", "image"]
    assert qwen["tools"] is True
    assert qwen["tier"] == "t2_generalist"
    assert qwen["premium"] is False


def test_registry_endpoint_reports_per_model_state() -> None:
    app, registry = make_app()
    registry.health("qwen3.6").mark_failure()
    registry.health("glm5.3").mark_quota_exhausted(NOW + timedelta(minutes=30))
    data = {item["id"]: item for item in TestClient(app).get("/v1/registry").json()["data"]}
    assert data["qwen3.6"]["state"]["failures"] == 1
    assert data["glm5.3"]["state"]["available"] is False
    assert data["glm5.3"]["state"]["quota_exhausted_until"] is not None
    assert data["deepseek-v4-flash"]["state"]["available"] is True


@respx.mock
def test_registry_endpoint_never_leaks_secrets() -> None:
    # A real client with a canary key: neither the key nor the credentialed URL
    # may appear anywhere in the response.
    canary = "sk-CANARY-9911"
    respx.get(URL).mock(return_value=httpx.Response(200, json=live_catalog()))
    client = CatalogClient(client=httpx.AsyncClient(), api_key=canary)
    app = create_app(registry=Registry(load_card(), fetcher=client.fetch))
    response = TestClient(app).get("/v1/registry")
    assert response.status_code == 200
    assert canary not in response.text
    assert "api.nan.builders" not in response.text
    assert "authorization" not in response.text.lower()


@respx.mock
def test_registry_endpoint_keeps_prompt_text_out() -> None:
    # No prompt field exists anywhere in the payload, whatever the catalog sends.
    payload = live_catalog()
    payload["data"][0]["last_prompt"] = "the user's entire conversation"
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))
    client = CatalogClient(client=httpx.AsyncClient(), api_key="sk-test")
    app = create_app(registry=Registry(load_card(), fetcher=client.fetch))
    body = TestClient(app).get("/v1/registry").json()
    text = str(body)
    assert "last_prompt" not in text
    assert "conversation" not in text


def test_registry_endpoint_survives_a_catalog_failure() -> None:
    async def broken_fetch() -> tuple[CatalogEntry, ...]:
        raise CatalogUnavailable("catalog is down")

    app = create_app(registry=Registry(load_card(), fetcher=broken_fetch))
    response = TestClient(app).get("/v1/registry")
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert "qwen3.6" in ids  # degraded to the card inventory, never empty-by-crash


def test_registry_endpoint_excludes_non_chat_models() -> None:
    app, _ = make_app()
    ids = [item["id"] for item in TestClient(app).get("/v1/registry").json()["data"]]
    assert "flux-2-klein" not in ids
    assert "qwen-image-2.1" not in ids
    assert "qwen3-embedding" not in ids
