"""Unit tests for Carrier Compare's Phase-0 Fast Courier probe.

Live calculator automation is intentionally absent here. It is an explicit
operator smoke through a leased EmptyOS sandbox, never a normal pytest gate.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from helpers import app_path

# Resolved by app id, not by track path: a hardcoded
# apps/extension/<group>/<id> breaks on any regroup and surfaces as a pytest
# COLLECTION error, which aborts the entire suite rather than failing one test.
# This file did exactly that when the app moved out of extension/labs.
APP_DIR = app_path("carrier-compare")
_SPEC = importlib.util.spec_from_file_location("carrier_compare_fast_courier", APP_DIR / "fast_courier.py")
assert _SPEC and _SPEC.loader
FC = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(FC)


def _card(
    *,
    carrier: str = "Example Carrier",
    service: str = "Road Express",
    price: str = "$13.64",
    eta: str = "Estimated Transit Time\n1-4 business days",
    collection: str = "Collection\nToday",
) -> dict:
    return {
        "text": f"{carrier}\n{service}\n{price}\n{eta}\n{collection}",
        "image_alts": [carrier],
    }


def test_parse_offer_preserves_source_price_and_carrier():
    offers = FC.parse_offer_cards([_card()], captured_at="2026-07-15T00:00:00+00:00")
    assert len(offers) == 1
    assert offers[0]["source"] == "Fast Courier"
    assert offers[0]["displayed_carrier"] == "Example Carrier"
    assert offers[0]["price"] == "13.64"
    assert offers[0]["currency"] == "AUD"


def test_parse_eta_range_exactly():
    offer = FC.parse_offer_cards([_card()], captured_at="now")[0]
    assert offer["eta_min_days"] == 1
    assert offer["eta_max_days"] == 4
    assert "1-4 business days" in offer["eta_raw"]


def test_parse_single_day_eta():
    offer = FC.parse_offer_cards(
        [_card(eta="Estimated Transit Time\n1 business day")], captured_at="now"
    )[0]
    assert offer["eta_min_days"] == 1
    assert offer["eta_max_days"] == 1


def test_missing_ambiguous_fields_remain_null_and_flagged():
    card = {
        "text": "$18.20\nEstimated Transit Time\n2 business days",
        "image_alts": ["Carrier A"],
    }
    offer = FC.parse_offer_cards([card], captured_at="now")[0]
    assert offer["service"] is None
    assert offer["collection_raw"] is None
    assert "missing_service" in offer["anomaly_flags"]
    assert "missing_collection" in offer["anomaly_flags"]


def test_malformed_price_is_not_invented():
    assert FC.parse_offer_cards([_card(price="$call us")], captured_at="now") == []


def test_duplicate_dom_discoveries_are_collapsed():
    offers = FC.parse_offer_cards([_card(), _card()], captured_at="now")
    assert len(offers) == 1


def test_multiple_carriers_remain_separate_offers():
    offers = FC.parse_offer_cards(
        [
            _card(carrier="Carrier A", price="$10.00"),
            _card(carrier="Carrier B", service="Standard", price="$12.50"),
        ],
        captured_at="now",
    )
    assert [row["displayed_carrier"] for row in offers] == ["Carrier A", "Carrier B"]


def test_carrier_name_containing_express_is_not_used_as_service():
    offer = FC.parse_offer_cards(
        [_card(carrier="Direct Freight Express", service="Road Express")], captured_at="now"
    )[0]
    assert offer["displayed_carrier"] == "Direct Freight Express"
    assert offer["service"] == "Road Express"


def test_price_basis_is_only_claimed_when_evidence_says_it():
    assert FC.parse_price_basis("Total includes GST and all surcharges") == (
        "GST included",
        "surcharges included",
    )
    assert FC.parse_price_basis("Quoted total") == (None, None)


def test_evidence_sanitizer_redacts_contact_data_and_bounds_output():
    text = "Contact test@example.com or 0412 345 678 " + ("x" * 60_000)
    sanitized = FC.sanitize_evidence(text)
    assert "test@example.com" not in sanitized
    assert "0412 345 678" not in sanitized
    assert len(sanitized) == 48_000


@pytest.mark.asyncio
async def test_every_adapter_call_pins_playwright_and_context():
    calls = []

    class FakeApp:
        async def browse(self, action, **kwargs):
            calls.append((action, kwargs))
            return {"ok": True}

    adapter = FC.FastCourierAdapter(FakeApp(), run=SimpleNamespace(run_id="run-1"))
    await adapter._browse("navigate", url=FC.CALCULATOR_URL)

    assert calls == [
        (
            "navigate",
            {
                "context_id": "carrier-compare:run-1:fast_courier",
                "only_provider": "playwright",
                "url": FC.CALCULATOR_URL,
            },
        )
    ]


def test_calculator_target_is_fixed_and_not_user_supplied():
    assert FC.CALCULATOR_URL == "https://fcapp.fastcourier.com.au/"
    assert FC.SOURCE_ID == "fast_courier"
