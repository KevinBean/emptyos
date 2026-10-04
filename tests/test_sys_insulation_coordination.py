"""insulation-coordination — API and UI system tests.

Needs the daemon. Each `UX-*` requirement in INTENT.md and each `REPORT-*`
requirement in REPORT-SPEC.md labels, in a comment directly above it, the test
that asserts it; `tests/test_unit_insulation_coordination_spec.py` checks that
every stated id labels a test that exists.
"""

from __future__ import annotations

import pytest

BASE = "/insulation-coordination"

G3_SEPARATION = {"method": "annex_e_eq19", "u_pl_kv": 1500.0,
                 "line_type": "transmission-four-conductor-bundle", "n_lines": 2,
                 "a1_m": 40.0, "a2_m": 0.0, "a3_m": 0.0, "a4_m": 0.0,
                 "span_m": 400.0, "line_length_m": 1300.0}
G3_INTERNAL = {"u_rp_kv": 1300.0, "k_c": 1.03, "k_s": 1.15}


@pytest.mark.api
class TestInsulationCoordinationAPI:
    def test_schema_lists_each_methods_parameters(self, http_client):
        r = http_client.get(f"{BASE}/api/schema").json()
        assert r["slots"] == ["levels", "chain", "separation", "energy", "arrester", "discharge"]
        assert "steepness_kv_per_us" in r["methods"]["separation"]["simplified_eq1_eq2"]
        assert "line_type" in r["methods"]["separation"]["annex_e_eq19"]
        assert "line_type" not in r["methods"]["separation"]["simplified_eq1_eq2"]

    def test_levels_for_a_275kv_system(self, http_client):
        r = http_client.post(f"{BASE}/api/levels", json={"us_kv": 300}).json()
        assert r["ok"] is True
        assert r["result"]["um_kv"] == 300.0
        assert r["result"]["pu_base_kv"] == pytest.approx(244.949, abs=1e-3)
        assert "non-preferred" in r["result"]["um_note"]

    def test_methods_and_conformance_are_registered(self, http_client):
        c = http_client.get(f"{BASE}/api/conformance").json()
        assert {x["case_id"] for x in c["cases"]} == {
            "iec60071-2-g3-selection", "iec60071-2-g3-internal-slow-front",
            "iec60071-2-g3-external-tov", "iec60071-2-g3-separation",
            "iec60071-2-g4-separation-four-lines",
            "iec60099-4-annex-l-example-1", "iec60099-4-annex-l-example-4"}

    def test_conformance_passes(self, http_client):
        r = http_client.post(f"{BASE}/api/conformance/run", json={}).json()
        assert len(r["results"]) == 7
        for row in r["results"]:
            assert row.get("passed") is True, row

    def test_unknown_input_is_refused_not_ignored(self, http_client):
        r = http_client.post(f"{BASE}/api/chain", json={**G3_INTERNAL, "ks": 1.15}).json()
        assert "unknown input" in r["error"]

    def test_another_calculators_input_is_refused_not_dropped(self, http_client):
        r = http_client.post(f"{BASE}/api/chain", json={**G3_INTERNAL, "us_kv": 300}).json()
        assert "not taken by this method" in r["error"] and "us_kv" in r["error"]

    def test_missing_input_is_named(self, http_client):
        r = http_client.post(f"{BASE}/api/chain", json={"u_rp_kv": 1300, "k_c": 1.03}).json()
        assert "k_s" in r["error"]

    def test_unmet_selection_is_a_result(self, http_client):
        r = http_client.post(f"{BASE}/api/levels",
                             json={"us_kv": 765, "required_siwv_pe_kv": 1746}).json()
        assert r["result"]["selection_met"] is False
        assert "special test" in r["result"]["selection_reason"]

    # REPORT-METHOD-01 · REPORT-SCOPE-01 · REPORT-SOURCE-01
    def test_report_names_method_scope_and_source(self, http_client):
        sheet = http_client.post(f"{BASE}/api/report",
                                 json={"endpoint": "separation", **G3_SEPARATION}).text
        assert "annex_e_eq19" in sheet and "engines/insulation_coordination/evaluate.py" in sheet
        assert "does not replace an EMT study" in sheet
        assert "Table E.2" in sheet

    # REPORT-AGREE-01
    def test_report_agrees_with_the_api_and_follows_an_input_change(self, http_client):
        api = http_client.post(f"{BASE}/api/chain", json=G3_INTERNAL).json()["result"]
        sheet = http_client.post(f"{BASE}/api/report", json={"endpoint": "chain", **G3_INTERNAL}).text
        assert f"| Required withstand voltage | Urw | {api['u_rw_kv']:.6g} | kV |" in sheet
        moved = {**G3_INTERNAL, "k_s": 1.05}
        api2 = http_client.post(f"{BASE}/api/chain", json=moved).json()["result"]
        sheet2 = http_client.post(f"{BASE}/api/report", json={"endpoint": "chain", **moved}).text
        assert f"{api['u_rw_kv']:.6g}" not in sheet2
        assert f"| Required withstand voltage | Urw | {api2['u_rw_kv']:.6g} | kV |" in sheet2

    # REPORT-SKIP-01
    def test_report_lists_checks_not_run_once_each(self, http_client):
        sheet = http_client.post(f"{BASE}/api/report", json={
            "endpoint": "arrester", "us_kv": 300, "earth_fault_factor": 1.36,
            "duration_s": 1.0}).text
        assert "## Checks not run" in sheet and "product property" in sheet
        unmet = http_client.post(f"{BASE}/api/report", json={
            "endpoint": "levels", "us_kv": 765, "required_siwv_pe_kv": 1746}).text
        assert unmet.count("a special test is needed") == 1

    # REPORT-REFUSE-01
    def test_a_refused_report_is_a_400_not_a_document(self, http_client):
        r = http_client.post(f"{BASE}/api/report", json={"endpoint": "chain", "u_rp_kv": 1300})
        assert r.status_code == 400
        assert "missing required input" in r.text
        assert "# Insulation co-ordination" not in r.text

    def test_a_wrongly_typed_report_input_is_a_400_not_a_500(self, http_client):
        r = http_client.post(f"{BASE}/api/report", json={"endpoint": "levels", "us_kv": [1]})
        assert r.status_code == 400


