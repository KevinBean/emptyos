"""cable-bonding — API and UI system tests.

Needs the daemon. Every UI assertion here maps to a `UX-*` requirement in
`INTENT.md`, because the three visibility requirements are the app's actual
reason to exist: a spreadsheet already computes the numbers, and what it cannot
do is show you how much of the answer rests on a length you estimated or on
which authority you named.
"""

from __future__ import annotations

import pytest

from helpers import requires_dep

BASE = "/cable-bonding"

VALID = {
    "standing_limit_v": 150.0,
    # The second required input (REFUSE-MARGIN). Omitted here originally, which
    # made every VALID-based request a refusal and the whole suite red.
    "coordination_margin_min": 0.15,
    "route_length_m": 1000.0,
    "spacing_m": 0.25,
    "formation": "flat",
    "sheath_mean_radius_m": 0.033,
    "load_current_a": 1000.0,
    "fault_current_a": 20000.0,
    "bil_kvp": 650.0,
    "z1_ohm": 14.0,
    "z1b_ohm": 17.35,
    "lead_length_m": 10.0,
    "authority": "iec-60840",
    # The arrester. Once its rated voltage stopped being defaulted -- a review
    # found the old default contributing 20 kV of a 56 kV answer, silently --
    # `U_R` became a supplied term of F1 rather than a garnish on it, so a
    # fixture without it exercises the SKIP path, not the calculation.
    # 10.0 / 8.5, NOT the 10.0 / 8.0 pair `spec.py` records as the old
    # fabricated default: (10-8)/8 is exactly 0.25, the inclusive top edge of
    # `UC_TO_UR_RANGE`, so a fixture sitting there can never distinguish a
    # working plausibility warning from a broken one. 8.5 gives 17.6 %, inside
    # the band with room on both sides.
    "svl_u_r_kv": 10.0,
    "svl_u_c_kv": 8.5,
}

#: The same arrangement with no arrester named. Three of the five slots cannot
#: be computed from it; see `test_slots_that_need_the_arrester_are_skipped`.
#: Listed rather than derived by prefix: a `startswith("svl_")` filter would
#: silently swallow any future svl-prefixed field that is NOT arrester data.
_ARRESTER = ("svl_u_r_kv", "svl_u_c_kv")
NO_ARRESTER = {k: v for k, v in VALID.items() if k not in _ARRESTER}

#: Rated voltage supplied, continuous voltage not. The realistic partial case --
#: `U_R` is on the nameplate, `U_C` is on the data sheet you do not have.
PARTIAL_ARRESTER = {k: v for k, v in VALID.items() if k != "svl_u_c_kv"}


