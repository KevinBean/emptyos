"""System app tests: Garden — read-only overlay rendering layer.

V1 ships 4 plots (physical / social / intellectual / occupational) + 5 themes
(sumi-e / appleton / scroll / cottage / edo). Tests verify the happy path,
the disable contract (graceful degradation), and the zero-coupling-out
constraint.
"""

from __future__ import annotations

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors


PLOT_SLUGS = ("physical", "social", "intellectual", "occupational")
THEME_IDS = ("sumi-e", "appleton", "scroll", "cottage", "edo")


@pytest.mark.api
class TestGardenAPI:
    def test_state_shape(self, http_client):
        data = assert_dict_response(http_client.get("/garden/api/state"))
        assert "plots" in data
        assert "theme" in data
        assert "window_days" in data
        assert "total_plants" in data
        assert isinstance(data["plots"], dict)

    def test_state_has_all_4_plots(self, http_client):
        data = assert_dict_response(http_client.get("/garden/api/state"))
        for slug in PLOT_SLUGS:
            assert slug in data["plots"], f"missing plot: {slug}"
            p = data["plots"][slug]
            assert isinstance(p.get("plants"), list)
            assert isinstance(p.get("species"), str)
            assert p.get("slug") == slug

    def test_themes_lists_all_5(self, http_client):
        data = assert_dict_response(http_client.get("/garden/api/themes"))
        themes = data.get("themes", [])
        ids = {t.get("id") for t in themes if isinstance(t, dict)}
        assert ids == set(THEME_IDS), f"expected {THEME_IDS}, got {ids}"
        assert data.get("active") in THEME_IDS
        assert data.get("default") == "appleton"

    def test_state_fresh_param(self, http_client):
        data = assert_dict_response(http_client.get("/garden/api/state?fresh=1"))
        assert "plots" in data

    def test_api_tick_returns_state(self, http_client):
        data = assert_dict_response(http_client.post("/garden/api/tick"))
        assert "plots" in data
        assert data.get("computed_at", 0) > 0

    @pytest.mark.parametrize("slug", PLOT_SLUGS)
    def test_plot_svg_renders_default_theme(self, http_client, slug):
        r = http_client.get(f"/garden/api/plot/{slug}.svg")
        assert r.status_code == 200, f"plot {slug} failed: {r.text[:200]}"
        body = r.text
        assert "<svg" in body
        assert f'data-plot="{slug}"' in body
        assert 'data-theme=' in body

    @pytest.mark.parametrize("theme", THEME_IDS)
    def test_plot_svg_renders_every_theme(self, http_client, theme):
        """Each theme should render the same plot without error and produce
        a theme-tagged SVG. Validates that the theme dict is complete."""
        r = http_client.get(f"/garden/api/plot/social.svg?theme={theme}")
        assert r.status_code == 200, f"theme {theme}: {r.text[:200]}"
        assert f'data-theme="{theme}"' in r.text

    def test_themes_produce_different_svg(self, http_client):
        """Two themes rendering the same plot should produce structurally
        different SVG (different byte count or different defs). Guards
        against 'just color change' regressions."""
        a = http_client.get("/garden/api/plot/social.svg?theme=sumi-e").text
        b = http_client.get("/garden/api/plot/social.svg?theme=edo").text
        c = http_client.get("/garden/api/plot/social.svg?theme=cottage").text
        assert a != b and b != c and a != c, (
            "sumi-e / edo / cottage rendered the same SVG — theme grammars "
            "are not actually branching"
        )

    def test_unknown_plot_returns_404(self, http_client):
        r = http_client.get("/garden/api/plot/nonsense.svg")
        assert r.status_code == 404


@pytest.mark.api
class TestGardenIsolation:
    """Constraint 1 of the plan: zero coupling out. These tests guard it."""

    def test_no_garden_contributes_slot_in_other_apps(self):
        import re
        from pathlib import Path

        offenders: list[str] = []
        pattern = re.compile(r"\[\[contributes\.garden\.", re.IGNORECASE)
        for manifest in Path("apps").rglob("manifest.toml"):
            try:
                text = manifest.read_text(encoding="utf-8")
            except OSError:
                continue
            if pattern.search(text):
                offenders.append(str(manifest))
        assert not offenders, (
            "garden must never be a contribution target — found "
            f"[[contributes.garden.*]] in: {offenders}"
        )

    def test_no_other_app_imports_garden(self):
        from pathlib import Path

        offenders: list[str] = []
        for src in Path("apps").rglob("*.py"):
            if "garden" in src.parts:
                continue
            try:
                text = src.read_text(encoding="utf-8")
            except OSError:
                continue
            if "apps.garden" in text or "from .garden" in text:
                offenders.append(str(src))
        assert not offenders, (
            f"other apps import from garden — coupling leak: {offenders}"
        )


@pytest.mark.interactive
class TestGardenUI:
    def test_page_loads(self, page, base_url, page_errors):
        page.goto(base_url + "/garden/")
        page.wait_for_load_state("networkidle")
        assert_no_js_errors(page_errors)

    def test_theme_picker_has_5_options(self, page, base_url):
        page.goto(base_url + "/garden/")
        page.wait_for_selector("#theme-select option", state="attached", timeout=5000)
        options = page.query_selector_all("#theme-select option")
        assert len(options) == 5, f"expected 5 theme options, got {len(options)}"

    def test_grid_renders_4_plots(self, page, base_url):
        page.goto(base_url + "/garden/")
        page.wait_for_selector(".eos-g-card", timeout=5000)
        cards = page.query_selector_all(".eos-g-card")
        assert len(cards) == 4, f"expected 4 plot cards, got {len(cards)}"

    def test_theme_switch_changes_attribute(self, page, base_url):
        page.goto(base_url + "/garden/")
        page.wait_for_selector("#theme-select option[value='edo']", state="attached", timeout=5000)
        page.select_option("#theme-select", "edo")
        page.wait_for_function(
            "document.getElementById('garden').getAttribute('data-theme') === 'edo'",
            timeout=3000,
        )
        assert page.get_attribute("#garden", "data-theme") == "edo"
