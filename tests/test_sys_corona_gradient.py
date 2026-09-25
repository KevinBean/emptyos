"""corona-gradient — API and UI system tests.

Needs the daemon. Every UI assertion maps to a `UX-*` requirement in
`INTENT.md`, because the refusal is most of this app's reason to exist: the
arithmetic is four equations, and what a sheet of paper cannot do is stop a
reader turning a gradient into an acceptance the standard does not support.
"""

from __future__ import annotations

import pytest

BASE = "/corona-gradient"

# IEEE 605-2023 J.2.3.2 — 230 kV strain bus, triple bundle.
J23 = {
    "configuration": "bundle_three_phase",
    "nominal_kv": 230.0, "height_m": 7.95, "phase_spacing_m": 4.12,
    "diameter_mm": 43.9, "n": 3, "subconductor_spacing_mm": 457.0,
    "ambient_c": 40.0, "altitude_km": 0.4, "irregularity_factor": 0.85,
}

# IEEE 605-2023 I.5.2 — 69 kV rigid bus, flat three-phase, single conductor.
I52 = {
    "configuration": "three_phase",
    "nominal_kv": 69.0, "height_m": 3.66, "phase_spacing_m": 2.44,
    "diameter_mm": 73.0, "n": 1,
    "ambient_c": 40.0, "altitude_km": 0.366, "irregularity_factor": 0.85,
}