@pytest.mark.api
class TestAPI:
    def test_schema_endpoint_serves_the_spec(self, http_client):
        r = http_client.get(f"{BASE}/api/schema")
        assert r.status_code == 200
        d = r.json()
        assert set(d["required"]) == {"standing_limit_v", "coordination_margin_min"}
        assert "standing_limit_v" in d["schema"]["properties"]
        assert d["schema"]["properties"]["z1_ohm"]["minimum"] > 0

    def test_calc_returns_all_five_slots(self, http_client):
        r = http_client.post(f"{BASE}/api/calc", json=VALID)
        assert r.status_code == 200
        d = r.json()
        assert "error" not in d, d.get("error")
        assert set(d["slots"]) == {
            "standing", "tov", "transient", "coordination", "svl"
        }

    def test_the_missing_required_input_is_refused_with_its_reason(self, http_client):
        """REFUSE-LIMIT / UX-INP-02 — the refusal explains why no default exists."""
        body = {k: v for k, v in VALID.items() if k != "standing_limit_v"}
        d = http_client.post(f"{BASE}/api/calc", json=body).json()
        assert "error" in d
        assert "standing_limit_v" in d["error"]
        assert "No IEC limit exists" in d["error"]

    def test_a_domain_violation_is_refused_by_name(self, http_client):
        d = http_client.post(f"{BASE}/api/calc", json={**VALID, "z1_ohm": -5.0}).json()
        assert "error" in d and "z1_ohm" in d["error"]

    def test_the_transient_result_carries_the_bonding_lead_share(self, http_client):
        d = http_client.post(f"{BASE}/api/calc", json=VALID).json()
        t = d["transient"]
        assert 0.0 < t["inductive_fraction"] < 1.0
        assert t["svl_term_kv"] + t["inductive_term_kv"] == pytest.approx(
            t["barrier_voltage_kv"], rel=1e-9
        )

    def test_the_tov_result_carries_the_resistive_share(self, http_client):
        d = http_client.post(f"{BASE}/api/calc", json=VALID).json()
        assert d["tov"]["tov_resistive_fraction"] > 0.0

    def test_coordination_reports_every_authority(self, http_client):
        """REPORT-SPREAD at the API layer."""
        d = http_client.post(f"{BASE}/api/calc", json=VALID).json()
        c = d["coordination"]
        assert set(c["other_authorities"]) == {
            "iec-60840", "ieee-575", "cigre-tb-283"
        }

    def test_the_verdict_flips_on_the_authority(self, http_client):
        """The whole reason the authority is a required choice, in its strongest
        form: one arrangement, two authorities, OPPOSITE verdicts.

        A short (3 m) bonding lead is essential here. At 10 m IEC 60840 and
        CIGRE TB 283 agree at 75 kV — which the engine suite pins separately —
        so a long-lead case would show no divergence at all. The divergence
        lives at short leads, where TB 283 keeps the older Electra 47 value of
        40 kVp against 60 kV in both IEC and IEEE, and is the least
        conservative of the three.

        `z1_ohm = 6` puts the interrupt voltage at ~45 kV, between the two.
        """
        case = {**VALID, "lead_length_m": 3.0, "z1_ohm": 6.0}
        iec = http_client.post(f"{BASE}/api/calc",
                       json={**case, "authority": "iec-60840"}).json()
        tb = http_client.post(f"{BASE}/api/calc",
                      json={**case, "authority": "cigre-tb-283"}).json()
        assert iec["transient"]["barrier_voltage_kv"] == pytest.approx(
            tb["transient"]["barrier_voltage_kv"], rel=1e-12
        ), "the same arrangement must give the same interrupt voltage"
        assert iec["coordination"]["withstand_kv"] == 60.0
        assert tb["coordination"]["withstand_kv"] == 40.0
        assert iec["coordination"]["passed"] is True
        assert tb["coordination"]["passed"] is False

    def test_iec_and_tb283_agree_at_long_leads(self, http_client):
        """The other half, so the test above is known to be about short leads
        rather than about the two authorities always disagreeing."""
        case = {**VALID, "lead_length_m": 10.0}
        iec = http_client.post(f"{BASE}/api/calc",
                       json={**case, "authority": "iec-60840"}).json()
        tb = http_client.post(f"{BASE}/api/calc",
                      json={**case, "authority": "cigre-tb-283"}).json()
        assert iec["coordination"]["withstand_kv"] == 75.0
        assert tb["coordination"]["withstand_kv"] == 75.0

    def test_svl_reports_skipped_steps(self, http_client):
        """REPORT-STEPS — the arrester's two ratings are named; its TOV-vs-time
        curve, residual voltage and energy rating are not. So steps 2, 5 and 6
        cannot run and 1, 3 and 4 can.

        Partial product data is the normal case at design time, and the point of
        the requirement is that the six-step selection says which steps it did
        not perform rather than presenting three of six as a completed one.

        Both halves are asserted. `steps_skipped >= {...}` alone is satisfied by
        a selector that degenerated to running NOTHING, which is the failure
        this requirement is supposed to make visible.
        """
        d = http_client.post(f"{BASE}/api/calc", json=VALID).json()
        s = d["svl"]
        assert {str(k) for k in s["steps_skipped"]} == {"2", "5", "6"}
        assert sorted(s["steps_run"]) == [1, 3, 4]
        # Each skip names the product property it wanted, not just a number.
        assert "TOV" in str(s["steps_skipped"]["2"])
        assert "residual" in str(s["steps_skipped"]["5"]).lower()
        assert "energy" in str(s["steps_skipped"]["6"]).lower()

    def test_slots_that_need_the_arrester_are_skipped(self, http_client):
        """`U_R` is a term of F1, so no arrester means no interrupt voltage --
        and therefore no coordination verdict and no selection.

        This path used to be a 500: the defaults were removed without the read
        sites following, so the documented happy path raised `KeyError`. The
        contract is that the request succeeds, returns what it can, and names
        each thing it could not compute with the reason.
        """
        r = http_client.post(f"{BASE}/api/calc", json=NO_ARRESTER)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert "error" not in d, d.get("error")
        assert set(d["slots"]) == {"standing", "tov"}
        assert set(d["slots_skipped"]) == {"transient", "coordination", "svl"}
        assert "svl_u_r_kv" in d["slots_skipped"]["transient"]

        # "returns what it can" is half the contract, and asserting the two slot
        # NAMES does not check it: `slots` is a hardcoded literal on the server,
        # so `{"standing": {}, "tov": {}}` satisfies every line above. Read the
        # payloads and compare them to the full run -- the two slots that do not
        # depend on the arrester must be numerically identical to their values
        # when one IS named.
        full = http_client.post(f"{BASE}/api/calc", json=VALID).json()
        assert d["standing"]["standing_voltage_v"] == pytest.approx(
            full["standing"]["standing_voltage_v"], rel=1e-12)
        assert d["tov"]["tov_sheath_voltage_v"] == pytest.approx(
            full["tov"]["tov_sheath_voltage_v"], rel=1e-12)
        assert d["standing"]["standing_voltage_v"] > 0.0
        assert d["standing"]["within_limit"] is not None

    def test_a_rated_voltage_without_a_continuous_voltage_skips_step_3(self, http_client):
        """The partial arrester: `U_R` from the nameplate, `U_C` unknown.

        This used to substitute 0.0 at the read site and run step 3 against it,
        producing a FAILED Uc check at a margin of exactly -100 % -- which reads
        as *this arrester is unsuitable* when the truth is *you did not say what
        its continuous voltage is*. It also silently disabled the Ur/Uc
        plausibility warning, whose guard was `> 0.0`. Same fabricated-default
        defect as the `U_R` one, surviving one field over.
        """
        d = http_client.post(f"{BASE}/api/calc", json=PARTIAL_ARRESTER).json()
        assert "error" not in d, d.get("error")
        s = d["svl"]
        assert 3 not in s["steps_run"]
        assert "3" in {str(k) for k in s["steps_skipped"]}
        assert "Uc" in str(s["steps_skipped"]["3"])
        assert not any(c["name"] == "uc" for c in s["checks"]),             "an unsupplied product property must not produce a verdict about it"

    def test_a_named_method_dispatches_rather_than_being_refused(self, http_client):
        """The positive half of the envelope fix.

        `run_method_endpoint` consumes `method` as its routing key and used to
        hand the same dict to the method fn, where this app's strict coercion
        saw an undeclared input and refused. So naming a method -- the one
        request that needs the registry -- was the one request that could not
        work. The pre-existing default-method test sends no `method` key at all,
        so it passed throughout and proves nothing about this.
        """
        r = http_client.post(f"{BASE}/api/transient",
                             json={**VALID, "method": "cigre_wg_simplified"})
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d.get("ok") is True, d
        assert d["method"] == "cigre_wg_simplified"
        assert d["result"]["barrier_voltage_kv"] > 0.0

    def test_an_undeclared_input_is_still_refused_by_name(self, http_client):
        """The envelope fix must not have widened into a general escape hatch.
        Only the routing key the mixin itself consumed is exempt.
        """
        d = http_client.post(f"{BASE}/api/transient",
                             json={**VALID, "z1_ohmm": 14.0}).json()
        assert "error" in d and "z1_ohmm" in d["error"], d

    def test_report_endpoint_returns_markdown(self, http_client):
        """UX-REP-01."""
        r = http_client.post(f"{BASE}/api/report", json=VALID)
        assert r.status_code == 200
        text = r.text
        assert "# Calculation report" in text
        assert "650 kVp / 14 ohm" in text          # F2 shown as a division
        assert "What the other authorities give" in text

    def test_the_report_and_the_api_cannot_disagree(self, http_client):
        """The consistency invariant of REPORT-SPEC.md: both render from one
        engine call, so the same inputs cannot produce two answers."""
        d = http_client.post(f"{BASE}/api/calc", json=VALID).json()
        text = http_client.post(f"{BASE}/api/report", json=VALID).text
        assert f"{d['transient']['barrier_voltage_kv']:.2f} kV" in text

    def test_algorithm_document_is_served(self, http_client):
        r = http_client.get(f"{BASE}/api/algorithm")
        assert r.status_code == 200
        assert "## 4. Method" in r.text

    def test_methods_are_registered(self, http_client):
        r = http_client.get(f"{BASE}/api/methods")
        assert r.status_code == 200

    def test_the_transient_endpoint_dispatches_the_default_method(self, http_client):
        r = http_client.post(f"{BASE}/api/transient", json=VALID)
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["method"] == "cigre_wg_simplified"
        assert d["result"]["barrier_voltage_kv"] > 0

    @requires_dep('scipy')
    def test_the_emt_method_refuses_without_a_measured_cross_section(self, http_client):
        """REFUSE-GEOMDATA — the EMT path must not infer a cable cross-section."""
        r = http_client.post(f"{BASE}/api/transient",
                             json={**VALID, "method": "emt_bergeron"})
        body = r.text
        assert "cross-section" in body or "emt_r_core_outer_m" in body, body[:300]

    @requires_dep('scipy')
    def test_the_emt_method_runs_with_a_measured_cross_section(self, http_client):
        """And when the radii are supplied it builds, gates and reports."""
        r = http_client.post(f"{BASE}/api/transient", json={
            **VALID, "method": "emt_bergeron",
            "emt_r_core_outer_m": 0.0150, "emt_r_sheath_inner_m": 0.0320,
            "emt_r_sheath_outer_m": 0.0330, "emt_r_outer_m": 0.0370,
            # The materials are measured too — Z(w) depends on resistivity as
            # strongly as on radius, so the refusal covers them.
            "emt_rho_core_ohm_m": 1.7241e-8,
            "emt_rho_sheath_ohm_m": 21.4e-8,
            "emt_eps_r_insulation": 2.5,
        })
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        res = d.get("result", d)
        assert res.get("diagonalisation_residual") is not None, r.text[:300]
        assert res["diagonalisation_residual"] < 0.05

    def test_conformance_passes_on_all_eight_published_rows(self, http_client):
        """VAL-F2 — the gate.

        The previous body was `assert "row_0_kv" in blob or d.get("passed") is
        not None`, which `passed: False` satisfies and which any computed
        result satisfies. It was the only test exercising the live
        `/api/conformance/run` wiring — `_conf_f2_inputs`, `_conf_f2_expected`,
        `_batch_simplified` and the mixin comparison — so a key mismatch that
        compared *nothing* shipped green. The engine is anchored separately by
        `TestPublishedTable::test_each_published_row`; this is the app-side
        join, and it now fails if the join stops joining.
        """
        r = http_client.post(f"{BASE}/api/conformance/run")
        assert r.status_code == 200, r.text[:300]
        d = r.json()

        cases = d if isinstance(d, list) else (d.get("results") or d.get("cases") or [d])
        assert cases, f"conformance run compared nothing: {str(d)[:300]}"

        # An empty comparison is not a pass — the TRUST-LOOP "set difference
        # over an empty set" shape.
        for case in cases:
            assert case.get("expected"), (
                f"case {case.get('case_id')!r} carries no expected values, so "
                f"whatever it 'passed' was not a comparison"
            )
            assert case.get("passed") is True, (
                f"case {case.get('case_id')!r} did not pass: "
                f"{str(case.get('methods'))[:400]}"
            )

        # The published table has eight rows and the fixture is the anchor; a
        # silent shrink to one row would otherwise still read as a pass.
        import json as _json
        from pathlib import Path as _Path
        fixture = _json.loads(
            (_Path(__file__).parent.parent / "apps/extension/engineering/"
             "cable-bonding/fixtures/cigre_tb283_table_f2.json")
            .read_text(encoding="utf-8")
        )
        n_rows = len(fixture["rows"])
        assert n_rows == 8, "the fixture itself lost rows"
        compared = str(d)
        for i in range(n_rows):
            assert f"row_{i}_" in compared, (
                f"published row {i} of {n_rows} never reached the comparison"
            )


