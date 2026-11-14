"""Smoke tests for the gateway scaffold."""

from fastapi.testclient import TestClient

from nan_auto.gateway.app import app


def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_models_lists_router_aliases() -> None:
    client = TestClient(app)
    response = client.get("/v1/models")
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert ids == ["nan-auto", "nan-auto-fast", "nan-auto-quality"]
