"""System app tests: Trust Loop — 29 use cases.

Acceptance criteria → tests (from apps/public/standard/trust-loop/ALGORITHM.md):
  AC1  POST /api/calc reproduces the published case within 0.5%
                                                  → test_published_case
  AC2  Every result carries compute provenance (method + version, no model)
                                                  → test_calc_provenance
  AC3  The result shows the working, per element  → test_contributions_present
  AC4  Illegal inputs are refused, not guessed    → test_negative_impedance_refused,
                                                    test_inconsistent_transformer_refused
  AC5  GET /api/methods lists the one method      → test_methods_listing
  AC6  GET /api/conformance lists the published case
                                                  → test_conformance_listing
  AC7  POST /api/conformance/run passes the gate  → test_conformance_gate_passes
  AC8  Stage 2 is a live artifact, not a claim    → test_algorithm_document_served
  AC22 GET /api/assurance lists the controlled package -> test_assurance_package_listing
  AC9  GET /api/published-case returns the anchor → test_published_case_endpoint
  AC10 GET /trust-loop/ renders without JS errors → test_page_loads
  AC11 "Load the published case" fills the form and shows the anchor verdict
                                                  → test_published_button_flow
  AC12 Both graphics draw from engine output      → test_graphics_render
  AC13 Editing an input live-recomputes           → test_live_recompute
  AC19 The loop is a spine; the calculator leads and stays above the fold
                                                  → test_loop_is_a_spine_and_the_calculator_leads,
                                                    test_spine_navigates,
                                                    test_narrow_layout_does_not_trap_the_scroll
  AC17 Each stage lists its actions in the order they happen, and what decides
                                                  → test_actions_are_ordered_by_time_not_by_actor,
                                                    test_no_one_gets_a_vote_at_the_gate
  AC20 Cards show what they hand to the next stage; the gate is the pivot
                                                  → test_cards_show_what_they_hand_to_the_next_stage
  AC21 Stages 1 and 2 open in place, without leaving the page
                                                  → test_stages_one_and_two_open_without_leaving_the_page
  AC18 Stage 2 lands on a rendered document, not a raw file
                                                  → test_algorithm_doc_opens_in_the_shared_viewer
  AC16 The gate runs on load and shows, without a click
                                                  → test_gate_runs_and_shows_on_load
  AC15 The stage cards report the running system, not static claims
                                                  → test_stage_cards_carry_live_evidence
  AC14 The report runs inputs to result, checkable line by line
                                                  → test_report_is_a_full_derivation,
                                                    test_report_tracks_posted_inputs,
                                                    test_report_rendered_on_page
Edge/regression (no AC): test_sensitivity_endpoint
"""

import pytest
from helpers import assert_dict_response
from page_helpers import assert_no_js_errors

PUBLISHED = {
    "u_net": 20000.0, "i_k_net": 14400.0, "c": 1.1,
    "r_mv": 0.360, "x_mv": 0.335,
    "s_n": 400000.0, "u_2n": 400.0, "vk_pct": 4.0, "pk_pct": 3.0,
    "r_lv": 0.000388, "x_lv": 0.000395,
}


