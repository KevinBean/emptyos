"""surface-layer-screen — API and UI system tests.

Needs the daemon. Every UI assertion maps to a `UX-*` requirement in
`INTENT.md`, because the visibility requirements are most of this app's reason
to exist: the arithmetic is four lines, and what a sheet of paper cannot do is
show you that the verdict's sign turns on one input you guessed.
"""

from __future__ import annotations

import pytest

BASE = "/surface-layer-screen"

# CIGRE TB 963 §7.1 Case Study 1, absolute-loss form.
TB963 = {
    "burial_depth_m": 1.0,
    "cable_outer_diameter_m": 0.10,
    "rho_soil": 1.0,
    "layers": [{"thickness_m": 0.10, "rho": 1.33}],
    "sources": [
        {"name": "Phase 3", "offset_m": 0.0, "weight": 49.9},
        {"name": "Phase 1", "offset_m": 0.3, "weight": 35.2},
        {"name": "Phase 2", "offset_m": 0.6, "weight": 38.1},
    ],
    "absolute_losses": True,
    "theta_c": 90.0,
    "theta_amb": 20.0,
    "threshold_pct": 1.0,
}

WEIGHTS = {
    **TB963,
    "absolute_losses": False,
    "external_share": 0.679,
    "sources": [
        {"name": "Phase 3", "offset_m": 0.0, "weight": 1.0},
        {"name": "Phase 1", "offset_m": 0.3, "weight": 0.705},
        {"name": "Phase 2", "offset_m": 0.6, "weight": 0.763},
    ],
}


