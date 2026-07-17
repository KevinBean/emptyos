"""External Lab Host contract tests: identity, module discovery, mounted-lifespan,
public-only CORS, prefix-aware demo store, and legacy redirect."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    sites = tmp_path / "sites.toml"
    sites.write_text(
        '[defaults]\nprovider="openai"\n[sites.legacy]\nname="Legacy"\nallowed_origins=["https://legacy.example"]\ncorpus_url=""\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CHATBOT_SITES_PATH", str(sites))
    monkeypatch.setenv("CHATBOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHATBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("CHATBOT_DEMO_ENABLED", "1")
    monkeypatch.setenv("CHATBOT_PUBLIC_ORIGIN", "https://pilot.example")

    import chatbot.main
    import external_lab.main

    importlib.reload(chatbot.main)
    importlib.reload(external_lab.main)  # re-binds the reloaded chatbot app + lifecycle

    with TestClient(external_lab.main.app) as test_client:
        yield test_client


def test_health_identity_and_modules(client):
    payload = client.get("/health").json()
    assert payload["service"] == "emptyos-external-lab"
    assert payload["status"] == "ok"
    ids = {m["id"] for m in payload["modules"]}
    assert {"chatbot", "printer-store"} <= ids


def test_root_landing(client):
    payload = client.get("/").json()
    assert payload["service"] == "emptyos-external-lab"
    assert payload["entries"]["chatbot"] == "/chatbot/health"


def test_chatbot_root_landing_through_mount(client):
    # A bare /chatbot/ now returns the chatbot landing (prefix-aware) instead of 404.
    payload = client.get("/chatbot/").json()
    assert payload["service"] == "emptyos-chatbot"
    assert payload["endpoints"]["health"] == "/chatbot/health"


def test_lifespan_drives_mounted_chatbot(client):
    # If the host's lifespan didn't call chatbot_init, this would 500 (globals None).
    payload = client.get("/chatbot/health").json()
    assert payload["status"] == "ok"
    assert payload["sites"] == ["legacy"]


def test_cors_present_on_public_routes(client):
    resp = client.get("/chatbot/health")
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_cors_absent_on_admin_routes(client):
    # Admin is server-to-server (X-Admin-Token); no CORS surface. 401 without token.
    resp = client.get("/chatbot/admin/sites")
    assert resp.status_code == 401
    assert "access-control-allow-origin" not in resp.headers


def test_demo_store_links_target_mounted_chatbot(client):
    html = client.get("/demos/printer-store/").text
    assert "/chatbot/demo-store/catalog.json" in html
    assert 'src="/chatbot/widget/chatbot-widget.js"' in html
    assert 'location.origin+"/chatbot"' in html
    assert "{{BASE}}" not in html


def test_legacy_demo_store_redirects(client):
    resp = client.get("/demo-store/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/demos/printer-store/"