@pytest.mark.api
class TestTrustLoopAPI:
    def test_published_case(self, http_client):
        data = assert_dict_response(http_client.post("/trust-loop/api/calc", json=PUBLISHED))
        assert data["result"]["i_k3_a"] == pytest.approx(14943.0, rel=0.005)

    def test_calc_provenance(self, http_client):
        data = assert_dict_response(http_client.post("/trust-loop/api/calc", json=PUBLISHED))
        assert data["provenance"]["method"] == "impedance_referral"
        assert data["provenance"].get("method_version")

    def test_contributions_present(self, http_client):
        data = assert_dict_response(http_client.post("/trust-loop/api/calc", json=PUBLISHED))
        names = [c["name"] for c in data["result"]["contributions"]]
        assert names == ["Supply network", "MV cable", "Transformer", "LV cable"]
        assert data["result"]["dominant"] == "Transformer"

    def test_negative_impedance_refused(self, http_client):
        data = assert_dict_response(
            http_client.post("/trust-loop/api/calc", json={**PUBLISHED, "r_mv": -1}))
        assert "error" in data

    def test_inconsistent_transformer_refused(self, http_client):
        data = assert_dict_response(
            http_client.post("/trust-loop/api/calc", json={**PUBLISHED, "pk_pct": 90}))
        assert "error" in data

    def test_methods_listing(self, http_client):
        data = assert_dict_response(http_client.get("/trust-loop/api/methods"))
        assert [m["id"] for m in data["fault"]] == ["impedance_referral"]

    def test_conformance_listing(self, http_client):
        data = assert_dict_response(http_client.get("/trust-loop/api/conformance"))
        assert "abb-tap2-400kva-20kv" in [c["case_id"] for c in data["cases"]]

    def test_conformance_gate_passes(self, http_client):
        data = assert_dict_response(http_client.post("/trust-loop/api/conformance/run", json={}))
        assert data["results"], "gate ran no cases"
        assert all(r["passed"] for r in data["results"])

    def test_published_case_endpoint(self, http_client):
        data = assert_dict_response(http_client.get("/trust-loop/api/published-case"))
        assert data["published_result_a"] == pytest.approx(14943.0)
        assert data["inputs"]["s_n"] == pytest.approx(400000.0)

    def test_assurance_package_listing(self, http_client):
        data = assert_dict_response(http_client.get("/trust-loop/api/assurance"))
        assert data["method"] == "trust-loop-v1"
        assert data["status"] == "demonstration"
        docs = {row["key"]: row for row in data["documents"]}
        assert set(docs) == {
            "index", "source_pack", "algorithm", "implementation",
            "validation", "app_spec", "report_spec", "release_verification",
        }
        assert all(row["available"] for row in docs.values())

    def test_algorithm_document_served(self, http_client):
        r = http_client.get("/trust-loop/api/algorithm")
        assert r.status_code == 200
        body = r.text
        # Stage 2 must be the real spec, not a stub: it has to carry the
        # method, the limits and the anchor.
        assert "Applicability limits" in body
        assert "14 943" in body or "14943" in body
        assert "1SDC007101G0202" in body

    def test_report_is_a_full_derivation(self, http_client):
        r = http_client.get("/trust-loop/api/report")
        assert r.status_code == 200
        body = r.text
        # Inputs through to result, not a summary.
        for section in ("## Given", "## Supply network", "## MV cable",
                        "## Transformer", "## LV cable", "## Total and result", "## Result"):
            assert section in body, f"report missing {section}"
        assert "1SDC007101G0202" in body       # cites its source
        assert "14.9346" in body or "14934" in body

    def test_report_tracks_posted_inputs(self, http_client):
        r = http_client.post("/trust-loop/api/report", json={**PUBLISHED, "s_n": 630000})
        assert r.status_code == 200
        assert "630000" in r.text

    def test_sensitivity_endpoint(self, http_client):
        data = assert_dict_response(http_client.post(
            "/trust-loop/api/sensitivity", json={**PUBLISHED, "element": "transformer"}))
        assert len(data["points"]) > 5
        assert data["element"] == "transformer"


