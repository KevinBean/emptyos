import importlib.util
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


MODULE = _load("apply_routing_evidence_batch", "apply_routing_evidence_batch.py")
AUDIT = _load("audit_for_routing_test", "audit_evidence_graph.py")


FOUR_COLUMN = """## Delta disposition

| Domain | Disposition | Target | Reason |
|---|---|---|---|
| Knowledge fragments | digest-contained | This digest | The digest keeps the caveats. |
| Calculations and engineering | digest-contained | This digest | Assumptions retained. |

No living Vault note was changed.
"""


def test_patch_adds_the_fifth_column_and_preserves_the_first_four():
    """The reason text is the original digest's own words, not ours to reword."""
    patched = MODULE.patch_table(
        FOUR_COLUMN,
        {
            "Knowledge fragments": {
                "routing_evidence": (
                    "queries: cable rating; 载流量 · "
                    "checked: [[30_Resources/KB/cable-rating]]"
                )
            }
        },
    )
    assert MODULE.HEADER in patched
    assert MODULE.SEPARATOR in patched

    rows = {row["domain"]: row for row in AUDIT.delta_dispositions(patched)}
    patched_row = rows["Knowledge fragments"]
    assert patched_row["reason"] == "The digest keeps the caveats."
    assert patched_row["target"] == "This digest"
    assert AUDIT.valid_digest_contained_routing_evidence(
        patched_row["routing_evidence"]
    )

    # An untouched row keeps its cells and gains only a placeholder.
    untouched = rows["Calculations and engineering"]
    assert untouched["reason"] == "Assumptions retained."
    assert untouched["routing_evidence"] == "-"
    assert not AUDIT.valid_digest_contained_routing_evidence(
        untouched["routing_evidence"]
    )


def test_patch_is_idempotent_on_an_already_five_column_table():
    evidence = "queries: cable rating · checked: [[30_Resources/KB/x]]"
    once = MODULE.patch_table(
        FOUR_COLUMN, {"Knowledge fragments": {"routing_evidence": evidence}}
    )
    twice = MODULE.patch_table(
        once, {"Knowledge fragments": {"routing_evidence": evidence}}
    )
    assert once == twice


def test_no_candidate_found_is_accepted_evidence():
    """An honest empty search is a receipt; a blank cell is not."""
    patched = MODULE.patch_table(
        FOUR_COLUMN,
        {
            "Knowledge fragments": {
                "routing_evidence": (
                    "queries: obscure term; 冷门 · checked: no candidate found"
                )
            }
        },
    )
    row = next(
        r
        for r in AUDIT.delta_dispositions(patched)
        if r["domain"] == "Knowledge fragments"
    )
    assert AUDIT.valid_digest_contained_routing_evidence(
        row["routing_evidence"]
    )


def test_a_pipe_in_a_patched_cell_cannot_split_the_row():
    """A raw pipe would silently become two cells when the auditor re-reads."""
    patched = MODULE.patch_table(
        FOUR_COLUMN,
        {
            "Knowledge fragments": {
                "disposition": "reused-no-change",
                "target": "[[30_Resources/KB/x]]",
                "reason": "Covered by A | B already",
                "routing_evidence": (
                    "queries: a; b · checked: [[30_Resources/KB/x]]"
                ),
            }
        },
    )
    rows = AUDIT.delta_dispositions(patched)
    row = next(r for r in rows if r["domain"] == "Knowledge fragments")
    assert row["disposition"] == "reused-no-change"
    # The pipe survives as a divides sign. A backslash escape would not:
    # `markdown_table_cells` splits on the raw character regardless.
    assert row["reason"] == "Covered by A ∣ B already"
    assert AUDIT.valid_digest_contained_routing_evidence(
        row["routing_evidence"]
    )
    assert len(rows) == 2


def test_a_row_whose_reason_hides_a_pipe_is_refused_not_guessed():
    """Cell count cannot distinguish this from a real five-column row.

    `| A | B | C | Compared A | B kept |` parses as five cells, exactly like a
    row that already carries a receipt. Overwriting the last one would eat
    real prose, so the ambiguity is refused and left for a human. Existing
    digests carry no pipes, so this costs nothing today and cannot silently
    destroy text tomorrow.
    """
    text = FOUR_COLUMN.replace(
        "The digest keeps the caveats.", "Compared A | B and kept both."
    )
    with pytest.raises(MODULE.APPLY.ApplyRefused) as excinfo:
        MODULE.patch_table(
            text,
            {
                "Knowledge fragments": {
                    "routing_evidence": (
                        "queries: x · checked: [[30_Resources/KB/x]]"
                    )
                }
            },
        )
    assert "Ambiguous" in str(excinfo.value)


