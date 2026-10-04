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


MODULE = _load("apply_legacy_schema_batch", "apply_legacy_schema_batch.py")
AUDIT = _load("audit_for_legacy_test", "audit_evidence_graph.py")


LEGACY = """---
record_kind: conversation-digest
provider: chatgpt
source_conversation_id: 237c3cb7-ba8b-4637-88b8-35c7cea89b11
title: "Cable expert"
tags:
  - conversation
---

# Cable expert

## Digest

A user-only OCR dump about daily load factor and cyclic cable loading.

## Decisions and durable deltas

- No design conclusion was promoted.

## Fact-check notes

- The OCR text was not verified against the licensed standard.

## Routing

No living Vault note was changed.

## Source

[[40_Archive/AI Conversations/originals/chatgpt/x--237c3cb7]]
"""


def coverage(status="not-present"):
    return {
        domain: {"status": status, "notes": "Classified from the digest."}
        for domain in AUDIT.DOMAIN_LABELS
    }


def complete(text=LEGACY, cov=None):
    return MODULE.complete_schema(
        text,
        coverage=cov or coverage(),
        digest_path="30_Resources/conversations/cable-expert.md",
        source_path="40_Archive/AI Conversations/originals/chatgpt/x--237c3cb7.md",
        provider_id="237c3cb7-ba8b-4637-88b8-35c7cea89b11",
    )


def test_adds_exactly_the_three_things_the_auditor_requires():
    out = complete()
    assert "## Domain coverage" in out
    assert "## Evidence chain" in out
    assert "derived_notes:" in AUDIT.frontmatter(out)
    statuses = AUDIT.domain_statuses(out)
    assert all(
        statuses.get(d) in AUDIT.DOMAIN_STATUSES for d in AUDIT.DOMAIN_LABELS
    )


def test_existing_prose_is_untouched():
    """The digest, decisions, fact-check and routing were reviewed once."""
    out = complete()
    for heading in (
        "Digest",
        "Decisions and durable deltas",
        "Fact-check notes",
        "Routing",
    ):
        assert AUDIT.markdown_section(out, heading).strip() == (
            AUDIT.markdown_section(LEGACY, heading).strip()
        )


def test_coverage_lands_before_decisions_not_appended_at_the_end():
    out = complete()
    assert out.index("## Domain coverage") < out.index(
        "## Decisions and durable deltas"
    )
    assert out.index("## Evidence chain") < out.index("## Source")


def test_completion_is_idempotent():
    once = complete()
    twice = complete(once)
    assert once == twice


def test_a_pipe_in_a_note_cannot_split_the_coverage_row():
    cov = coverage()
    cov["Knowledge fragments"] = {
        "status": "delta",
        "notes": "Covers A | B distinctions.",
    }
    out = complete(cov=cov)
    statuses = AUDIT.domain_statuses(out)
    assert statuses["Knowledge fragments"] == "delta"
    assert all(
        statuses.get(d) in AUDIT.DOMAIN_STATUSES for d in AUDIT.DOMAIN_LABELS
    )


def test_a_delta_gets_a_disposition_row_owing_a_receipt():
    """Completing coverage is what makes these deltas visible at all.

    A durable delta with no derived note needs a disposition row or the record
    stays in routing review forever. The row is written here; the receipt is
    left as a placeholder because earning it means searching the Vault, which
    is a different script's job -- and a placeholder keeps the record honestly
    flagged as owing evidence rather than appearing to have it.
    """
    cov = coverage()
    cov["Knowledge fragments"] = {
        "status": "delta",
        "notes": "A durable distinction about loss-load factor.",
    }
    out = complete(cov=cov)
    rows = AUDIT.delta_dispositions(out)
    assert [r["domain"] for r in rows] == ["Knowledge fragments"]
    assert rows[0]["disposition"] == "digest-contained"
    assert rows[0]["reason"] == "A durable distinction about loss-load factor."
    assert not AUDIT.valid_digest_contained_routing_evidence(
        rows[0]["routing_evidence"]
    )


def test_transient_only_digest_gets_no_disposition_table():
    cov = coverage()
    cov["Transient/no durable delta"] = {
        "status": "delta",
        "notes": "Nothing durable anywhere.",
    }
    out = complete(cov=cov)
    assert "## Delta disposition" not in out
    assert AUDIT.delta_dispositions(out) == []


def test_refuses_a_digest_without_frontmatter():
    with pytest.raises(MODULE.APPLY.ApplyRefused):
        complete("# No frontmatter\n\n## Digest\n\nBody.\n")


def test_evidence_chain_does_not_claim_a_mutation_that_never_happened():
    """A backfill records that nothing was routed, not that something was."""
    out = complete()
    chain = AUDIT.markdown_section(out, "Evidence chain")
    assert "none recorded by this backfill" in chain
    assert "237c3cb7" in chain