@pytest.mark.interactive
class TestTrustLoopUI:
    def test_page_loads(self, page, base_url, page_errors):
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("#loop-strip .tl-stage")
        assert_no_js_errors(page_errors)

    def test_gate_runs_and_shows_on_load(self, page, base_url, page_errors):
        """On this app the gate is the subject, so it must not need a click.

        Also pins that a reader who collapses the panel is not overridden by
        the next re-render — the opt-in only sets the INITIAL state.
        """
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("details.eos-conf-panel table.eos-conf-diffs")
        panel = page.locator("details.eos-conf-panel")
        assert panel.evaluate("d => d.open") is True
        body = panel.inner_text()
        assert "pass" in body.lower()
        assert "14943" in body and "0.5" in body
        # collapsing survives a re-run
        panel.evaluate("d => { d.open = false; }")
        page.click("button.eos-conf-run")
        page.wait_for_timeout(700)
        assert panel.evaluate("d => d.open") is False
        assert_no_js_errors(page_errors)

    def test_loop_is_a_spine_and_the_calculator_leads(self, page, base_url, page_errors):
        """The stages are context; the calculator is what a visitor presses.

        Pins that the loop sits in a sticky rail beside the work surface and
        the answer is reachable on the first screen — the grid-on-top layout
        spent it all on the stages.
        """
        page.set_viewport_size({"width": 1400, "height": 1000})
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("#loop-strip .tl-stage")
        assert page.locator(".tl-rail").count() == 1
        assert page.evaluate("getComputedStyle(document.querySelector('.tl-rail')).position") == "sticky"
        assert page.evaluate(
            "document.getElementById('out-ik').getBoundingClientRect().top < 1000"
        ), "the answer must be on the first screen"
        assert_no_js_errors(page_errors)

    def test_spine_navigates(self, page, base_url, page_errors):
        page.set_viewport_size({"width": 1400, "height": 1000})
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("#loop-strip .tl-stage")
        page.locator("#loop-strip .tl-stage").nth(5).click()
        page.wait_for_timeout(600)
        assert page.locator("#loop-strip .tl-stage.on").count() == 1
        assert_no_js_errors(page_errors)

    def test_narrow_layout_does_not_trap_the_scroll(self, page, base_url, page_errors):
        """A sticky rail taller than the viewport is a scroll trap, so below
        the breakpoint it must un-stick and the page must not scroll sideways."""
        page.set_viewport_size({"width": 820, "height": 900})
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("#loop-strip .tl-stage")
        assert page.evaluate("getComputedStyle(document.querySelector('.tl-rail')).position") == "static"
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
        ), "narrow layout scrolls horizontally"
        assert_no_js_errors(page_errors)

    def test_actions_are_ordered_by_time_not_by_actor(self, page, base_url, page_errors):
        """Each stage lists its steps in the order they happen.

        Grouping by actor hid the interesting part: on stage 3 a model drafts
        and a person reviews after; on stage 5 the person decides first and a
        model drafts to it. Same two actors, opposite order.
        """
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("#loop-strip .tl-stage .tl-roles")
        cards = page.locator("#loop-strip .tl-stage")

        def actors(i):
            return cards.nth(i).locator(".tl-roles .r-k:not(.de)").all_inner_texts()

        assert [a.lower() for a in actors(2)] == ["ai", "human"], "stage 3: model drafts, person reviews"
        assert [a.lower() for a in actors(4)] == ["human", "ai"], "stage 5: person decides, model drafts"
        # every stage answers to something, even the ones with no actor
        for i in range(7):
            assert cards.nth(i).locator(".tl-roles .r-v.de").count() == 1, f"stage {i + 1}"
        assert_no_js_errors(page_errors)

    def test_no_one_gets_a_vote_at_the_gate(self, page, base_url, page_errors):
        """Stage 4 is the reason the split is worth drawing."""
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("#loop-strip .tl-stage .tl-roles")
        gate = page.locator("#loop-strip .tl-stage").nth(3)
        actors = [a.lower() for a in gate.locator(".tl-roles .r-k:not(.de)").all_inner_texts()]
        assert "ai" not in actors and "human" not in actors, actors
        assert "published number" in gate.inner_text()
        assert_no_js_errors(page_errors)

    def test_cards_show_what_they_hand_to_the_next_stage(self, page, base_url, page_errors):
        """The loop is a chain: each stage's output is the next one's input."""
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("#loop-strip .tl-stage .p")
        assert page.locator("#loop-strip .tl-stage .p").count() == 7
        # The gate is the pivot — a fail sends stage 3 round again.
        assert page.locator("#loop-strip .tl-stage.pivot").count() == 1
        assert "goes round again" in page.locator("#loop-strip .tl-stage").nth(3).inner_text()
        assert_no_js_errors(page_errors)

    def test_stages_one_and_two_open_without_leaving_the_page(self, page, base_url, page_errors):
        """Sending a reader to another app costs them the calculator."""
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("#loop-strip .tl-stage")
        page.locator("#loop-strip .tl-stage").nth(1).click()      # algorithm document
        page.wait_for_selector("#algo-doc h2")
        assert page.url.endswith("/trust-loop/"), "must not navigate away"
        assert page.locator("#algo-doc table").count() >= 3
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        page.locator("#loop-strip .tl-stage").nth(0).click()      # the source
        page.wait_for_selector("#eos-modal-overlay")
        assert "1SDC007101G0202" in page.locator("#eos-modal-overlay").inner_text()
        assert page.url.endswith("/trust-loop/")
        assert_no_js_errors(page_errors)

    def test_algorithm_doc_opens_in_the_shared_viewer(self, page, base_url, page_errors):
        """Stage 2 must land on a rendered document, not a plain-text dump."""
        page.goto(base_url + "/appdoc?app=trust-loop&file=ALGORITHM.md")
        # `#` renders as <h2>: the shared renderer shifts headings down one
        # level so note content never emits a competing <h1>.
        page.wait_for_selector("#doc h2")
        body = page.locator("#doc")
        assert "Applicability limits" in body.inner_text()
        assert body.locator("table").count() >= 3, "tables must render as tables"
        assert_no_js_errors(page_errors)

    def test_stage_cards_carry_live_evidence(self, page, base_url, page_errors):
        """The cards must report the running system, not restate claims.

        Stage 4 shows the gate's own verdict, stage 3 the method it actually
        resolved, stage 6 the real number of report lines. A card that fell
        back to its static text would pass a "does it render" check, so this
        asserts the live values specifically.
        """
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("#loop-strip .tl-stage .ev")
        cards = page.locator("#loop-strip .tl-stage")
        assert cards.count() == 7
        page.wait_for_function(
            "document.querySelectorAll('#loop-strip .ev.ok').length > 0")
        gate = page.locator("#loop-strip .tl-stage").nth(3).inner_text()
        assert "PASS" in gate and "14943" in gate, gate
        engine = page.locator("#loop-strip .tl-stage").nth(2).inner_text()
        assert "impedance_referral" in engine
        report = page.locator("#loop-strip .tl-stage").nth(5).inner_text()
        assert "lines" in report
        assert_no_js_errors(page_errors)

    def test_published_button_flow(self, page, base_url, page_errors):
        page.goto(base_url + "/trust-loop/")
        page.click("#btn-published")
        page.wait_for_function(
            "document.getElementById('out-ik').textContent.indexOf('14.9') === 0")
        assert "published answer" in page.locator("#out-verdict").inner_text()
        assert_no_js_errors(page_errors)

    def test_graphics_render(self, page, base_url, page_errors):
        page.goto(base_url + "/trust-loop/")
        page.click("#btn-published")
        page.wait_for_selector("#scene svg")
        page.wait_for_selector("#chart svg rect")
        # The schematic prints the engine's own number, not a re-derived one.
        assert "kA" in page.locator("#scene").inner_html()
        assert_no_js_errors(page_errors)

    def test_report_rendered_on_page(self, page, base_url, page_errors):
        page.goto(base_url + "/trust-loop/")
        page.click("#btn-published")
        page.wait_for_selector("#out-work table.tl-report tr.grp")
        html = page.locator("#out-work").inner_html()
        # A derived line must show its substitution, not just its answer.
        assert "0.995" in html and "&#215;" not in html.split("0.995")[0][-10:]
        assert "I_k3" in html
        assert_no_js_errors(page_errors)

    def test_live_recompute(self, page, base_url, page_errors):
        page.goto(base_url + "/trust-loop/")
        page.click("#btn-published")
        page.wait_for_function(
            "document.getElementById('out-ik').textContent.indexOf('14.9') === 0")
        page.fill("#vk_pct", "8")
        # Doubling the transformer impedance must roughly halve the current,
        # and the anchor verdict must stop claiming the published case.
        page.wait_for_function(
            "document.getElementById('out-ik').textContent.indexOf('14.9') !== 0")
        assert "not the published case" in page.locator("#out-verdict").inner_text()
        assert_no_js_errors(page_errors)