@pytest.mark.api
class TestSurfaceLayerScreenAPI:
    def test_schema_exposes_the_spec(self, http_client):
        r = http_client.get(f"{BASE}/api/schema").json()
        props = r["schema"]["properties"]
        assert {"burial_depth_m", "layers", "sources", "external_share"} <= set(props)
        assert r["defaults"]["absolute_losses"] is False

    def test_reference_case_matches_the_brochure(self, http_client):
        r = http_client.get(f"{BASE}/api/reference-case").json()
        i = r["inputs"]
        assert r["case_id"] == "cigre-tb963-case-1"
        assert i["burial_depth_m"] == 1.0
        assert i["layers"][0]["rho"] == 1.33
        assert [s["weight"] for s in i["sources"]] == [49.9, 35.2, 38.1]

    def test_reference_case_screens_to_the_expected_figures(self, http_client):
        r = http_client.post(f"{BASE}/api/screen", json=TB963).json()
        assert r["delta_l_m"] == pytest.approx(0.033, abs=1e-6)
        assert r["external_share"] == pytest.approx(0.679, abs=2e-3)
        assert r["rating_change_pct"] == pytest.approx(-0.438, abs=5e-3)
        assert r["verdict"] == "exclude"

    def test_weight_mode_agrees_with_absolute_mode(self, http_client):
        a = http_client.post(f"{BASE}/api/screen", json=TB963).json()
        w = http_client.post(f"{BASE}/api/screen", json=WEIGHTS).json()
        assert w["rating_change_pct"] == pytest.approx(a["rating_change_pct"], abs=2e-3)

    def test_weight_mode_ignores_the_overall_loss_scale(self, http_client):
        """The defect the weight mode exists to remove (ALG-SCALE-01)."""
        base = http_client.post(f"{BASE}/api/screen", json=WEIGHTS).json()["rating_change_pct"]
        scaled = {**WEIGHTS, "sources": [
            {**s, "weight": s["weight"] * 37.0} for s in WEIGHTS["sources"]]}
        assert http_client.post(f"{BASE}/api/screen", json=scaled).json()["rating_change_pct"] \
            == pytest.approx(base, rel=1e-9)

    def test_absolute_mode_does_not(self, http_client):
        base = http_client.post(f"{BASE}/api/screen", json=TB963).json()["rating_change_pct"]
        scaled = {**TB963, "sources": [
            {**s, "weight": s["weight"] * 2.0} for s in TB963["sources"]]}
        got = http_client.post(f"{BASE}/api/screen", json=scaled).json()["rating_change_pct"]
        assert got == pytest.approx(2 * base, rel=0.02)

    def test_a_conductive_layer_improves_the_rating(self, http_client):
        """RELEASE-VERIFICATION journey step 5, run on the case it names.

        This used to run rho 0.4 over soil 1.2 — same mechanism and same sign,
        but a made-up pair, so the step claimed the asphalt case and executed a
        different one. The literal pair is a 100 mm layer at 0.8 over the
        fixture's soil 1.0, which is what a user actually screens.

        0.8 is SUPPLIED here, never defaulted: its attribution is disputed
        (SOURCE-PACK, and the app refuses to default it), so this asserts the
        engine's behaviour given the number, not that the number is right.
        """
        payload = {**WEIGHTS, "layers": [{"thickness_m": 0.10, "rho": 0.8}]}
        assert payload["rho_soil"] == 1.0, "the pair is 0.8 over 1.0"
        r = http_client.post(f"{BASE}/api/screen", json=payload).json()
        assert r["delta_l_m"] < 0, "a conductive layer pulls the equivalent depth up"
        assert r["rating_change_pct"] > 0
        assert r["verdict"] == "improves"

        # "...and the sheet follows" — the second half of the step, which
        # nothing asserted. A verdict the API reports and the sheet contradicts
        # is the failure this exists to catch.
        sheet = http_client.post(f"{BASE}/api/report", json=payload).text
        assert "improves" in sheet.lower()

    def test_backfill_thick_stack_is_refused_with_a_named_reason(self, http_client):
        """Journey step 6: the refusal must NAME the cable crown, not just say
        'backfill'. The crown is derived (burial 1.0 m − OD/2 0.05 m = 950 mm),
        so pinning the figure pins the geometry the refusal reasons about — a
        message that only said 'backfill' would pass while computing it wrong.
        """
        payload = {**WEIGHTS, "layers": [{"thickness_m": 1.2, "rho": 1.33}]}
        r = http_client.post(f"{BASE}/api/screen", json=payload).json()
        assert "error" in r and "backfill correction" in r["error"]
        assert "top of the cable" in r["error"], "the refusal must name the crown"
        assert "950 mm" in r["error"], "and state its depth"

    def test_defaulted_external_share_is_announced(self, http_client):
        payload = {k: v for k, v in WEIGHTS.items() if k != "external_share"}
        r = http_client.post(f"{BASE}/api/screen", json=payload).json()
        assert any("defaulted" in w for w in r["warnings"])

    def test_methods_and_conformance_are_registered(self, http_client):
        m = http_client.get(f"{BASE}/api/methods").json()
        assert "equivalent_depth" in str(m)
        c = http_client.get(f"{BASE}/api/conformance").json()
        assert any(x["case_id"] == "cigre-tb963-case-1" for x in c["cases"])

    def test_conformance_passes(self, http_client):
        r = http_client.post(f"{BASE}/api/conformance/run", json={}).json()
        assert r["results"], "no conformance results returned"
        for row in r["results"]:
            assert row.get("passed") is True, row

    # REPORT-AGREE-01
    def test_report_agrees_with_the_api_and_follows_an_input_change(self, http_client):
        screen = http_client.post(f"{BASE}/api/screen", json=TB963).json()
        sheet = http_client.post(f"{BASE}/api/report", json=TB963).text
        assert f"{screen['rating_change_pct']:+.3f} %" in sheet
        assert "not a derating" in sheet
        assert "1.33" in sheet and "0.8" in sheet          # REPORT-AMBIG-01

        # Both directions. Asserting only that the OLD number is gone is
        # satisfied by any failed request — including report_response's own
        # in-band error returns, whose body contains no percentage at all.
        moved = {**TB963, "layers": [{"thickness_m": 0.30, "rho": 1.33}]}
        moved_screen = http_client.post(f"{BASE}/api/screen", json=moved).json()
        sheet2 = http_client.post(f"{BASE}/api/report", json=moved).text
        assert f"{screen['rating_change_pct']:+.3f} %" not in sheet2, \
            "the sheet still carries the old number after an input change"
        assert f"{moved_screen['rating_change_pct']:+.3f} %" in sheet2, (
            "the sheet does not carry the new number — a failed request would "
            "satisfy the negative assertion above on its own"
        )

    def test_a_refused_report_is_a_400_not_a_document(self, http_client):
        """A refusal must not be filable as the exclusion record."""
        bad = {**TB963, "layers": [{"thickness_m": 1.2, "rho": 1.33}]}
        r = http_client.post(f"{BASE}/api/report", json=bad)
        assert r.status_code == 400
        assert "backfill correction" in r.text
        assert "Surface layer screening" not in r.text


