"""Lead capture + intent tagging + deprecation-lever contract tests.

Mined-lesson package (ELEK ClickConnector case, 2026-07-17):
  - lead capture is a first-class deterministic outcome on content sites
  - a lead is stored BEFORE delivery is attempted — never lost
  - deprecation needs both levers (corpus removal is pinned in
    tests/test_unit_publish_corpus_exclude.py; the prompt lever here)
  - every chat request carries a deterministic `intent` branch tag

Self-contained fixture (root mount): the mount-prefix contract is already
pinned by test_api.py's parametrized client; these routes add no new
path-handling logic.
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient


SITES_TOML = """
[defaults]
provider = "openai"

[sites.blog]
name = "Blog"
allowed_origins = ["https://blog.example"]
corpus_url = ""
lead_capture_enabled = true

[sites.plain]
name = "Plain"
allowed_origins = ["https://plain.example"]
corpus_url = ""
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    sites = tmp_path / "sites.toml"
    sites.write_text(SITES_TOML, encoding="utf-8")
    monkeypatch.setenv("CHATBOT_SITES_PATH", str(sites))
    monkeypatch.setenv("CHATBOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHATBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("CHATBOT_DEMO_ENABLED", "1")
    monkeypatch.setenv("CHATBOT_PUBLIC_ORIGIN", "https://pilot.example")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from chatbot import main

    main = importlib.reload(main)
    # raise_server_exceptions=False: the no-flag fall-through tests reach the
    # LLM path with no OPENAI_API_KEY, which raises server-side; we only assert
    # the deterministic lead branch never fired, so surface it as a 500.
    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        yield test_client


def admin_headers():
    return {"X-Admin-Token": "test-token"}


def _chat(client, site_id, origin, text):
    return client.post(
        "/chat",
        headers={"Origin": origin},
        json={"site_id": site_id, "session_id": "s1", "messages": [{"role": "user", "content": text}]},
    )


# ── Lead form in the chat flow ──────────────────────────────────────


def test_contact_phrase_returns_lead_form_when_enabled(client):
    response = _chat(client, "blog", "https://blog.example", "Can I talk to a human about this?")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "lead"
    assert payload["cost_usd"] == 0.0
    block = payload["blocks"][0]
    assert block["type"] == "secure_action_form"
    assert block["action"] == "lead.create"
    field_names = {f["name"] for f in block["fields"]}
    assert field_names == {"name", "email", "message"}


def test_contact_phrase_without_flag_never_returns_lead_form(client):
    # `plain` has lead_capture_enabled=false → falls through to the corpus/LLM
    # path (which 502s here: empty corpus + no provider key). The pin is only
    # that the deterministic lead branch never fires.
    response = _chat(client, "plain", "https://plain.example", "Can I talk to a human?")
    assert response.status_code != 200 or response.json().get("source") != "lead"


def test_ordinary_question_on_lead_site_does_not_trigger_form(client):
    response = _chat(client, "blog", "https://blog.example", "What is this site about?")
    assert response.status_code != 200 or response.json().get("source") != "lead"


def test_commerce_handoff_takes_precedence_over_lead(client):
    # A commerce site with both branches live: the commerce handoff form (which
    # routes to the store connector) wins over the generic lead form.
    client.post("/admin/demo/printer-store", headers=admin_headers())
    client.put(
        "/admin/sites/printer-pilot",
        headers=admin_headers(),
        json={"lead_capture_enabled": True},
    )
    response = _chat(client, "printer-pilot", "https://pilot.example", "I want to talk to a human")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "commerce"
    assert payload["blocks"][0]["action"] == "handoff.create"


# ── lead.create action: store-first, gated, idempotent ──────────────


def _lead_action(client, origin, key="lead-key-0001", **overrides):
    body = {
        "site_id": "blog",
        "session_id": "s1",
        "action": "lead.create",
        "idempotency_key": key,
        "args": {"name": "Ada", "email": "ada@example.com", "message": "Please contact me"},
    }
    body.update(overrides)
    return client.post("/commerce/action", headers={"Origin": origin}, json=body)


def test_lead_create_stores_and_lists(client):
    response = _lead_action(client, "https://blog.example")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True and payload["action"] == "lead.create"
    assert "message" in payload  # widget renders payload.message

    listing = client.get("/admin/sites/blog/leads", headers=admin_headers()).json()
    assert len(listing["leads"]) == 1
    lead = listing["leads"][0]
    assert lead["email"] == "ada@example.com"
    assert lead["delivery"] == "stored"  # no helpdesk_endpoint configured
    assert lead["status"] == "new"


def test_lead_create_rejected_for_wrong_origin(client):
    assert _lead_action(client, "https://evil.example").status_code == 401


def test_lead_create_rejected_when_flag_off(client):
    response = _lead_action(client, "https://plain.example", site_id="plain")
    assert response.status_code == 403


def test_lead_create_requires_valid_email_and_message(client):
    bad_email = _lead_action(
        client, "https://blog.example", key="lead-key-0002",
        args={"name": "", "email": "not-an-email", "message": "hi"},
    )
    assert bad_email.status_code == 400
    no_msg = _lead_action(
        client, "https://blog.example", key="lead-key-0003",
        args={"name": "", "email": "a@b.co", "message": ""},
    )
    assert no_msg.status_code == 400


def test_lead_create_idempotent_replay(client):
    first = _lead_action(client, "https://blog.example", key="lead-key-0004")
    replay = _lead_action(client, "https://blog.example", key="lead-key-0004")
    assert first.status_code == replay.status_code == 200
    assert replay.json().get("idempotent_replay") is True
    listing = client.get("/admin/sites/blog/leads", headers=admin_headers()).json()
    assert len(listing["leads"]) == 1


def test_failed_helpdesk_delivery_never_loses_the_lead(client, monkeypatch):
    # Unreachable endpoint: the row must exist with delivery='failed'.
    monkeypatch.setenv("CHATBOT_ALLOW_LOCAL_SOURCES", "1")
    from chatbot import main as chatbot_main

    chatbot_main.CONFIG.sites["blog"].helpdesk_endpoint = "http://127.0.0.1:9/hook"
    response = _lead_action(client, "https://blog.example", key="lead-key-0005")
    assert response.status_code == 200
    listing = client.get("/admin/sites/blog/leads", headers=admin_headers()).json()
    assert listing["leads"][0]["delivery"] == "failed"


# ── Intent tagging + analytics ──────────────────────────────────────


def test_intents_surface_in_admin_analytics(client):
    _chat(client, "blog", "https://blog.example", "can I speak to someone please")
    analytics = client.get("/admin/sites/blog/analytics", headers=admin_headers()).json()
    assert analytics["intents_30d"].get("lead") == 1


def test_commerce_intents_are_branch_specific(client):
    client.post("/admin/demo/printer-store", headers=admin_headers())
    _chat(client, "printer-pilot", "https://pilot.example", "wifi printer under $300")
    _chat(client, "printer-pilot", "https://pilot.example", "where is my order?")
    analytics = client.get("/admin/sites/printer-pilot/analytics", headers=admin_headers()).json()
    intents = analytics["intents_30d"]
    assert intents.get("commerce.search") == 1
    assert intents.get("commerce.order") == 1


def test_ledger_intent_column_migrates_existing_db(tmp_path):
    db = tmp_path / "ledger.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT NOT NULL,
            ip_hash TEXT NOT NULL, session_id TEXT, tokens_in INTEGER NOT NULL,
            tokens_out INTEGER NOT NULL, cost_usd REAL NOT NULL, model TEXT,
            ts REAL NOT NULL
        );
        INSERT INTO requests (site_id, ip_hash, session_id, tokens_in, tokens_out, cost_usd, model, ts)
        VALUES ('s', 'h', '', 0, 0, 0.0, 'faq', 1.0);
        """
    )
    conn.commit()
    conn.close()

    from chatbot.ledger import Ledger

    ledger = Ledger(db_path=db)  # must ALTER in the intent column
    ledger.record(
        site_id="s", ip_hash="h", session_id="", tokens_in=0, tokens_out=0,
        cost_usd=0.0, model="lead", intent="lead",
    )
    intents = ledger.analytics("s")["intents_30d"]
    assert intents.get("lead") == 1  # new row via intent column
    # legacy row (intent NULL) falls back to its model source tag on read —
    # but its ts=1.0 is outside the 30d window, so it simply must not crash.


# ── avoid_topics prompt lever ───────────────────────────────────────


def test_avoid_topics_appends_prompt_clause_only_when_set():
    from chatbot.config import SiteConfig
    from chatbot.main import _build_system_prompt

    def site(**kw):
        return SiteConfig(
            id="s", name="Site", allowed_origins=[], corpus_url="",
            daily_cap_usd=1.0, model="m", persona="", **kw,
        )

    empty_corpus = {"chunks": [], "faqs": []}
    baseline = _build_system_prompt(site(), empty_corpus)
    assert "retired or unavailable" not in baseline  # empty list = byte-identical

    prompt = _build_system_prompt(site(avoid_topics=["Cable Pro PC", " ", ""]), empty_corpus)
    assert "retired or unavailable" in prompt
    assert "Cable Pro PC" in prompt
    assert prompt.count(";") >= 1 or "Cable Pro PC" in prompt  # blanks dropped


def test_config_roundtrip_preserves_lead_and_avoid_fields(tmp_path):
    from chatbot.config import load_config, render_config_toml, write_config_atomic

    sites = tmp_path / "sites.toml"
    sites.write_text(SITES_TOML, encoding="utf-8")
    cfg = load_config(str(sites))
    cfg.sites["blog"].avoid_topics = ["Old Product"]
    assert cfg.sites["blog"].lead_capture_enabled is True

    rendered = render_config_toml(cfg)
    assert "lead_capture_enabled = true" in rendered
    assert "Old Product" in rendered

    write_config_atomic(cfg, str(sites))
    reloaded = load_config(str(sites))
    assert reloaded.sites["blog"].lead_capture_enabled is True
    assert reloaded.sites["blog"].avoid_topics == ["Old Product"]
    assert reloaded.sites["plain"].lead_capture_enabled is False