def test_a_stale_placeholder_receipt_is_safely_replaced():
    """A five-column row whose receipt is `-` is unambiguous, so patch it."""
    text = FOUR_COLUMN.replace(
        "| Knowledge fragments | digest-contained | This digest | "
        "The digest keeps the caveats. |",
        "| Knowledge fragments | digest-contained | This digest | "
        "The digest keeps the caveats. | - |",
    )
    patched = MODULE.patch_table(
        text,
        {
            "Knowledge fragments": {
                "routing_evidence": (
                    "queries: x · checked: [[30_Resources/KB/x]]"
                )
            }
        },
    )
    row = next(
        r
        for r in AUDIT.delta_dispositions(patched)
        if r["domain"] == "Knowledge fragments"
    )
    assert row["reason"] == "The digest keeps the caveats."
    assert AUDIT.valid_digest_contained_routing_evidence(
        row["routing_evidence"]
    )


def test_patch_refuses_a_digest_with_no_disposition_table():
    with pytest.raises(MODULE.APPLY.ApplyRefused):
        MODULE.patch_table("## Digest\n\nJust prose.\n", {})


def test_routing_section_variant_is_patched_too():
    """ChatGPT canonical digests use `## Routing`, Claude uses the other."""
    text = FOUR_COLUMN.replace("## Delta disposition", "## Routing")
    patched = MODULE.patch_table(
        text,
        {
            "Knowledge fragments": {
                "routing_evidence": (
                    "queries: x · checked: [[30_Resources/KB/x]]"
                )
            }
        },
    )
    row = next(
        r
        for r in AUDIT.delta_dispositions(patched)
        if r["domain"] == "Knowledge fragments"
    )
    assert AUDIT.valid_digest_contained_routing_evidence(
        row["routing_evidence"]
    )


WITH_COVERAGE = """## Domain coverage

| Domain | Status | Finding |
|---|---|---|
| Knowledge fragments | delta | A red-black tree needs rotation logic. |
| Tools, code, and calculators | delta | The artifact is a skeleton. |

""" + FOUR_COLUMN


def test_the_domain_coverage_table_is_left_completely_alone():
    """It also starts with `Domain` and lists domain labels down column one.

    A document-wide row match relabels its header and pads its rows, and the
    auditor keeps passing because `domain_statuses` reads by position. That
    combination would have quietly corrupted every digest in the drain, so the
    patch is scoped to the disposition section by name.
    """
    patched = MODULE.patch_table(
        WITH_COVERAGE,
        {
            "Knowledge fragments": {
                "routing_evidence": (
                    "queries: x · checked: [[30_Resources/KB/x]]"
                )
            }
        },
    )
    coverage = AUDIT.markdown_section(patched, "Domain coverage")
    assert "| Domain | Status | Finding |" in coverage
    assert "| Domain | Disposition |" not in coverage
    assert (
        "| Knowledge fragments | delta | A red-black tree needs rotation "
        "logic. |" in coverage
    )
    # And the statuses still parse to exactly what they were.
    assert AUDIT.domain_statuses(patched) == AUDIT.domain_statuses(
        WITH_COVERAGE
    )
    # while the disposition row did get its receipt.
    row = next(
        r
        for r in AUDIT.delta_dispositions(patched)
        if r["domain"] == "Knowledge fragments"
    )
    assert AUDIT.valid_digest_contained_routing_evidence(
        row["routing_evidence"]
    )


def test_content_outside_the_disposition_section_is_byte_identical():
    patched = MODULE.patch_table(
        WITH_COVERAGE,
        {
            "Knowledge fragments": {
                "routing_evidence": (
                    "queries: x · checked: [[30_Resources/KB/x]]"
                )
            }
        },
    )
    before_head = WITH_COVERAGE.split("## Delta disposition")[0]
    after_head = patched.split("## Delta disposition")[0]
    assert before_head == after_head


def test_backup_path_is_unique_per_content():
    a = MODULE.backup_path("40_Archive/AI Conversations/claude/x.md", "one")
    b = MODULE.backup_path("40_Archive/AI Conversations/claude/x.md", "two")
    assert a != b
    assert a.startswith("99_Attachments/temp-backup/x-")
    assert a.endswith(".md")
