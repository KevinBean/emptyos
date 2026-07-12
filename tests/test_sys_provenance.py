"""Browser contract for the opt-in auto-provenance response decorator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "emptyos/web/static/eos-provenance.js"

# An essential app (ESSENTIAL_APPS in app_loader), so the page always exists.
# `hello-world` is retired — it 404s, which silently voided the injection test.
HOST_PAGE = "/hub/"

_STUB_UI = """() => {
    document.body.innerHTML = '<div id="host"><div id="sink" data-ai-output="/prov-test/api/"></div></div>';
    window.EOS_UI = window.EOS_UI || {};
    window.EOS_UI.provenance = function(p) {
        return '<span class="test-prov" title="' + (p.title || '') + '">' +
               p.mode + ' \\u00b7 ' + (p.provider || '') + ' \\u00b7 ' + (p.model || '') + '</span>';
    };
    return null;
}"""


def _mount(page, base_url):
    page.goto(f"{base_url}{HOST_PAGE}", wait_until="domcontentloaded")
    page.evaluate(_STUB_UI)
    page.add_script_tag(path=str(SCRIPT))


@pytest.mark.interactive
def test_runtime_mounts_survives_rerenders_updates_and_clears(page, base_url):
    responses = {
        "/prov-test/api/answer": {
            "provenance": {"mode": "cloud", "provider": "remote", "model": "small", "latency_ms": 42}
        },
        "/prov-test/api/answer2": {
            "provenance": {"mode": "local", "provider": "on-device", "model": "compact", "citations": ["a"]}
        },
    }

    def handle(route):
        path = route.request.url.split(base_url, 1)[-1]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(responses[path]))

    page.route("**/prov-test/api/**", handle)
    _mount(page, base_url)

    page.evaluate("fetch('/prov-test/api/answer').then(r => r.json())")
    page.locator("#sink + .eos-auto-provenance").wait_for()
    assert "cloud · remote · small" in page.locator(".test-prov").inner_text()
    assert "42 ms" in page.locator(".test-prov").get_attribute("title")

    # Repainting inside the sink cannot erase its sibling chip.
    page.evaluate("document.querySelector('#sink').innerHTML = '<p>new answer</p>'")
    assert page.locator("#sink + .eos-auto-provenance").count() == 1

    # Replacing the sink and chip together is repaired from the short ring cache.
    page.evaluate(
        "document.querySelector('#host').innerHTML = "
        "'<div id=\"sink\" data-ai-output=\"/prov-test/api/\"></div>'"
    )
    page.locator("#sink + .eos-auto-provenance").wait_for()

    page.evaluate("fetch('/prov-test/api/answer2').then(r => r.json())")
    page.wait_for_function("document.querySelector('.test-prov').textContent.indexOf('local') !== -1")
    assert "1 citation" in page.locator(".test-prov").get_attribute("title")


@pytest.mark.interactive
def test_same_endpoint_without_provenance_clears_the_chip(page, base_url):
    """A retraction from the painting endpoint drops the attribution."""
    state = {"prov": True}

    def handle(route):
        body = (
            {"provenance": {"mode": "cloud", "provider": "p", "model": "m"}}
            if state["prov"]
            else {"ok": True}
        )
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/prov-test/api/answer", handle)
    _mount(page, base_url)

    page.evaluate("fetch('/prov-test/api/answer').then(r => r.json())")
    page.locator("#sink + .eos-auto-provenance").wait_for()

    state["prov"] = False
    page.evaluate("fetch('/prov-test/api/answer').then(r => r.json())")
    page.wait_for_function("!document.querySelector('.eos-auto-provenance')")


@pytest.mark.interactive
def test_unrelated_api_call_does_not_erase_the_chip(page, base_url):
    """Regression: a `data-ai-output=""` sink matches every /api/ path, so an
    ordinary chrome call (GET /api/health) used to clear a valid attribution."""
    page.route(
        "**/prov-test/api/answer",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"provenance":{"mode":"cloud","provider":"p","model":"m"}}',
        ),
    )
    page.goto(f"{base_url}{HOST_PAGE}", wait_until="domcontentloaded")
    page.evaluate(
        """() => {
        document.body.innerHTML = '<div id="sink" data-ai-output=""></div>';
        window.EOS_UI = window.EOS_UI || {};
        window.EOS_UI.provenance = p => '<span class="test-prov">' + p.mode + '</span>';
        }"""
    )
    page.add_script_tag(path=str(SCRIPT))

    page.evaluate("fetch('/prov-test/api/answer').then(r => r.json())")
    page.locator("#sink + .eos-auto-provenance").wait_for()

    page.evaluate("fetch('/api/health').then(r => r.json())")
    page.wait_for_timeout(400)
    assert page.locator("#sink + .eos-auto-provenance").count() == 1, \
        "an unrelated /api/ response erased the provenance chip"


@pytest.mark.interactive
def test_ndjson_stream_is_never_parsed(page, base_url):
    """Falsifiable by construction: the stream is served from the *same* endpoint
    that painted the chip (only that endpoint may retract it) and its body carries
    NO provenance. Drop the content-type guard and the chip is cleared, failing
    this test. A stream on a different path, or one embedding a provenance object,
    would make the assertion unfailable."""

    def handle(route):
        if "stream=1" in route.request.url:
            route.fulfill(status=200, content_type="application/x-ndjson", body='{"ok":true}\n')
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"provenance":{"mode":"cloud","provider":"p","model":"m"}}',
        )

    page.route("**/prov-test/api/answer*", handle)
    _mount(page, base_url)

    page.evaluate("fetch(new Request('/prov-test/api/answer')).then(r => r.json())")
    page.locator("#sink + .eos-auto-provenance").wait_for()

    page.evaluate("window.__streamDone = false; "
                  "fetch('/prov-test/api/answer?stream=1').then(r => r.text())"
                  ".then(() => window.__streamDone = true)")
    page.wait_for_function("window.__streamDone === true")
    page.wait_for_timeout(300)
    assert page.locator("#sink + .eos-auto-provenance").count() == 1, \
        "the NDJSON stream was parsed and retracted the chip"


@pytest.mark.interactive
def test_chip_paints_from_the_pages_own_load_time_fetch(page, base_url, http_client):
    """The real end-to-end: server-injected runtime, real EOS_UI.provenance, and a
    fetch the *page itself* issues while loading. A deferred runtime wraps
    window.fetch too late and this silently paints nothing.
    """
    page.route(
        "**/reflect/api/reflect/insights",
        lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"insights":[{"pattern":"load-time","severity":"low"}],'
                 '"provenance":{"mode":"cloud","provider":"openai","model":"gpt-4o-mini",'
                 '"latency_ms":123}}',
        ),
    )
    current = http_client.get("/settings/api/get?key=ui.auto_provenance").json()
    old_value = current.get("value") if isinstance(current, dict) else None
    try:
        http_client.post("/settings/api/set", json={"key": "ui.auto_provenance", "value": True})
        page.goto(f"{base_url}/reflect/", wait_until="load")
        assert page.locator('script[src="/static/eos-provenance.js"]').count() == 1
        chip = page.locator("#insights + .eos-auto-provenance .eos-badge-provenance")
        chip.wait_for(timeout=8000)
        text = chip.inner_text()
        assert "cloud" in text and "openai" in text, text
        assert "123 ms" in (chip.get_attribute("title") or "")
        assert "eos-badge-provenance-cloud" in (chip.get_attribute("class") or "")
    finally:
        http_client.post("/settings/api/set",
                         json={"key": "ui.auto_provenance", "value": old_value})


@pytest.mark.interactive
def test_server_injection_follows_live_setting(page, base_url, http_client):
    current = http_client.get("/settings/api/get?key=ui.auto_provenance").json()
    old_value = current.get("value") if isinstance(current, dict) else None
    try:
        # Assert BOTH directions inside the try, so the test does not depend on
        # whatever the operator's ambient flag value happens to be.
        set_result = http_client.post(
            "/settings/api/set", json={"key": "ui.auto_provenance", "value": True}
        )
        assert set_result.status_code == 200
        page.goto(f"{base_url}{HOST_PAGE}", wait_until="domcontentloaded")
        assert page.locator('script[src="/static/eos-provenance.js"]').count() == 1

        http_client.post("/settings/api/set", json={"key": "ui.auto_provenance", "value": False})
        page.goto(f"{base_url}{HOST_PAGE}", wait_until="domcontentloaded")
        assert page.locator('script[src="/static/eos-provenance.js"]').count() == 0
    finally:
        http_client.post("/settings/api/set", json={"key": "ui.auto_provenance", "value": old_value})
