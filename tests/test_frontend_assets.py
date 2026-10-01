"""Frontend assets are served from the static root under strict rules.

The admin console is split into HTML + CSS + ES modules, so the backend needs a
narrow asset endpoint. These tests pin its safety properties: only whitelisted
extensions under ``web/static/`` are served, with the declared media type and the same
``no-store`` policy as the console page.
"""

import asyncio
import re

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

from app.config.configuration import PROJECT_ROOT
from app.transport import admin as admin_module


@pytest.fixture
def client(admin):
    return TestClient(Starlette(routes=admin.routes))


def _web_root(tmp_path, monkeypatch, name, content=b"body{}"):
    root = tmp_path / "web"
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    monkeypatch.setattr(admin_module, "ADMIN_ASSET_ROOT", root.resolve())


def _request(path_params):
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/admin/console/static/x",
        "path_params": path_params,
        "headers": [],
        "query_string": b"",
    })


def test_allowed_asset_served_with_no_store(client, tmp_path, monkeypatch):
    _web_root(tmp_path, monkeypatch, "app.css", b"body{color:red}")
    response = client.get("/admin/console/static/app.css")
    assert response.status_code == 200
    assert response.text == "body{color:red}"
    assert response.headers["content-type"].startswith("text/css")
    assert response.headers["cache-control"] == "no-store"


def test_javascript_asset_uses_javascript_media_type(client, tmp_path, monkeypatch):
    _web_root(tmp_path, monkeypatch, "js/core.js", b"export const a=1;")
    response = client.get("/admin/console/static/js/core.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_disallowed_extension_is_not_served(client, tmp_path, monkeypatch):
    _web_root(tmp_path, monkeypatch, "secret.html", b"<h1>x</h1>")
    assert client.get("/admin/console/static/secret.html").status_code == 404


def test_missing_asset_is_404(client):
    assert client.get("/admin/console/static/missing.css").status_code == 404


def test_real_web_root_does_not_serve_the_console_html(client):
    assert client.get("/admin/console/static/index.html").status_code == 404


def test_path_traversal_is_rejected_by_handler(admin, tmp_path, monkeypatch):
    _web_root(tmp_path, monkeypatch, "app.css", b"body{}")
    (tmp_path / "secret.css").write_text("secret", encoding="utf-8")
    response = asyncio.run(admin.handle_asset(_request({"asset": "../secret.css"})))
    assert response.status_code == 404


def test_console_references_loadable_assets(client):
    html = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="(/admin/console/static/[^"]+)"', html)
    assert refs, "console should reference split assets"
    for ref in refs:
        assert client.get(ref).status_code == 200, ref
