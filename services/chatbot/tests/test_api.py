"""Admin and public commerce contract tests.

Parametrized over two mount shapes so both are pinned:
  ""         — the bare service at root paths (standalone Docker / chat.binbian.net)
  "/chatbot" — mounted under the External Lab Host, which drives the lifespan
The ``client`` fixture returns a wrapper that transparently prepends the prefix,
so each test body stays written against root-relative paths.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _PrefixClient:
    """Wraps a TestClient, prepending the mount prefix to every request path."""

    def __init__(self, inner: TestClient, prefix: str) -> None:
        self._inner = inner
        self._prefix = prefix

    def _p(self, path: str) -> str:
        return f"{self._prefix}{path}"

    def get(self, path, **kw):
        return self._inner.get(self._p(path), **kw)

    def post(self, path, **kw):
        return self._inner.post(self._p(path), **kw)

    def put(self, path, **kw):
        return self._inner.put(self._p(path), **kw)


@pytest.fixture(params=["", "/chatbot"], ids=["root", "mounted"])
def client(request, tmp_path, monkeypatch):
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
    from chatbot import main
    main = importlib.reload(main)

    prefix = request.param
    if prefix:
        import contextlib

        @contextlib.asynccontextmanager
        async def _parent_lifespan(_app):
            # Mirror the External Lab Host: drive the mounted app's lifecycle,
            # since Starlette does not propagate lifespan to mounted sub-apps.
            await main.chatbot_init()
            try:
                yield
            finally:
                await main.chatbot_shutdown()

        target = FastAPI(lifespan=_parent_lifespan)
        target.mount(prefix, main.app)
    else:
        target = main.app

    with TestClient(target) as test_client:
        yield _PrefixClient(test_client, prefix)


def admin_headers():
    return {"X-Admin-Token": "test-token"}


def test_admin_requires_token(client):
    assert client.get("/admin/sites").status_code == 401


def test_legacy_site_is_publish_owned_and_commerce_off(client):
    site = client.get("/admin/sites", headers=admin_headers()).json()["sites"][0]
    assert site["managed_by"] == "publish"
    assert site["commerce_enabled"] is False


def test_create_studio_site_as_disabled_draft(client):
    response = client.post("/admin/sites", headers=admin_headers(), json={
        "id": "shop", "name": "Shop", "allowed_origins": ["https://shop.example"], "enabled": False,
    })
    assert response.status_code == 200
    site = response.json()["site"]
    assert site["managed_by"] == "studio"
    assert site["enabled"] is False


def test_studio_cannot_overwrite_publish_owned_site(client):
    response = client.put("/admin/sites/legacy", headers=admin_headers(), json={"name": "Changed"})
    assert response.status_code == 409


def test_demo_seed_creates_120_products(client):
    response = client.post("/admin/demo/printer-store", headers=admin_headers())
    assert response.status_code == 200
    assert response.json()["products"] == 120


def test_commerce_chat_returns_typed_product_block_without_model(client):
    client.post("/admin/demo/printer-store", headers=admin_headers())
    response = client.post("/chat", headers={"Origin": "https://pilot.example"}, json={
        "site_id": "printer-pilot", "session_id": "s1", "messages": [{"role": "user", "content": "wifi printer under $300"}],
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "commerce"
    assert payload["blocks"][0]["type"] == "product_grid"
    assert all(p["price"]["amount"] <= 300 for p in payload["blocks"][0]["products"])


def test_cart_action_is_idempotent_and_requires_allowlist(client):
    client.post("/admin/demo/printer-store", headers=admin_headers())
    body = {"site_id": "printer-pilot", "session_id": "s1", "action": "cart.add", "idempotency_key": "cart-key-123", "args": {"product_id": "office-001", "quantity": 1}}
    first = client.post("/commerce/action", headers={"Origin": "https://pilot.example"}, json=body)
    second = client.post("/commerce/action", headers={"Origin": "https://pilot.example"}, json=body)
    assert first.status_code == 200
    assert second.json()["idempotent_replay"] is True


def _add_to_cart(client, *, session_id, product_id="office-001", key="cart-seed-1", quantity=1):
    return client.post("/commerce/action", headers={"Origin": "https://pilot.example"}, json={
        "site_id": "printer-pilot", "session_id": session_id, "action": "cart.add",
        "idempotency_key": key, "args": {"product_id": product_id, "quantity": quantity},
    })


def _ask(client, question, *, session_id):
    return client.post("/chat", headers={"Origin": "https://pilot.example"}, json={
        "site_id": "printer-pilot", "session_id": session_id,
        "messages": [{"role": "user", "content": question}],
    }).json()


def test_cart_question_answers_from_the_session_mirror(client):
    client.post("/admin/demo/printer-store", headers=admin_headers())
    _add_to_cart(client, session_id="s1")
    payload = _ask(client, "check my cart", session_id="s1")
    assert payload["source"] == "commerce"  # deterministic lane, no model spend
    block = payload["blocks"][0]
    assert block["type"] == "cart_summary"
    assert [i["product_id"] for i in block["items"]] == ["office-001"]


def test_cart_is_scoped_to_the_asking_session(client):
    client.post("/admin/demo/printer-store", headers=admin_headers())
    _add_to_cart(client, session_id="s1")
    payload = _ask(client, "check my cart", session_id="s2")
    assert payload["source"] == "commerce"
    assert payload["blocks"] == []
    assert "empty" in payload["reply"].lower()


def test_idempotent_replay_does_not_double_the_mirrored_quantity(client):
    client.post("/admin/demo/printer-store", headers=admin_headers())
    _add_to_cart(client, session_id="s1", key="cart-dupe-key")
    _add_to_cart(client, session_id="s1", key="cart-dupe-key")  # same key → replay
    block = _ask(client, "check my cart", session_id="s1")["blocks"][0]
    assert block["items"][0]["quantity"] == 1


def test_demo_action_errors_survive_the_worker_thread(client):
    """_demo_action runs via asyncio.to_thread; its HTTPExceptions must still
    reach the client as real status codes, not a 500."""
    client.post("/admin/demo/printer-store", headers=admin_headers())
    unknown = _add_to_cart(client, session_id="s1", product_id="does-not-exist", key="missing-key-1")
    assert unknown.status_code == 404
    bad_order = client.post("/commerce/action", headers={"Origin": "https://pilot.example"}, json={
        "site_id": "printer-pilot", "action": "order.lookup", "idempotency_key": "bad-order-1", "args": {},
    })
    assert bad_order.status_code == 400


def test_failed_add_leaves_the_cart_untouched(client):
    client.post("/admin/demo/printer-store", headers=admin_headers())
    _add_to_cart(client, session_id="s1", product_id="does-not-exist", key="missing-key-2")
    payload = _ask(client, "check my cart", session_id="s1")
    assert payload["blocks"] == []  # a 404 add must never mirror


def test_sale_question_stays_in_the_deterministic_lane(client):
    client.post("/admin/demo/printer-store", headers=admin_headers())
    payload = _ask(client, "what is on sale", session_id="s1")
    assert payload["source"] == "commerce"
    assert payload["blocks"][0]["type"] == "product_grid"


def test_sensitive_order_inputs_are_not_returned(client):
    client.post("/admin/demo/printer-store", headers=admin_headers())
    body = {"site_id": "printer-pilot", "action": "order.lookup", "idempotency_key": "order-key-123", "args": {"order_number": "SECRET-ORDER", "email": "private@example.com"}}
    payload = client.post("/commerce/action", headers={"Origin": "https://pilot.example"}, json=body).json()
    assert "SECRET-ORDER" not in str(payload)
    assert "private@example.com" not in str(payload)


def test_cart_action_omits_dead_cart_url(client):
    client.post("/admin/demo/printer-store", headers=admin_headers())
    body = {"site_id": "printer-pilot", "session_id": "s1", "action": "cart.add", "idempotency_key": "cart-nokey", "args": {"product_id": "office-001", "quantity": 1}}
    payload = client.post("/commerce/action", headers={"Origin": "https://pilot.example"}, json=body).json()
    assert "cart_url" not in payload  # the /demo-store/cart.html page never existed


def test_root_landing_is_prefix_aware(client):
    payload = client.get("/").json()
    assert payload["service"] == "emptyos-chatbot"
    base = client._prefix  # "" at root, "/chatbot" mounted
    assert payload["endpoints"]["health"] == f"{base}/health"
    assert payload["endpoints"]["demo"] == f"{base}/demo-store/"


def test_demo_store_links_are_prefix_aware(client):
    # The demo-store HTML must carry the mount prefix on its own asset URLs so it
    # renders correctly whether served at root (standalone) or under /chatbot.
    html = client.get("/demo-store/").text
    base = client._prefix  # "" at root, "/chatbot" mounted
    assert f"'{base}/demo-store/catalog.json'" in html
    assert f'src="{base}/widget/chatbot-widget.js"' in html
    assert f'location.origin+"{base}"' in html
    assert "{{BASE}}" not in html  # placeholder fully substituted


def test_site_meta_serves_the_presentation_fields_the_widget_binds_to(client):
    # The widget fetches this endpoint on boot and reads these exact keys to
    # paint its starter chips, empty state, and accent. Renaming one here is a
    # silent widget regression — no Python test would otherwise go red — so the
    # field names are pinned, not just the status code.
    client.post("/admin/sites", headers=admin_headers(), json={
        "id": "shop", "name": "Shop",
        "allowed_origins": ["https://shop.example"],
        "starter_questions": ["Q1", "Q2"],
        "welcome_message": "Hello there.",
        "brand_color": "#112233",
    })
    payload = client.get("/sites/shop/meta").json()
    assert payload["starter_questions"] == ["Q1", "Q2"]
    assert payload["welcome_message"] == "Hello there."
    assert payload["brand_color"] == "#112233"


def test_site_meta_is_public_and_leaks_no_operator_config(client):
    # It is fetched cross-origin by the widget with no admin token, so it must
    # stay readable — and must never carry the operator-only surface next to it
    # in the same SiteConfig (allowed_origins, corpus_url, provider keys).
    response = client.get("/sites/legacy/meta")
    assert response.status_code == 200
    payload = response.json()
    for leaked in ("allowed_origins", "corpus_url", "provider", "api_key", "allowed_actions"):
        assert leaked not in payload


def test_site_meta_404s_for_unknown_site(client):
    assert client.get("/sites/nope/meta").status_code == 404


def test_site_meta_is_cached_like_the_widget_script(client):
    # Fetched once per pageview on every embedding site. It caches for the same
    # window as chatbot-widget.js so the pair propagates together; dropping this
    # header silently turns every pageview into an origin hit.
    response = client.get("/sites/legacy/meta")
    assert response.headers["cache-control"] == "public, max-age=300"


def test_site_meta_404_is_not_cached(client):
    # A 404 here means the site was not created yet. Caching it would keep a
    # freshly-created site invisible to its own widget for the full window.
    response = client.get("/sites/nope/meta")
    assert response.status_code == 404
    assert "max-age" not in response.headers.get("cache-control", "")
