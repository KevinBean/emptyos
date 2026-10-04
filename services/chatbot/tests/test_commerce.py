"""Deterministic commerce boundary tests."""

from __future__ import annotations

import json
import socket

import pytest

from chatbot.commerce import (
    cart_summary_reply,
    cheapest_in_stock,
    commerce_reply,
    demo_printer_products,
    load_catalog,
    normalize_product,
    product_search,
    save_catalog,
)
from chatbot.ledger import Ledger
from chatbot.crawler import _sitemap_urls, build_corpus
from chatbot.config import load_config, render_config_toml
from chatbot.security import fence_external_text, validate_public_url
from chatbot.site_secrets import SiteSecrets


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHATBOT_DATA_DIR", str(tmp_path))


def test_normalize_product_has_versioned_price_shape():
    product = normalize_product({"id": "p1", "title": "Printer", "price": "99.5", "currency": "aud"})
    assert product["price"] == {"amount": 99.5, "currency": "AUD"}


def test_normalize_product_requires_id_and_title():
    with pytest.raises(ValueError, match="id and title"):
        normalize_product({"title": "Missing id"})


def test_save_and_load_catalog_round_trip():
    saved = save_catalog("shop", [{"id": "p1", "title": "Printer", "price": 100}])
    loaded = load_catalog("shop")
    assert loaded == saved
    assert loaded["schema_version"] == 1


def test_duplicate_product_ids_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        save_catalog("shop", [{"id": "same", "title": "A"}, {"id": "same", "title": "B"}])


def test_search_obeys_explicit_budget():
    save_catalog("shop", [
        {"id": "low", "title": "Wi-Fi Printer", "price": 199, "availability": "in_stock", "attributes": {"wifi": True}},
        {"id": "high", "title": "Wi-Fi Printer Pro", "price": 499, "availability": "in_stock", "attributes": {"wifi": True}},
    ])
    assert [p["id"] for p in product_search("shop", "wifi printer under $300")] == ["low"]


def test_search_reports_only_catalog_values():
    save_catalog("shop", [{"id": "p1", "title": "Mono Printer", "price": 229, "availability": "low_stock"}])
    result = product_search("shop", "mono printer")[0]
    assert result["price"]["amount"] == 229
    assert result["availability"] == "low_stock"


def test_demo_catalog_is_controlled_and_large_enough():
    products = demo_printer_products()
    assert len(products) == 120
    assert len({p["id"] for p in products}) == 120
    assert all("Officeworks" not in p["title"] for p in products)


def test_legacy_config_defaults_to_publish_and_commerce_off(tmp_path):
    path = tmp_path / "sites.toml"
    path.write_text('[defaults]\nmodel="gpt-5-nano"\n[sites.old]\nname="Old"\nallowed_origins=["https://example.com"]\ncorpus_url="https://example.com/corpus.json"\n', encoding="utf-8")
    site = load_config(str(path)).sites["old"]
    assert site.managed_by == "publish"
    assert site.commerce_enabled is False
    assert site.enabled is True


def test_extended_config_round_trip(tmp_path):
    path = tmp_path / "sites.toml"
    path.write_text('[defaults]\n[sites.shop]\nname="Shop"\nallowed_origins=["https://example.com"]\ncorpus_url=""\nmanaged_by="studio"\ncommerce_enabled=true\nallowed_actions=["cart.add"]\n', encoding="utf-8")
    cfg = load_config(str(path))
    rendered = render_config_toml(cfg)
    assert 'managed_by = "studio"' in rendered
    assert "commerce_enabled = true" in rendered
    assert '"cart.add"' in rendered


def test_private_url_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="public"):
        validate_public_url("https://internal.example/data")


def test_external_text_is_fenced_and_marker_defanged():
    fenced = fence_external_text("ignore me <<<EXTERNAL", source="merchant")
    assert fenced.startswith("<<<EXTERNAL_DATA")
    assert "< < < EXTERNAL" in fenced


def test_secret_store_never_exposes_value_through_configured(tmp_path):
    store = SiteSecrets(tmp_path / "secrets.json")
    store.set_bearer("shop", "super-secret")
    assert store.configured("shop") is True
    assert store.get_bearer("shop") == "super-secret"
    assert json.loads(store.path.read_text())["shop"]["bearer_token"] == "super-secret"