@pytest.mark.interactive
class TestInsulationCoordinationUI:
    """UX-* assertions, plus two structural checks (a form per slot, unique ids).

    Waits on the last section's form grid, not its result region: an empty
    result div has no height, so Playwright reports it hidden and a visibility
    wait on it never settles even though the page built fine. Fields are
    addressed by `data-field` inside their section, because a field two
    calculators take (`us_kv`) renders in both.
    """

    def _ready(self, page, base_url):
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector('.ic-section[data-slot="discharge"] .ic-grid')
        page.wait_for_selector('.ic-section[data-slot="separation"] select.eos-method-picker-select')
        return page

    def _method(self, section, method_id):
        section.locator("select.eos-method-picker-select").select_option(method_id)

    def _section(self, page, slot):
        return page.locator(f'.ic-section[data-slot="{slot}"]')

    def _fill(self, section, values):
        for field, value in values:
            section.locator(f'[data-field="{field}"]').fill(value)

    def _calculate(self, section):
        section.locator('[data-action="run"]').click()

    def test_every_slot_renders_a_form(self, page, base_url):
        pg = self._ready(page, base_url)
        assert pg.locator(".ic-section .ic-grid").count() == 6

    def test_field_ids_are_unique(self, page, base_url):
        pg = self._ready(page, base_url)
        ids = pg.eval_on_selector_all("[data-field]", "els => els.map(e => e.id)")
        assert len(ids) == len(set(ids)), "a field id repeats across sections"

    # UX-EXAMPLE-01
    @pytest.mark.parametrize("slot,pairs", [
        ("levels", ["800 kV (computed 800)", "1550 kV (computed 1550)", "1950 kV (computed 1950)"]),
        ("chain", ["1340 kV (computed 1339)", "1540 kV (computed 1539.85)"]),
        ("separation", ["130 kV (computed 129.412)", "1630 kV (computed 1629.41)"]),
        ("energy", ["2797 kJ (computed 2796.58)"]),
    ])
    def test_each_published_example_shows_printed_beside_computed(self, page, base_url, slot, pairs):
        pg = self._ready(page, base_url)
        sec = self._section(pg, slot)
        sec.locator('[data-action="example"]').click()
        published = sec.locator('[data-part="published"]')
        published.get_by_text("Printed in").first.wait_for()
        text = published.inner_text()
        for pair in pairs:
            assert pair in text, f"{slot}: {pair!r} not in {text!r}"

    # UX-LIMIT-01
    @pytest.mark.parametrize("limit,verdict", [("787.5", "Urp exceeds the acceptance limit"),
                                               ("1000", "Within the acceptance limit")])
    def test_the_limit_is_a_verdict_in_both_directions(self, page, base_url, limit, verdict):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "separation")
        self._method(sec, "simplified_eq1_eq2")
        self._fill(sec, (("u_pl_kv", "540"), ("steepness_kv_per_us", "1500"), ("a1_m", "3"),
                         ("a2_m", "20"), ("a3_m", "5"), ("a4_m", "2"), ("urp_limit_kv", limit)))
        self._calculate(sec)
        sec.get_by_text(verdict).wait_for()

    # UX-STALE-01
    def test_switching_method_clears_the_result(self, page, base_url):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "separation")
        sec.locator('[data-action="example"]').click()
        sec.locator('[data-part="published"]').get_by_text("Printed in").first.wait_for()
        self._method(sec, "simplified_eq1_eq2")
        assert sec.locator(".ic-result").inner_text().strip() == ""
        assert sec.locator(".ic-sheet-slot").inner_text().strip() == ""

    # UX-LEVELS-01
    def test_available_levels_are_listed(self, page, base_url):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "levels")
        self._fill(sec, (("us_kv", "300"),))
        self._calculate(sec)
        sec.get_by_text("Standard levels for this Um").wait_for()
        assert sec.locator("table.ic-levels tbody tr").count() == 2
        table = sec.locator("table.ic-levels").inner_text()
        assert "1275" in table and "1125" in table

    # UX-SHEET-01
    def test_sheet_renders_below_the_result_with_a_copy_control(self, page, base_url):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "chain")
        sec.locator('[data-action="example"]').click()
        # Scoped to the result rows: the same label also appears in the printed-value row.
        sec.locator('[data-part="rows"]').get_by_text("Required withstand voltage").wait_for()
        sec.locator('[data-action="sheet"]').click()
        sheet = sec.locator('[data-part="sheet"]')
        sheet.wait_for()
        assert "| Required withstand voltage | Urw | 1539.85 | kV |" in sheet.inner_text()
        assert sec.locator('[data-part="rows"]').count() == 1
        assert sec.locator('[data-action="copy-sheet"]').count() == 1
        below = pg.evaluate(
            "() => { const s = document.querySelector('.ic-section[data-slot=\"chain\"]');"
            " const r = s.querySelector('.ic-result'), k = s.querySelector('.ic-sheet-slot');"
            " return !!(r.compareDocumentPosition(k) & Node.DOCUMENT_POSITION_FOLLOWING); }")
        assert below is True

    # UX-METHOD-01
    def test_method_choice_hides_the_other_methods_inputs(self, page, base_url):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "separation")
        self._method(sec, "simplified_eq1_eq2")
        assert sec.locator('[data-field-wrap="steepness_kv_per_us"]').is_visible()
        assert not sec.locator('[data-field-wrap="line_type"]').is_visible()
        self._method(sec, "annex_e_eq19")
        assert not sec.locator('[data-field-wrap="steepness_kv_per_us"]').is_visible()
        assert sec.locator('[data-field-wrap="line_type"]').is_visible()

    # UX-EQ-01
    @pytest.mark.parametrize("a1,equation", [("10", "1"), ("100", "2")])
    def test_the_governing_equation_is_shown(self, page, base_url, a1, equation):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "separation")
        self._method(sec, "simplified_eq1_eq2")
        self._fill(sec, (("u_pl_kv", "540"), ("steepness_kv_per_us", "1500"), ("a1_m", a1),
                         ("a2_m", "0"), ("a3_m", "0"), ("a4_m", "0")))
        self._calculate(sec)
        sec.get_by_text(f"Governed by Equation ({equation})").wait_for()

    # UX-UNMET-01
    def test_an_unmet_selection_says_so_in_words(self, page, base_url):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "levels")
        self._fill(sec, (("us_kv", "765"), ("required_siwv_pe_kv", "1746")))
        self._calculate(sec)
        sec.get_by_text("No standard level meets this").wait_for()
        assert "special test" in sec.locator(".ic-result").inner_text()

    # UX-REFUSE-01
    def test_a_refusal_replaces_the_previous_result(self, page, base_url):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "chain")
        sec.locator('[data-action="example"]').click()
        sec.locator('[data-part="rows"]').get_by_text("Required withstand voltage").wait_for()
        sec.locator('[data-field="k_s"]').fill("")
        self._calculate(sec)
        sec.locator(".ic-result").get_by_text("missing required input").wait_for()
        assert sec.locator('[data-part="rows"]').count() == 0

    # UX-REFUSE-01
    def test_a_malformed_curve_line_is_explained(self, page, base_url):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "arrester")
        self._fill(sec, (("us_kv", "300"), ("earth_fault_factor", "1.36"), ("duration_s", "1"),
                         ("tov_curve", "0,1 1,20")))
        self._calculate(sec)
        # Scoped to the result: the section's lead paragraph also mentions the decimal point.
        sec.locator(".ic-result").get_by_text("decimal point").wait_for()

    # UX-SKIP-01
    def test_checks_not_run_are_listed_with_reasons(self, page, base_url):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "arrester")
        self._fill(sec, (("us_kv", "300"), ("earth_fault_factor", "1.36"), ("duration_s", "1")))
        self._calculate(sec)
        sec.get_by_text("Checks not run").wait_for()
        assert "product property" in sec.locator(".ic-result").inner_text()

    # UX-LUMPED-01
    def test_the_lumped_figure_is_labelled_as_not_physical(self, page, base_url):
        pg = self._ready(page, base_url)
        sec = self._section(pg, "discharge")
        self._fill(sec, (("v0_kv", "245"), ("capacitance_uf", "0.557"),
                         ("surge_impedance_ohm", "30"), ("loop_inductance_uh", "10")))
        self._calculate(sec)
        sec.get_by_text("not the physical current").wait_for()