@pytest.mark.interactive
class TestUI:
    def test_page_loads_without_js_errors(self, page, base_url, page_errors):
        from page_helpers import assert_no_js_errors

        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#cb-fields .cb-field", timeout=10000)
        assert_no_js_errors(page_errors)

    def test_the_form_is_built_from_the_spec(self, page, base_url):
        """UX-INP-01 — units and bounds reach the browser."""
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#f-z1_ohm", timeout=10000)
        assert page.get_attribute("#f-z1_ohm", "min") is not None
        assert "ohm" in page.inner_text("label[for='f-z1_ohm']")

    def test_the_required_field_renders_empty_with_its_reason(self, page, base_url):
        """UX-INP-02 — not a placeholder that looks like a default."""
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#f-standing_limit_v", timeout=10000)
        assert page.input_value("#f-standing_limit_v") == ""
        text = page.inner_text("#f-standing_limit_v ~ .cb-req")
        assert "no default" in text.lower()

    @staticmethod
    def _open(page, base_url):
        page.goto(f"{base_url}{BASE}/")
        page.wait_for_selector("#f-standing_limit_v", timeout=10000)
        page.fill("#f-standing_limit_v", "150")
        page.fill("#f-coordination_margin_min", "0.15")

    @classmethod
    def _calculate(cls, page, base_url, **overrides):
        """Drive a FULL five-card run.

        The arrester is filled explicitly. It used to be defaulted, and the
        helper inherited that: after the review removed the fabricated `U_R`,
        this helper still typed only the two required fields and every
        result-rendering test waited 20 s for a card the page was right not to
        draw. Naming the arrester here keeps these tests about what they claim
        to be about -- how a computed result is presented -- and leaves the
        partial run to `test_a_run_without_an_arrester_renders_its_gaps`.
        """
        cls._open(page, base_url)
        page.fill("#f-svl_u_r_kv", "10")
        page.fill("#f-svl_u_c_kv", "8")
        for k, v in overrides.items():
            page.select_option(f"#f-{k}", str(v))
        page.click("#cb-calc")
        page.wait_for_selector("#cb-lead-share", timeout=20000)

    def test_result_shows_the_bonding_lead_share(self, page, base_url):
        """UX-RES-01."""
        self._calculate(page, base_url)
        share = page.inner_text("#cb-lead-share")
        assert share.endswith("%")
        assert float(share.rstrip("%")) > 0.0

    def test_result_shows_the_authority_spread(self, page, base_url):
        """UX-RES-02 — a verdict that depends on a choice must look like one."""
        self._calculate(page, base_url)
        spread = page.inner_text("#cb-spread")
        assert "ieee-575" in spread and "cigre-tb-283" in spread

    def test_skipped_svl_steps_are_visible(self, page, base_url):
        """UX-RES-03."""
        self._calculate(page, base_url)
        text = page.inner_text("#cb-skipped")
        assert "did NOT run" in text
        assert "Step 2" in text or "step 2" in text.lower()

    def test_a_partial_svl_selection_does_not_read_as_a_full_one(self, page, base_url):
        """No arrester data is supplied, so three of the six TB 797 steps cannot
        run. The headline must say so — a bare ACCEPTED in 1.6rem type is where
        that distinction would otherwise stop surviving."""
        self._calculate(page, base_url)
        body = page.inner_text("#cb-results")
        assert "ACCEPTED on 3 of 6 steps" in body, body[-400:]

    def test_a_run_without_an_arrester_renders_its_gaps(self, page, base_url):
        """The partial run, on a real page: two cards, and three named absences.

        INTENT.md's first journey used to promise five cards from the two
        required fields, because `U_R` was defaulted. It is a term of F1, so
        the honest result of not naming an arrester is fewer answers -- and the
        page has to say which ones and why, rather than rendering a gap.
        """
        self._open(page, base_url)
        page.click("#cb-calc")
        page.wait_for_selector("#cb-skipped-slots", timeout=20000)
        body = page.inner_text("#cb-results")

        # The counts, because the claim is about all three. Asserting one
        # substring passes a page that names one absence, or five.
        assert page.locator("#cb-results .cb-card").count() == 3   # 2 results + 1 absence
        assert page.locator("#cb-skipped-slots .cb-skip").count() == 3
        for label in ("Interrupt voltage", "Insulation coordination", "SVL selection"):
            assert label in body, body[-500:]
        assert "svl_u_r_kv" in body, body[-500:]

        msg = "no arrester means no interrupt voltage, so no lead share to show"
        assert page.locator("#cb-lead-share").count() == 0, msg

    def test_verdicts_are_carried_by_text_not_colour(self, page, base_url):
        """Accessibility, per INTENT.md — a screenshot must be readable."""
        self._calculate(page, base_url)
        body = page.inner_text("#cb-results")
        assert ("PASS" in body) or ("FAIL" in body)
        assert ("WITHIN LIMIT" in body) or ("EXCEEDS LIMIT" in body)
