"""Pin the thin-digest advisory in the conversation-ingest evidence auditor.

The auditor validates digest *structure* -- headings present, ``derived_notes:``
declared, evidence-grammar cells well-formed. It had no view of whether the
digest said anything, which is how 1,236 digests written on 2026-08-01 (median
338 chars, 83% under the 800-char bar) passed as ``new_schema_complete``.

Both directions are pinned here: the advisory must fire on a substantive
conversation reduced to a stub, and must stay silent on a genuinely short one
and on a properly written digest.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".agents"
    / "skills"
    / "eos-ai-conversation-ingest"
    / "scripts"
    / "audit_evidence_graph.py"
)


@pytest.fixture(scope="module")
def audit():
    spec = importlib.util.spec_from_file_location("_audit_evidence_graph", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def digest(body: str) -> str:
    return f"""---
provider: chatgpt
derived_notes: []
---

## Digest

{body}

## Domain coverage

| Domain | Status | Notes |
|---|---|---|
"""


LONG = (
    "A complete export-native capture spanning three days. Kevin works through "
    "the cable thermal rating for a direct-buried trefoil circuit, arriving at "
    "a Stage 2 result of 51.6 C normal and 88.9 C under N-1, both within the "
    "90 C limit. The conversation then turns to whether the crossing is inside "
    "the installer's scope, which it is not."
)


def test_flags_substantive_conversation_reduced_to_a_stub(audit):
    """15 messages compressed to under 300 chars is the defect this exists for."""
    out = audit.thin_digest_advisory(digest("Short note about a thing."), 15)
    assert out["thin_digest"] is True
    assert out["digest_body_chars"] < audit.THIN_DIGEST_MIN_CHARS
    assert out["message_count"] == 15


def test_silent_on_a_genuinely_short_conversation(audit):
    """A 4-message exchange with a short digest is honest, not a defect."""
    out = audit.thin_digest_advisory(digest("Short note about a thing."), 4)
    assert out["thin_digest"] is False


def test_silent_on_a_properly_written_digest(audit):
    out = audit.thin_digest_advisory(digest(LONG), 53)
    assert out["thin_digest"] is False
    assert out["digest_body_chars"] >= audit.THIN_DIGEST_MIN_CHARS


def test_boundary_is_inclusive_on_messages_exclusive_on_chars(audit):
    """Exactly at the message threshold counts; exactly at the char floor does not."""
    assert audit.thin_digest_advisory(digest("x"), audit.THIN_DIGEST_MIN_MESSAGES)[
        "thin_digest"
    ] is True
    assert audit.thin_digest_advisory(
        digest("x"), audit.THIN_DIGEST_MIN_MESSAGES - 1
    )["thin_digest"] is False
    exact = "y" * audit.THIN_DIGEST_MIN_CHARS
    assert audit.thin_digest_advisory(digest(exact), 20)["thin_digest"] is False


def test_missing_digest_is_not_a_thin_digest(audit):
    """A missing digest already has its own reason; do not double-report it."""
    out = audit.thin_digest_advisory(None, 40)
    assert out["thin_digest"] is False
    assert out["digest_body_chars"] == 0


def test_measures_the_digest_section_not_the_whole_note(audit):
    """A stub digest inside a long note must still flag."""
    text = digest("Tiny.") + "\n\n## Fact-check notes\n\n" + LONG * 5
    out = audit.thin_digest_advisory(text, 30)
    assert out["thin_digest"] is True


def test_advisory_never_appears_in_reasons(audit):
    """The advisory must not be able to flip new_schema_complete.

    It ships advisory-first deliberately: 217 historical records would flag, and
    turning that into a hard failure before they are re-digested would bury the
    signal it exists to surface.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    body = src.split("def thin_digest_advisory", 1)[1]
    assert "reasons.append" not in body.split("def normalize_conversation_path")[0]
    assert "thin-digest" not in src, "advisory must not be wired as a reason string"
