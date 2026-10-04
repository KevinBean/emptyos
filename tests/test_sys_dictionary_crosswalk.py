"""System tests: Dictionary — vowel↔spelling crosswalk endpoint + page.

Kept in its own file rather than appended to test_sys_dictionary.py so the two
can be reviewed and run independently.

The unit half (tests/test_unit_dictionary_crosswalk.py) pins the data itself and
needs no daemon; this half pins the wiring — that the route registered, the
shape survives HTTP, and the page renders without JS errors.
"""

import pytest

from page_helpers import assert_no_js_errors, switch_tab, wait_briefly


@pytest.mark.api
class TestCrosswalkAPI:
    def test_endpoint_registers(self, http_client):
        """A bound helper whose binding line is missing 404s here, not at import."""
        assert http_client.get("/dictionary/api/crosswalk").status_code == 200

    def test_shape(self, http_client):
        d = http_client.get("/dictionary/api/crosswalk").json()
        for key in ("vowels", "graphemes", "edges", "totals", "joined"):
            assert key in d, f"missing {key}"
        assert d["totals"]["vowels"] == len(d["vowels"])
        assert d["totals"]["edges"] == len(d["edges"])
        assert d["totals"]["graphemes"] == len(d["graphemes"])

    def test_every_edge_resolves_over_http(self, http_client):
        d = http_client.get("/dictionary/api/crosswalk").json()
        ids = {v["id"] for v in d["vowels"]}
        gids = {g["id"] for g in d["graphemes"]}
        for e in d["edges"]:
            assert e["vowel"] in ids
            assert e["grapheme"] in gids

    def test_vowels_carry_the_render_contract(self, http_client):
        """The page lays sounds out on the IPA quadrilateral and hues them by
        backness — both need these fields present on every non-rhotic vowel."""
        d = http_client.get("/dictionary/api/crosswalk").json()
        for v in d["vowels"]:
            assert v["group"] in ("front", "central", "back")
            assert isinstance(v["spellings"], int) and v["spellings"] > 0
            assert isinstance(v["errors"], int)
            if not v.get("rhotic"):
                assert isinstance(v.get("x"), (int, float))
                assert isinstance(v.get("y"), (int, float))

    def test_examples_survive_the_json_boundary(self, http_client):
        d = http_client.get("/dictionary/api/crosswalk").json()
        assert all((e.get("examples") or "").strip() for e in d["edges"])

    def test_ipa_survives_the_json_boundary(self, http_client):
        """Mojibake here would make every label unreadable — and it is exactly
        the bug that shipped in the standalone version before a render check."""
        d = http_client.get("/dictionary/api/crosswalk").json()
        ipa = {v["ipa"] for v in d["vowels"]}
        assert "ɪ" in ipa and "æ" in ipa and "ə" in ipa and "ɔɪ" in ipa

    def test_kb_route_is_registered_without_firing_it(self, http_client):
        """Asserted through the OpenAPI schema on purpose: POSTing it would
        publish 21 real notes into the operator's actual vault, and a test has
        no business deciding to do that. The write path itself is covered by
        the pure body renderer in the unit suite."""
        paths = http_client.get("/openapi.json").json().get("paths", {})
        assert "/dictionary/api/crosswalk/kb" in paths
        assert "post" in paths["/dictionary/api/crosswalk/kb"]

    def test_errors_are_non_negative_and_rhotic_never_joins(self, http_client):
        d = http_client.get("/dictionary/api/crosswalk").json()
        for v in d["vowels"]:
            assert v["errors"] >= 0
            if v.get("rhotic"):
                assert v["errors"] == 0, "vowel+/r/ has no single ARPABET phone to join"


@pytest.mark.interactive
class TestCrosswalkUI:
    def test_page_renders_the_graph(self, page, base_url, page_errors):
        page.goto(f"{base_url}/dictionary/pages/crosswalk.html")
        page.wait_for_selector("#graph .node", timeout=15000)
        assert page.locator("#graph .node").count() > 20
        assert page.locator("#graph .edge").count() > 100
        assert_no_js_errors(page_errors)

    def test_theme_class_is_applied(self, page, base_url):
        """Secondary pages/ files bypass the kernel's theme injection, so this
        page ships its own bootstrap. Without it every token resolves to nothing."""
        page.goto(f"{base_url}/dictionary/pages/crosswalk.html")
        wait_briefly(page)
        cls = page.evaluate("document.documentElement.className")
        assert "theme-" in cls

    def test_clicking_a_sound_focuses_and_clears(self, page, base_url, page_errors):
        page.goto(f"{base_url}/dictionary/pages/crosswalk.html")
        page.wait_for_selector("#graph .node", timeout=15000)
        page.evaluate(
            "document.querySelector('.node[data-type=\"p\"][data-id=\"uw\"]')"
            ".dispatchEvent(new MouseEvent('click',{bubbles:true}))"
        )
        assert page.locator("#graph.dimmed").count() == 1
        assert page.locator(".p-head").count() == 1
        page.keyboard.press("Escape")
        assert page.locator("#graph.dimmed").count() == 0
        assert_no_js_errors(page_errors)

    def test_dictionary_page_links_to_the_crosswalk(self, page, base_url):
        """Without this the page is reachable only by typing the URL or via the
        hub panel, which is invisible until the pronounce pipeline has data —
        so a fresh install would have no way to find it at all."""
        page.goto(f"{base_url}/dictionary/")
        # The link lives on the Practice tab, which is not the default one —
        # asserting without switching only proves it exists in hidden markup.
        assert switch_tab(page, "practice"), "could not open the Practice tab"
        link = page.locator('a[href="/dictionary/pages/crosswalk.html"]')
        assert link.count() == 1
        assert link.first.is_visible()

    def test_no_horizontal_overflow_on_mobile(self, page, base_url):
        page.set_viewport_size({"width": 375, "height": 760})
        page.goto(f"{base_url}/dictionary/pages/crosswalk.html")
        wait_briefly(page)
        over = page.evaluate(
            "document.documentElement.scrollWidth - window.innerWidth")
        assert over <= 0, f"page scrolls sideways by {over}px at 375w"

    def test_accordion_replaces_the_graph_on_mobile(self, page, base_url):
        """Narrow viewports get a real stacked list, not a squashed SVG."""
        page.set_viewport_size({"width": 375, "height": 760})
        page.goto(f"{base_url}/dictionary/pages/crosswalk.html")
        page.wait_for_selector("#acc details", timeout=15000)
        assert page.locator("#acc details").count() > 15
        assert page.locator(".canvas").is_visible() is False