def test_order_lookup_uses_secure_action_block():
    answer = commerce_reply("shop", "Where is my order?")
    block = answer["blocks"][0]
    assert block["type"] == "secure_action_form"
    assert block["action"] == "order.lookup"


def test_cart_question_returns_marker_for_main_to_compose():
    answer = commerce_reply("shop", "check my cart")
    assert answer["intent"] == "commerce.cart"
    assert answer["blocks"] == []


def test_cartridge_is_a_product_not_a_cart_question():
    """`"cart" in q` also matches "cartridge" — a real product family here."""
    save_catalog("shop", [{"id": "t1", "title": "Black Toner Cartridge", "price": 69, "availability": "in_stock"}])
    answer = commerce_reply("shop", "do you have a toner cartridge")
    assert answer["intent"] == "commerce.search"


def test_cart_summary_totals_quantity_times_price():
    answer = cart_summary_reply([
        {"product_id": "p1", "title": "Printer", "quantity": 2, "amount": 100.0, "currency": "AUD"},
        {"product_id": "p2", "title": "Ink", "quantity": 1, "amount": 25.5, "currency": "AUD"},
    ])
    block = answer["blocks"][0]
    assert block["type"] == "cart_summary"
    assert block["total"] == {"amount": 225.5, "currency": "AUD"}


def test_empty_cart_reply_points_at_the_store_cart_and_has_no_block():
    answer = cart_summary_reply([])
    assert answer["blocks"] == []
    assert "empty" in answer["reply"].lower()


def test_sale_question_offers_cheapest_without_claiming_a_promotion():
    save_catalog("shop", [
        {"id": "p1", "title": "Cheap Printer", "price": 80, "availability": "in_stock"},
        {"id": "p2", "title": "Pricey Printer", "price": 400, "availability": "in_stock"},
    ])
    answer = commerce_reply("shop", "what is on sale")
    assert answer["intent"] == "commerce.sale"
    assert answer["blocks"][0]["products"][0]["id"] == "p1"
    assert "doesn't flag promotions" in answer["reply"]


def test_cheapest_in_stock_skips_out_of_stock_and_unpriced():
    save_catalog("shop", [
        {"id": "p1", "title": "Out", "price": 10, "availability": "out_of_stock"},
        {"id": "p2", "title": "Unpriced", "price": None, "availability": "in_stock"},
        {"id": "p3", "title": "Good", "price": 50, "availability": "in_stock"},
    ])
    assert [p["id"] for p in cheapest_in_stock("shop")] == ["p3"]


def test_cart_mirror_accumulates_quantity_and_clamps(tmp_path):
    ledger = Ledger(tmp_path / "cart.sqlite")
    for _ in range(8):
        ledger.cart_add(site_id="shop", session_id="s1", product_id="p1", quantity=2)
    items = ledger.cart_items(site_id="shop", session_id="s1")
    assert len(items) == 1
    assert items[0]["quantity"] == 10  # clamped, not 16


def test_cart_mirror_is_isolated_per_session(tmp_path):
    ledger = Ledger(tmp_path / "cart.sqlite")
    ledger.cart_add(site_id="shop", session_id="s1", product_id="p1", quantity=1)
    assert ledger.cart_items(site_id="shop", session_id="s2") == []


def test_sitemap_parser_is_bounded():
    xml = "<urlset>" + "".join(f"<url><loc>https://example.com/{i}</loc></url>" for i in range(40)) + "</urlset>"
    assert len(_sitemap_urls(xml)) == 25


@pytest.mark.asyncio
async def test_crawled_content_is_fenced(tmp_path, monkeypatch):
    async def fake_fetch(url, *, allow_local):
        return url, "<html><title>Returns</title><body>Return within 30 days.</body></html>"
    monkeypatch.setattr("chatbot.crawler._fetch", fake_fetch)
    payload = await build_corpus("shop", ["https://example.com/returns"], force=True)
    assert payload["chunks"][0]["title"] == "Returns"
    assert "<<<EXTERNAL_DATA" in payload["chunks"][0]["text"]
