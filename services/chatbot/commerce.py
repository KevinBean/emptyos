"""Normalized catalogue storage and deterministic product discovery."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .security import validate_public_url


# Feeds spell availability inconsistently; these are the values that mean "buyable".
IN_STOCK = {"in_stock", "in stock", "available"}


def _data_dir() -> Path:
    path = Path(os.environ.get("CHATBOT_DATA_DIR", "./data")) / "catalogs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _catalog_path(site_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", site_id)
    if not safe or safe != site_id:
        raise ValueError("invalid site id")
    return _data_dir() / f"{safe}.json"


def normalize_product(raw: dict) -> dict:
    product_id = str(raw.get("id") or raw.get("sku") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not product_id or not title:
        raise ValueError("product id and title are required")
    price = raw.get("price", {})
    if isinstance(price, dict):
        amount = price.get("amount")
        currency = price.get("currency") or raw.get("currency") or "AUD"
    else:
        amount, currency = price, raw.get("currency") or "AUD"
    amount = None if amount in (None, "") else round(float(amount), 2)
    attrs = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
    for key in ("brand", "sku", "description"):
        if raw.get(key):
            attrs.setdefault(key, raw[key])
    return {
        "id": product_id,
        "title": title,
        "category": str(raw.get("category") or "").strip(),
        "url": str(raw.get("url") or "").strip(),
        "image_url": str(raw.get("image_url") or "").strip(),
        "price": {"amount": amount, "currency": str(currency).upper()},
        "availability": str(raw.get("availability") or "unknown").lower(),
        "attributes": attrs,
        "variants": raw.get("variants") if isinstance(raw.get("variants"), list) else [],
        "related_product_ids": raw.get("related_product_ids") if isinstance(raw.get("related_product_ids"), list) else [],
        "updated_at": str(raw.get("updated_at") or datetime.now(UTC).isoformat()),
    }


def save_catalog(site_id: str, products: list[dict]) -> dict:
    normalized = [normalize_product(p) for p in products]
    ids = [p["id"] for p in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("product ids must be unique")
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "products": normalized,
    }
    path = _catalog_path(site_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return payload


def load_catalog(site_id: str) -> dict:
    path = _catalog_path(site_id)
    if not path.exists():
        return {"schema_version": 1, "generated_at": None, "products": []}
    return json.loads(path.read_text(encoding="utf-8"))


async def ensure_catalog_feed(site_id: str, url: str, *, stale_seconds: int = 3600) -> dict:
    path = _catalog_path(site_id)
    if path.exists() and time.time() - path.stat().st_mtime < stale_seconds:
        return load_catalog(site_id)
    allow_local = os.environ.get("CHATBOT_ALLOW_LOCAL_SOURCES") == "1"
    current = validate_public_url(url, allow_local=allow_local)
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        for _ in range(5):
            response = await client.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location: raise ValueError("catalog redirect missing location")
                current = validate_public_url(urljoin(current, location), allow_local=allow_local)
                continue
            response.raise_for_status()
            if len(response.content) > 5_000_000: raise ValueError("catalog feed exceeds 5 MB")
            raw = response.json()
            products = raw.get("products") if isinstance(raw, dict) else raw
            if not isinstance(products, list): raise ValueError("catalog feed must be a list or {products: []}")
            return save_catalog(site_id, products)
    raise ValueError("too many catalog redirects")


def product_search(site_id: str, query: str, *, limit: int = 4) -> list[dict]:
    products = load_catalog(site_id).get("products", [])
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 1}
    budget_match = re.search(r"(?:under|below|less than|max(?:imum)?|budget)\s*\$?([0-9]+(?:\.[0-9]+)?)", query.lower())
    budget = float(budget_match.group(1)) if budget_match else None
    scored = []
    for product in products:
        amount = product.get("price", {}).get("amount")
        if budget is not None and amount is not None and float(amount) > budget:
            continue
        # `or ""` not a get() default: a present-but-null field yields None,
        # which the default never catches and .lower() would crash on.
        title = (product.get("title") or "").lower()
        haystack = " ".join([
            title, (product.get("category") or ""),
            json.dumps(product.get("attributes", {}), ensure_ascii=False),
        ]).lower()
        score = sum(3 if word in title else 1 for word in words if word in haystack)
        if product.get("availability") in IN_STOCK:
            score += 1
        scored.append((score, product))
    scored.sort(key=lambda pair: (-pair[0], pair[1].get("price", {}).get("amount") or 10**12, pair[1].get("title") or ""))
    return [product for score, product in scored[:limit] if score > 0 or not words]


def cheapest_in_stock(site_id: str, *, limit: int = 4) -> list[dict]:
    """Lowest-priced in-stock products. The feed carries no promotion flag, so
    this is what we can honestly offer for a 'what's on sale' question."""
    products = load_catalog(site_id).get("products", [])
    priced = [
        p for p in products
        if p.get("price", {}).get("amount") is not None
        and p.get("availability") in IN_STOCK
    ]
    priced.sort(key=lambda p: (float(p["price"]["amount"]), p.get("title") or ""))
    return priced[:limit]