@pytest.mark.interactive
class TestSurfaceLayerScreenUI:
    """Every assertion here maps to a `UX-*` requirement in INTENT.md.

    Two Playwright rules this suite learned the hard way, both of which made
    the app look broken when it was not:

    - `inner_text()` returns *rendered* text, so a `text-transform: uppercase`
      on a `<th>` makes it `RELATIVE LOSS W`. Match case-insensitively, or use
      `text_content()` which returns the DOM text untransformed.
    - The page renders asynchronously from `slsLoadRef()`. Wait for the settled
      result (`#sl-meta`), not for a static element that exists in the served
      HTML before any JS has run.
    """

    def _ready(self, page, base_url):
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#sl-meta")          # settled, not merely present
        return page

    def test_page_loads_the_reference_case(self, page, base_url):
        pg = self._ready(page, base_url)
        assert "0.44%" in pg.inner_text(".sl-num-big")
        assert "33.0 mm" in pg.inner_text("#sl-meta")

    # UX-MODE-01
    def test_mode_switch_changes_the_loss_column_label(self, page, base_url):
        pg = self._ready(page, base_url)
        pg.check('input[name="lossmode"][value="weight"]')
        assert "relative" in pg.text_content("#wHead").lower()
        pg.check('input[name="lossmode"][value="absolute"]')
        assert "w/m" in pg.text_content("#wHead").lower()

    # UX-FEXT-01
    def test_external_share_is_input_in_weight_mode_and_output_in_absolute(self, page, base_url):
        pg = self._ready(page, base_url)
        pg.check('input[name="lossmode"][value="weight"]')
        assert pg.is_visible("#fextBox")
        pg.check('input[name="lossmode"][value="absolute"]')
        assert not pg.is_visible("#fextBox")
        assert pg.is_visible("#tempBox")
        assert "f_ext" in pg.text_content("#sl-meta").lower()

    # UX-WARN-01
    def test_a_defaulted_external_share_renders_a_warning(self, page, base_url):
        pg = self._ready(page, base_url)
        pg.check('input[name="lossmode"][value="weight"]')
        pg.fill("#fext", "0.20")
        pg.click("#run")
        pg.wait_for_function(
            "() => document.querySelector('#sl-warn') "
            "&& document.querySelector('#sl-warn').textContent.includes('0.45')"
        )

    # UX-SIGN-01
    def test_a_rating_increase_renders_with_a_single_plus(self, page, base_url):
        pg = self._ready(page, base_url)
        pg.check('input[name="lossmode"][value="weight"]')
        pg.fill("#rs", "1.2")
        pg.eval_on_selector("#layers tbody tr input[data-k='rho']", "e => { e.value = '0.4'; }")
        pg.click("#run")
        pg.wait_for_function(
            "() => document.querySelector('.sl-num-big').textContent.trim().startsWith('+')"
        )
        text = pg.inner_text(".sl-num-big")
        assert "\u2212-" not in text and "--" not in text
        assert "improves" in pg.inner_text("#result").lower()
        # UX-VERDICT-01, the fourth verdict: improves is a pass too.
        assert "eos-badge-status-pass" in pg.get_attribute(".sl-verdict .eos-badge", "class")

    # UX-REFUSE-01
    def test_a_refusal_shows_the_engines_own_message(self, page, base_url):
        """Journey step 6, on screen: the crown must reach the user.

        Waiting only for 'backfill' passed on a page that showed a generic
        refusal, which is the whole failure mode — the engine's message is
        specific and the point of surfacing it verbatim is that specificity.
        """
        pg = self._ready(page, base_url)
        pg.eval_on_selector("#layers tbody tr input[data-k='t']", "e => { e.value = '1200'; }")
        pg.click("#run")
        pg.wait_for_function(
            "() => document.querySelector('#result').textContent.toLowerCase().includes('backfill')"
        )
        shown = pg.inner_text("#result")
        assert "top of the cable" in shown, "the refusal must name the crown on screen"
        assert "950 mm" in shown, "and state its depth"

    # UX-SHARE-01
    def test_each_source_shows_its_share_of_the_increment(self, page, base_url):
        pg = self._ready(page, base_url)
        assert pg.inner_text("#sl-rows").count("%") >= 3

    # UX-VERDICT-01
    @pytest.mark.parametrize("threshold,verdict,variant", [
        ("1.0", "exclude", "pass"),
        ("0.5", "marginal", "shelved"),
        ("0.4", "escalate", "fail"),
    ])
    def test_the_verdict_badge_carries_its_outcome_colour(
            self, page, base_url, threshold, verdict, variant):
        """The reference case changes the rating by about 0,44 %, so the
        threshold alone walks it through exclude, marginal and escalate.

        Every verdict used to map to a variant EOS_UI does not have, and an
        unknown variant renders the neutral badge with no console error — so the
        class is asserted, not the words, which were always right.
        """
        pg = self._ready(page, base_url)
        pg.fill("#thr", threshold)
        pg.click("#run")
        pg.wait_for_function(
            "v => { const b = document.querySelector('.sl-verdict .eos-badge');"
            " return !!b && b.className.includes('eos-badge-status-' + v); }",
            arg=variant)
        badge = pg.locator(".sl-verdict .eos-badge")
        assert "neutral" not in badge.get_attribute("class")
        assert verdict in pg.inner_text("#result").lower()