@pytest.mark.api
class TestCoronaGradientAPI:
    def test_schema_exposes_the_spec(self, http_client):
        r = http_client.get(f"{BASE}/api/schema").json()
        props = r["schema"]["properties"]
        assert {"configuration", "nominal_kv", "height_m", "diameter_mm",
                "irregularity_factor"} <= set(props)
        assert r["defaults"]["nominal_kv"] == 230.0

    def test_schema_declares_no_acceptance_limit(self, http_client):
        """UX-RATIO-01 / REFUSE-VERDICT, held at the API boundary."""
        r = http_client.get(f"{BASE}/api/schema").json()
        joined = " ".join(r["schema"]["properties"]).lower()
        assert "limit" not in joined and "verdict" not in joined

    def test_j23_reproduces_the_published_figures(self, http_client):
        r = http_client.post(f"{BASE}/api/evaluate", json=J23).json()["result"]
        assert r["equivalent_height_m"] == pytest.approx(1.994, abs=5e-4)
        assert r["equivalent_radius_m"] == pytest.approx(0.166, abs=5e-4)
        assert r["e_average_kv_cm"] == pytest.approx(6.98, abs=5e-3)
        assert r["e_maximum_kv_cm"] == pytest.approx(7.61, abs=5e-3)
        assert r["e_onset_kv_cm"] == pytest.approx(17.7, abs=5e-2)
        assert r["v1_kv"] == pytest.approx(146.1, abs=5e-2)

    def test_i52_reproduces_the_published_figures(self, http_client):
        r = http_client.post(f"{BASE}/api/evaluate", json=I52).json()["result"]
        assert r["equivalent_height_m"] == pytest.approx(1.1574, abs=5e-5)
        assert r["e_average_kv_cm"] == pytest.approx(2.89, abs=5e-3)
        assert r["relative_air_density"] == pytest.approx(0.917, abs=5e-4)

    def test_result_carries_no_verdict_field(self, http_client):
        """REFUSE-VERDICT. A consumer must not be able to read a pass off this."""
        r = http_client.post(f"{BASE}/api/evaluate", json=J23).json()["result"]
        for banned in ("verdict", "passed", "acceptable", "status", "ok_corona"):
            assert banned not in r

    def test_ratio_is_reported_without_a_threshold(self, http_client):
        r = http_client.post(f"{BASE}/api/evaluate", json=J23).json()["result"]
        assert r["ratio_em_over_ec"] == pytest.approx(
            r["e_maximum_kv_cm"] / r["e_onset_kv_cm"], rel=1e-9
        )

    def test_bundle_result_warns_about_the_nema_deferral(self, http_client):
        r = http_client.post(f"{BASE}/api/evaluate", json=J23).json()["result"]
        assert any("NEMA CC 1" in w for w in r["warnings"])

    def test_reference_cases_are_loadable(self, http_client):
        """UX-CASE-01."""
        r = http_client.get(f"{BASE}/api/reference-cases").json()
        ids = {c["case_id"] for c in r["cases"]}
        assert {"ieee605-i52-rigid-bus", "ieee605-i53-flexible-bundle",
                "ieee605-j23-strain-bundle"} <= ids

    def test_changing_the_diameter_moves_the_gradient(self, http_client):
        """The anchor must not follow a changed input."""
        base = http_client.post(f"{BASE}/api/evaluate", json=J23).json()["result"]
        bigger = http_client.post(
            f"{BASE}/api/evaluate", json={**J23, "diameter_mm": 80.0}
        ).json()["result"]
        assert bigger["e_maximum_kv_cm"] < base["e_maximum_kv_cm"]

    def test_refusal_renders_the_engines_own_message(self, http_client):
        """UX-REFUSE-01 — not a generic failure."""
        bad = {**J23, "n": 6}
        r = http_client.post(f"{BASE}/api/evaluate", json=bad).json()
        assert "error" in r
        assert "up to 4" in r["error"]

    def test_unphysical_geometry_is_refused_in_band(self, http_client):
        bad = {**I52, "diameter_mm": 9000.0}
        r = http_client.post(f"{BASE}/api/evaluate", json=bad).json()
        assert "error" in r and "height above ground" in r["error"]

    def test_sweep_returns_a_curve_for_the_chart(self, http_client):
        r = http_client.post(
            f"{BASE}/api/sweep",
            json={**J23, "sweep_from_mm": 20, "sweep_to_mm": 100, "sweep_steps": 9},
        ).json()
        pts = r["points"]
        assert len(pts) == 9
        assert pts[0]["e_maximum_kv_cm"] > pts[-1]["e_maximum_kv_cm"]

    def test_sweep_bounds_the_work_a_caller_can_request(self, http_client):
        r = http_client.post(
            f"{BASE}/api/sweep", json={**J23, "sweep_steps": 100000}
        ).json()
        assert len(r["points"]) <= 80

    def test_saving_a_calculation_reaches_the_recent_list(self, http_client):
        """The round-trip EOS_UI.savedCalculations drives.

        Pinned because the first cut hand-rolled the save and never rendered
        the list: `#cg-saved-list` sat permanently empty and no test noticed,
        since nothing asserted the two routes even existed.
        """
        label = "PLAYWRIGHT-TEST- corona j23"
        r = http_client.post(
            f"{BASE}/api/save-calculation",
            json={"label": label, "inputs": J23, "method": "ieee605_annex_d+peek"},
        ).json()
        assert r.get("ok") is True, r

        items = http_client.get(f"{BASE}/api/calculations").json()["items"]
        assert any(label in str(it) for it in items), \
            "saved calculation did not appear in /api/calculations"

    def test_save_recomputes_rather_than_trusting_the_posted_result(self, http_client):
        """A client may post anything; the engine owns the values.

        Read back through `/api/calculations` rather than the save response:
        `save_calculation` returns only `{ok, id, app, path}` by design — the
        note is the record — so asserting `saved["result"]` would be asserting
        a key that never existed. The first cut did exactly that and would
        have failed on its first run.
        """
        label = "PLAYWRIGHT-TEST- corona liar"
        r = http_client.post(
            f"{BASE}/api/save-calculation",
            json={"label": label, "inputs": J23,
                  "result": {"e_maximum_kv_cm": 999.0}},
        ).json()
        assert r.get("ok") is True, r

        row = next(it for it in http_client.get(f"{BASE}/api/calculations").json()["items"]
                   if it.get("title") == label)
        assert row["result"]["e_maximum_kv_cm"] == pytest.approx(7.61, abs=5e-3)

    def test_methods_and_conformance_are_registered(self, http_client):
        m = http_client.get(f"{BASE}/api/methods").json()
        assert "gradient" in m and "onset" in m
        c = http_client.get(f"{BASE}/api/conformance").json()["cases"]
        assert len(c) == 5

    def test_all_conformance_cases_pass(self, http_client):
        """The gate: five published cases, both endpoints."""
        r = http_client.post(f"{BASE}/api/conformance/run", json={}).json()
        results = r["results"]
        assert len(results) == 5
        failed = [x["case_id"] for x in results if not x["passed"]]
        assert not failed, f"conformance failures: {failed}"

    def test_report_renders_the_same_numbers_as_the_evaluation(self, http_client):
        """REPORT-SPEC.md consistency invariant."""
        ev = http_client.post(f"{BASE}/api/evaluate", json=J23).json()["result"]
        md = http_client.get(f"{BASE}/api/report", params={**J23, "format": "md"}).text
        assert f"{ev['e_maximum_kv_cm']:.3f}" in md
        assert f"{ev['e_average_kv_cm']:.3f}" in md

    def test_report_discloses_the_informative_status(self, http_client):
        """REPORT-DISCLOSE. The sheet's second job.

        A sheet printing E_m and E_c without this paragraph invites exactly the
        comparison IEEE 605-2023 withdrew.
        """
        md = http_client.get(f"{BASE}/api/report", params={**J23, "format": "md"}).text
        assert "informative" in md.lower()
        assert "no physical law" in md
        assert "E_o" in md

    def test_report_carries_the_validation_statement(self, http_client):
        """REPORT-SPEC.md § Validation statement, which the sheet omitted.

        The document carried the heading, so `check_engineering_assurance`
        passed while the artefact it describes did not exist — the receipt
        looking maintained is exactly the failure TRUST-LOOP.md § "Every
        obligation carries a receipt" names.
        """
        md = http_client.get(f"{BASE}/api/report", params={**J23, "format": "md"}).text
        assert "## Validation statement" in md
        assert "1.0.0" in md, "method version absent from the sheet"
        for case in ("I.5.2", "I.5.3", "J.2.3.2"):
            assert case in md, f"{case} not named in the validation statement"

    def test_report_refuses_a_format_it_does_not_support(self, http_client):
        """A silent substitution where the documented behaviour is a refusal.

        `.claude/rules/app-reports.md`: an unknown format returns an in-band
        error. Returning 200 + markdown to a caller who asked for PDF lets them
        file a .md as a .pdf with no signal.
        """
        r = http_client.get(f"{BASE}/api/report", params={**J23, "format": "pdf"})
        body = r.text
        assert "error" in body.lower(), f"pdf silently served as markdown: {body[:120]}"

    def test_report_states_the_geometric_scope(self, http_client):
        """LIM-GEOMETRY. The sheet must say what the image construction assumes.

        A gradient quoted without its geometry reads as though it covered the
        fittings and ends, which is where corona is actually controlled
        (AS 2067 Cl. 2.2.6.1).
        """
        md = http_client.get(f"{BASE}/api/report", params={**J23, "format": "md"}).text
        assert "ideal ground plane" in md
        assert "sag" in md
        assert "fittings" in md

    def test_report_omits_fields_the_configuration_did_not_use(self, http_client):
        """A phase spacing on a single-conductor sheet reads as though used."""
        single = {**I52, "configuration": "single"}
        md = http_client.get(
            f"{BASE}/api/report", params={**single, "format": "md"}
        ).text
        assert "Phase spacing" not in md

    def test_report_refuses_with_a_document_status_not_an_error_body(self, http_client):
        r = http_client.get(
            f"{BASE}/api/report", params={**J23, "n": 6, "format": "md"}
        )
        assert r.status_code == 400
        assert r.text.startswith("Refused:")