def cart_summary_reply(items: list[dict]) -> dict:
    """Compose the cart reply from already-joined rows
    ({product_id, title, quantity, amount, currency}). Pure — the caller owns
    the ledger read and the catalogue join."""
    if not items:
        return {
            "intent": "commerce.cart",
            "reply": (
                "Your chat cart is empty — anything you add from the product cards here will show up. "
                "The store's own cart page stays the source of truth at checkout."
            ),
            "blocks": [],
        }
    currency = next((i.get("currency") for i in items if i.get("currency")), "AUD")
    total = sum(float(i.get("amount") or 0) * int(i.get("quantity") or 1) for i in items)
    count = sum(int(i.get("quantity") or 1) for i in items)
    return {
        "intent": "commerce.cart",
        "reply": (
            f"You've added {count} item{'s' if count != 1 else ''} through this chat. "
            "Prices come from the product feed — open the store's cart page to check out and see the final total."
        ),
        "blocks": [{
            "type": "cart_summary",
            "items": items,
            "total": {"amount": round(total, 2), "currency": currency},
        }],
    }


def commerce_reply(site_id: str, query: str) -> dict | None:
    q = query.lower()
    if any(term in q for term in ("where is my order", "track my order", "order status")):
        return {
            "intent": "commerce.order",
            "reply": "I can check the order system without sending these details to the language model.",
            "blocks": [{"type": "secure_action_form", "action": "order.lookup", "title": "Track an order", "fields": [{"name": "order_number", "label": "Order number", "type": "text"}, {"name": "email", "label": "Order email", "type": "email"}]}],
        }
    if any(term in q for term in ("return", "refund", "cancel order", "exchange")):
        return {
            "intent": "commerce.return",
            "reply": "I can check eligibility, but a team member must approve and create any return, refund, cancellation, or exchange.",
            "blocks": [{"type": "secure_action_form", "action": "return.check", "title": "Check return eligibility", "fields": [{"name": "order_number", "label": "Order number", "type": "text"}, {"name": "email", "label": "Order email", "type": "email"}]}],
        }
    if any(term in q for term in ("human", "person", "support", "helpdesk", "agent")):
        return {
            "intent": "commerce.handoff",
            "reply": "I can create a handoff for the store team. Your contact details go directly to the configured helpdesk, not the language model.",
            "blocks": [{"type": "secure_action_form", "action": "handoff.create", "title": "Ask the team", "fields": [{"name": "name", "label": "Name", "type": "text"}, {"name": "email", "label": "Email", "type": "email"}, {"name": "message", "label": "How can we help?", "type": "text"}]}],
        }
    # Cart is a marker only — main.py owns the ledger read + catalogue join and
    # composes the final reply via cart_summary_reply(), keeping this module pure.
    # Word-boundary match: a bare `"cart" in q` also fires on "cartridge",
    # which is a real product family in this catalogue.
    if re.search(r"\b(cart|basket|trolley)\b", q) or "shopping list" in q:
        return {"intent": "commerce.cart", "reply": "", "blocks": []}
    if re.search(r"\b(sale|discount|discounted|deal|deals|special|specials|clearance|promo|promotion)\b", q):
        products = cheapest_in_stock(site_id)
        if not products:
            return {"intent": "commerce.sale", "reply": "I couldn't find in-stock products in the current catalogue.", "blocks": []}
        return {
            "intent": "commerce.sale",
            "reply": (
                "The product feed doesn't flag promotions, so I can't tell you what's on sale — "
                "check the store's specials page for that. Here are the lowest-priced in-stock options."
            ),
            "blocks": [{"type": "product_grid", "products": products}],
        }
    catalogue_terms = {"buy", "price", "stock", "printer", "ink", "toner", "compare", "product", "recommend", "under", "wifi", "duplex", "colour", "color", "mono", "cheapest", "cheap", "budget", "scanner", "label"}
    if not any(term in q for term in catalogue_terms):
        return None
    products = product_search(site_id, query)
    if not products:
        return {"intent": "commerce.search", "reply": "I couldn't find a matching in-stock product in the current catalogue.", "blocks": []}
    return {
        "intent": "commerce.search",
        "reply": f"I found {len(products)} catalogue option{'s' if len(products) != 1 else ''}. Prices and availability below come from the latest product feed.",
        "blocks": [{"type": "product_grid", "products": products}],
    }


def demo_printer_products(count: int = 120) -> list[dict]:
    """Deterministic synthetic catalogue for the controlled pilot."""
    families = [
        ("Mono Laser Printer", "mono", "laser", 169),
        ("Colour Laser Printer", "colour", "laser", 289),
        ("Home Inkjet Printer", "colour", "inkjet", 89),
        ("Business Ink Tank Printer", "colour", "ink tank", 319),
        ("Compact Label Printer", "mono", "thermal", 109),
        ("Document Scanner", "colour", "scanner", 229),
        ("Black Toner Cartridge", "mono", "toner", 69),
        ("Colour Ink Multipack", "colour", "ink", 54),
    ]
    products = []
    for index in range(count):
        name, colour, technology, base = families[index % len(families)]
        tier = index // len(families) + 1
        product_id = f"office-{index + 1:03d}"
        products.append({
            "id": product_id,
            "sku": f"EOS-{index + 1:05d}",
            "title": f"{name} {100 + tier}",
            "category": "Printers" if index % len(families) < 5 else ("Scanners" if index % len(families) == 5 else "Ink & toner"),
            "url": f"#product-{product_id}",
            "price": {"amount": base + tier * 7, "currency": "AUD"},
            "availability": "in_stock" if index % 7 else "low_stock",
            "attributes": {
                "brand": "Northstar Office",
                "colour_mode": colour,
                "technology": technology,
                "wifi": index % 3 != 0,
                "duplex": index % 4 != 0,
                "recommended_use": "small office" if tier % 2 else "home office",
            },
        })
    return products
