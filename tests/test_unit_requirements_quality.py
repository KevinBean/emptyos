"""requirements wording checks (quality.py) — each rule pinned both ways.

Every rule must fire on the shape it names and stay silent on healthy
statements. The healthy set mixes statements from the mock substation spec
(`mock-substation-compliance`), false positives a hostile review measured on
the KB's clause notes (2026-10-03: "may" as permission, "as required by",
a quantity adjective followed by its number), and constructed cases.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from helpers import load_app_module

quality = load_app_module("requirements", "quality")
req = load_app_module("requirements", "app")
lint = quality.lint_statement


def codes(text, priority=None):
    return [f["code"] for f in lint(text, priority)]


HEALTHY = [
    # mock substation spec
    "The Contractor shall submit the earthing design report not less than 20 working days before installation.",
    "Grid conductors must be bare stranded copper of not less than 70 mm² cross-section.",
    "Flexible braids used for gate connections must be tinned copper of not less than 50 mm².",
    "Where this specification conflicts with a referenced standard, the more onerous requirement shall apply.",
    # measured on the KB clause notes
    "Plates shall be installed as required by AS 2067.",
    "The busbar shall be sufficient to withstand 25 kA for 1 s.",
    "The substation shall be energised by 31 May 2027.",
    "The Principal shall have access to inspect the works should he elect to do so.",
    "Should the cable be laid differently, the Contractor shall re-rate it.",
    "Pegs shall protrude approximately 300 mm above ground level.",
    # constructed
    "The relay shall trip within 100 ms under normal operating conditions.",
    "Fast-acting fuses shall be fitted to every outgoing circuit.",
    "Breakers of Type XXXX shall be listed in Schedule 3.",
    "The fastener shall be stainless steel.",
    "The inappropriate cover shall be replaced.",
]


@pytest.mark.parametrize("text", HEALTHY)
def test_healthy_statements_draw_no_finding(text):
    assert lint(text) == [], lint(text)


def test_no_obligation_word():
    f = lint("The cabinet is painted grey.")
    assert [x["code"] for x in f] == ["no-obligation"] and "description" in f[0]["message"]


def test_a_prohibition_without_shall_not_is_told_to_say_shall_not():
    f = lint("Below-ground joints are not permitted unless exothermically welded.")
    assert [x["code"] for x in f] == ["no-obligation"] and "shall not" in f[0]["message"]


@pytest.mark.parametrize("priority", [None, "must", "could"])
def test_should_is_flagged_unless_the_priority_agrees(priority):
    f = lint("Test records should be provided as PDF.", priority)
    assert [x["code"] for x in f] == ["weak-modal"] and f[0]["match"] == "should"


def test_should_with_a_should_priority_is_consistent():
    assert lint("Test records should be provided as PDF.", "should") == []


def test_should_beside_shall_is_still_flagged():
    assert "weak-modal" in codes("The panel shall be painted and should be galvanised.", "must")


def test_a_pure_permission_is_named_as_one():
    """"may" grants permission: never told to become "shall", but a statement
    that only permits has nothing a test can verify, and says so accurately."""
    f = lint("The Contractor may propose an alternative earthing arrangement for approval.")
    assert [x["code"] for x in f] == ["no-obligation"] and "permission" in f[0]["message"]
    assert "use" not in f[0]["message"].lower()


def test_may_is_permission_not_a_finding():
    assert lint("The Tenderer may offer an alternative and shall price it separately.") == []


def test_two_obligations_in_one_statement():
    f = lint("Continuity shall be verified and the results shall be recorded.")
    assert [x["code"] for x in f] == ["compound"] and f[0]["match"] == "shall, shall"


@pytest.mark.parametrize("word", [
    "adequate", "as appropriate", "etc", "and/or", "approximately", "user-friendly", "user friendly",
    "minimised", "state-of-the-art", "easy-to-use", "suitably", "as required",
])
def test_vague_terms(word):
    f = lint(f"The enclosure shall provide {word} protection.")
    assert [x["code"] for x in f] == ["vague"] and f[0]["match"].lower() == word


def test_a_quantity_word_with_no_number_after_it_is_still_vague():
    assert codes("The earth grid shall be sufficient for the site.") == ["vague"]


@pytest.mark.parametrize("ph", ["TBD", "TBC", "to be confirmed", "???", "待定", "？？"])
def test_placeholders(ph):
    f = [x for x in lint(f"The cable rating shall be {ph} A.") if x["code"] == "placeholder"]
    assert f and f[0]["severity"] == "error" and f[0]["match"] == ph


def test_a_chinese_statement_is_not_judged_by_english_rules():
    """系统应记录日志 = "the system shall log" — 应 is the obligation word."""
    assert lint("需求：系统应记录日志 §3") == []
    assert lint("需求：系统应记录日志，应用于所有响应") == []


def test_a_chinese_statement_still_gets_the_placeholder_check():
    assert codes("系统应记录日志，待定") == ["placeholder"]


def test_an_english_statement_quoting_a_chinese_term_is_still_checked():
    assert "weak-modal" in codes("The cable shall be 交联聚乙烯 insulated and should be adequate.", "must")


def test_empty_statement():
    assert codes("   ") == ["empty"]


# ── the project report (app.py::quality_report) ───────────────────────

def _note(rid, status="proposed", priority="must"):
    project, req_id = rid.split("~")
    return {"path": f"{project}/{req_id}.md",
            "properties": {"project": project, "req_id": req_id, "title": req_id,
                           "status": status, "priority": priority}}


def _report(project="", notes=None, bodies=None):
    app = req.RequirementsApp.__new__(req.RequirementsApp)
    notes = notes if notes is not None else [
        _note("p~REQ-001"), _note("p~REQ-002"), _note("q~REQ-001"),
        _note("p~REQ-003", status="superseded"), _note("p~REQ-004", priority="should"),
    ]
    bodies = bodies or {"p/REQ-001.md": "The relay shall trip within 100 ms.",
                        "p/REQ-002.md": "The relay should trip quickly.",
                        "q/REQ-001.md": "The fan shall run TBD hours.",
                        "p/REQ-003.md": "The relay should trip TBD.",
                        "p/REQ-004.md": "The panel should be painted grey."}
    threads = []

    def read(path, section):
        threads.append(threading.current_thread() is threading.main_thread())
        return bodies[path] if section == "Statement" else ""

    app.vault_query = lambda tags=None, **kw: notes if tags == [req.TAG] else []
    app.vault_read_section = read
    return asyncio.run(app.quality_report(project)), threads


def test_report_lists_only_flagged_statements_of_the_project():
    rep, _ = _report("p")
    assert rep["checked"] == 3                      # REQ-003 is superseded: not checked
    assert [r["id"] for r in rep["flagged"]] == ["p~REQ-002"]
    assert {f["code"] for f in rep["flagged"][0]["findings"]} == {"weak-modal", "vague"}


def test_report_applies_each_requirements_own_priority():
    rep, _ = _report("p")
    assert "p~REQ-004" not in [r["id"] for r in rep["flagged"]]   # should + priority should


def test_report_without_a_project_covers_every_project():
    rep, _ = _report("")
    assert rep["checked"] == 4
    assert [r["id"] for r in rep["flagged"]] == ["p~REQ-002", "q~REQ-001"]


def test_report_reads_statements_off_the_event_loop():
    _, threads = _report("p")
    assert threads and not any(threads), "a statement was read on the event loop thread"


def test_duplicate_ids_are_each_checked_from_their_own_note():
    notes = [_note("p~REQ-001"), dict(_note("p~REQ-001"), path="p/REQ-001-copy.md")]
    bodies = {"p/REQ-001.md": "The relay shall trip.", "p/REQ-001-copy.md": "The relay shall trip TBD."}
    rep, _ = _report("p", notes, bodies)
    assert rep["checked"] == 2 and len(rep["flagged"]) == 1
