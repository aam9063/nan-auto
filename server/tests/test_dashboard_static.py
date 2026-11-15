"""The gateway serves the built dashboard when a dist directory is mounted.

Deployment (NaN Cloud Spaces) runs one container: the FastAPI gateway and the
Vite-built SPA must share an origin, because the dashboard calls the API over
relative `/v1` paths. Unknown SPA paths fall back to `index.html`.

Each test points `DEFAULT_DIST_DIR` at an absent path so the ambient checkout's
real `web/dist` (present locally, absent in CI) cannot leak into the result.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from nan_auto.gateway import app as app_module
from nan_auto.gateway.app import create_app, mount_dashboard


def _without_ambient_dist(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(app_module, "DEFAULT_DIST_DIR", tmp_path / "absent-dist")


def _write_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><body>nan-auto dashboard</body></html>",
        encoding="utf-8",
    )
    return dist


def test_serves_index_html_from_mounted_dist(monkeypatch, tmp_path: Path) -> None:
    _without_ambient_dist(monkeypatch, tmp_path)
    app = create_app()
    mount_dashboard(app, _write_dist(tmp_path))
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert "nan-auto dashboard" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_api_routes_win_over_the_static_mount(monkeypatch, tmp_path: Path) -> None:
    _without_ambient_dist(monkeypatch, tmp_path)
    app = create_app()
    mount_dashboard(app, _write_dist(tmp_path))
    client = TestClient(app)

    response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_no_mount_without_a_dist(monkeypatch, tmp_path: Path) -> None:
    _without_ambient_dist(monkeypatch, tmp_path)
    app = create_app()
    mount_dashboard(app, tmp_path / "does-not-exist")
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 404


def test_mounts_the_default_dist_when_present(monkeypatch, tmp_path: Path) -> None:
    _without_ambient_dist(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "DEFAULT_DIST_DIR", _write_dist(tmp_path))
    app = create_app()
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