@pytest.mark.interactive
class TestCoronaGradientUI:
    def test_page_states_the_refusal(self, page, base_url):
        """UX-NOVERDICT-01 / UX-INFORMATIVE-01."""
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#cg-result .cg-num")
        body = page.inner_text("body")
        assert "No pass/fail is stated" in body
        assert "no physical law" in body
        assert "informative" in body.lower()

    def test_page_shows_onset_beside_the_gradient(self, page, base_url):
        """UX-ONSET-01 / UX-RATIO-01."""
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#cg-result .cg-num")
        body = page.inner_text("#cg-result")
        assert "Peek onset reference" in body
        assert "comparison, not a verdict" in body

    def test_onset_across_the_m_range_comes_from_the_onset_endpoint(
        self, page, base_url
    ):
        """UX-MRANGE-01 — m is a judgement, so its width is on the page.

        Asserts the transport as well as the render. The same numbers could be
        produced from `/api/evaluate`, and if they were, `/api/onset` would have
        no interface consumer at all: its request and response shape could then
        drift with conformance still green, because conformance drives the
        registered METHOD, never the route. Watching for the request is what
        makes this test able to notice that.
        """
        posts: list[str] = []
        page.on("request", lambda r: (
            posts.append(r.url)
            if r.method == "POST" and "/api/onset" in r.url else None
        ))
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#cg-onset-m table.cg-m-table tbody tr")

        rows = page.locator("#cg-onset-m tbody tr")
        n = rows.count()
        assert n == 4, f"expected the four permitted m values, got {n}"

        ms: list[float] = []
        ecs: list[float] = []
        for i in range(n):
            cells = rows.nth(i).locator("td")
            # The in-force row reads "0.85 — in use"; the rest are bare.
            ms.append(float(cells.nth(0).inner_text().split("—")[0].strip()))
            ecs.append(float(cells.nth(1).inner_text().strip()))

        assert ms == [0.2, 0.3, 0.6, 0.85], f"m column is {ms}"

        # E_c must be COMPUTED, not printed. Asserting only that the m values
        # appear lets a table rendering a constant -- or one row's value four
        # times -- pass, which is what an earlier draft of this test did.
        assert len(set(ecs)) == 4 and ecs == sorted(ecs), f"E_c column is {ecs}"

        # Anchored on the standard, not on this implementation: the page
        # defaults ARE the J.2.3 published case and its m is 0.85, so the
        # bottom row is that clause's own printed E_c = 17.7 kV/cm.
        assert ecs[-1] == pytest.approx(17.7, abs=5e-2), (
            f"bottom row should reproduce J.2.3.1's published E_c, got {ecs[-1]}"
        )
        # Eq (D.1) is linear in m at fixed geometry, so one ratio pins the whole
        # column without this test reimplementing Peek. The band is the display
        # rounding (two decimals), scaled by the largest ratio.
        for m, ec in zip(ms, ecs):
            assert ec == pytest.approx(ecs[0] * m / ms[0], abs=5e-2), (
                f"E_c is not linear in m: {list(zip(ms, ecs))}"
            )

        assert page.locator("#cg-onset-m tbody tr.cg-m-now").count() == 1, (
            "exactly one row is in force — marking every row would satisfy a "
            "bare 'in use' substring check"
        )

        assert len(posts) == n, (
            f"expected one POST /api/onset per row, saw {len(posts)}. The same "
            "numbers come back from /api/evaluate, so without counting them "
            "the endpoint could lose its only interface consumer unnoticed."
        )

    def test_page_shows_the_hand_checkable_intermediates(self, page, base_url):
        """UX-INTERMEDIATE-01."""
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#cg-meta")
        meta = page.inner_text("#cg-meta")
        assert "Relative air density" in meta
        assert "Equivalent height" in meta

    def test_every_input_label_carries_its_unit(self, page, base_url):
        """UX-UNIT-01.

        Checked against `spec.py` rather than a hand-written list, so a new
        field with no unit in its label fails here instead of shipping. The
        diameter label must additionally say whether it means the subconductor,
        because for a bundle that is the single easiest input to get wrong.
        """
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#cg-config")
        labels = page.inner_text("#app")
        for unit in ("kV", "(m)", "(mm)", "°C", "(km)"):
            assert unit in labels, f"no label carries {unit!r}"
        page.select_option("#cg-config", "bundle_three_phase")
        assert "subconductor" in page.inner_text("#cg-sub-note").lower()

    def test_reference_case_buttons_render_and_load(self, page, base_url):
        """UX-CASE-01, receipted in the UI rather than by an API test.

        The traceability table named `pages/corona-gradient.js` for this row
        while citing an API test that never loads the page: deleting the
        case-button loop left the cited test green and the affordance gone.
        """
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#cg-case-buttons button")
        buttons = page.locator("#cg-case-buttons button")
        assert buttons.count() == 3
        buttons.filter(has_text="j23").first.click()
        page.wait_for_function(
            "() => (document.querySelector('#cg-result .cg-num')||{}).innerText"
            "      && document.querySelector('#cg-result .cg-num').innerText.indexOf('7.61') === 0"
        )

    def test_warnings_render_distinctly_from_the_result(self, page, base_url):
        """UX-WARN-01 — deleting the EOS_UI.warnList call must fail something."""
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#cg-warn .eos-warn")
        assert "NEMA CC 1" in page.inner_text("#cg-warn")

    def test_a_refusal_renders_the_engines_message_on_the_page(self, page, base_url):
        """UX-REFUSE-01 — the engine's own words, not a generic failure.

        The refusal is driven from the HEIGHT, not the diameter. An earlier
        draft filled a 9000 mm diameter and waited for an error: 4.5 m of
        radius under the default 7.95 m height is perfectly legal geometry, so
        the engine computed a result and the test sat out its full timeout
        waiting for a refusal that was never coming. Collapsing the height
        instead puts the effective radius above it, which is the unphysical
        case `_check_clearance` exists to catch.
        """
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#cg-result .cg-num")
        page.fill("#cg-height", "0.01")
        page.dispatch_event("#cg-height", "change")
        page.wait_for_selector("#cg-result .eos-error-state, #cg-result [class*=error]")
        assert "height above ground" in page.inner_text("#cg-result")

    def test_single_configuration_hides_the_bundle_fields(self, page, base_url):
        """UX-SCOPE-01 — nobody reads a spacing the result did not consume."""
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#cg-config")
        page.select_option("#cg-config", "single")
        assert page.is_hidden("#cg-n-box")
        assert page.is_hidden("#cg-s-box")
        assert page.is_hidden("#cg-spacing-box")
