"""Unit tests for the Attested Memory pure core (emptyos/sdk/attested_memory.py).

Pure functions — no daemon, no kernel, no I/O. Covers the trust function's
verdict matrix (docs/MEMORY.md §4), scope gating (invariant #5), and the
fidelity audit (§8).
"""

from datetime import date

import pytest

from emptyos.sdk.attested_memory import (
    ASSERTED,
    INFERRED,
    RETIRED,
    SUSPECT,
    TENTATIVE,
    TRUSTED,
    TRUST_FUNCTION_VERSION,
    attestation_of,
    classify_trust,
    fidelity_audit,
    is_machine_touched,
    is_time_sensitive,
)

NOW = date(2026, 6, 16)


def _ago(days: int) -> str:
    from datetime import timedelta

    return (NOW - timedelta(days=days)).isoformat()


# ── attestation_of ──


def test_attestation_explicit_wins():
    assert attestation_of({"attestation": "imported", "author": "ai"}) == "imported"


def test_attestation_from_author():
    assert attestation_of({"author": "user"}) == ASSERTED
    assert attestation_of({"author": "both"}) == ASSERTED
    assert attestation_of({"author": "ai"}) == INFERRED


def test_attestation_unknown_is_empty():
    assert attestation_of({}) == ""
    assert attestation_of({"attestation": "bogus"}) == ""


# ── classify_trust verdict matrix ──


def test_user_asserted_is_trusted_without_verification():
    # A user-stated fact stays true until superseded — no last_verified needed.
    v = classify_trust({"author": "user"}, now=NOW)
    assert v.level == TRUSTED
    assert v.attestation == ASSERTED
    assert v.version == TRUST_FUNCTION_VERSION


def test_ai_inferred_unconfirmed_is_tentative():
    v = classify_trust({"author": "ai"}, now=NOW)
    assert v.level == TENTATIVE
    assert "unconfirmed" in " ".join(v.reasons)


def test_ai_inferred_confirmed_fresh_is_trusted():
    v = classify_trust({"author": "ai", "last_verified": _ago(10)}, now=NOW)
    assert v.level == TRUSTED
    assert v.attestation == INFERRED  # origin preserved, not promoted
    assert v.age_days == 10


def test_ai_inferred_confirmation_stale_is_suspect():
    v = classify_trust(
        {"author": "ai", "last_verified": _ago(400)}, now=NOW, stale_days=180
    )
    assert v.level == SUSPECT


def test_superseded_is_retired_regardless():
    v = classify_trust(
        {"author": "user", "superseded_by": "newer-note"}, now=NOW
    )
    assert v.level == RETIRED
    assert "newer-note" in " ".join(v.reasons)


def test_asserted_time_sensitive_stale_is_suspect():
    v = classify_trust(
        {"author": "user", "due": "2026-07-01", "last_verified": _ago(400)},
        now=NOW,
        stale_days=180,
    )
    assert v.level == SUSPECT


def test_asserted_time_sensitive_but_never_verified_stays_trusted():
    # No prior verification to go stale → a freshly-stated due date is fine.
    v = classify_trust({"author": "user", "due": "2026-07-01"}, now=NOW)
    assert v.level == TRUSTED


def test_unknown_provenance_fails_closed_to_tentative():
    v = classify_trust({"tags": ["kb"]}, now=NOW)
    assert v.level == TENTATIVE
    assert "unknown provenance" in " ".join(v.reasons)


def test_imported_is_trusted():
    v = classify_trust({"attestation": "imported"}, now=NOW)
    assert v.level == TRUSTED


def test_as_of_used_as_verification_fallback():
    v = classify_trust({"author": "ai", "as_of": _ago(5)}, now=NOW)
    assert v.level == TRUSTED
    assert v.age_days == 5


# ── is_time_sensitive ──


def test_time_sensitive_detection():
    assert is_time_sensitive({"expires_at": "2026-09-01"})
    assert is_time_sensitive({"next_review": "2026-07-01"})
    assert not is_time_sensitive({"author": "user"})


# ── is_machine_touched (scope, invariant #5) ──


def test_machine_touched_ai_author():
    assert is_machine_touched({"author": "ai"})
    assert is_machine_touched({"author": "both"})


def test_machine_touched_explicit_attestation():
    assert is_machine_touched({"attestation": "observed"})


def test_machine_touched_memory_tag():
    assert is_machine_touched({"tags": ["aura-memory"]})
    assert is_machine_touched({"tags": ["KB"]})  # case-insensitive


def test_hand_written_vault_is_exempt():
    # User-authored daily note with no provenance markers → out of scope.
    assert not is_machine_touched({"author": "user", "tags": ["daily"]})
    assert not is_machine_touched({"tags": ["person"]})
    assert not is_machine_touched({})


# ── fidelity_audit (§8) ──


def _row(path, props):
    return {"path": path, "properties": props}


def test_fidelity_audit_scopes_and_scores():
    rows = [
        # curated user fact (in scope via memory tag) → trusted
        _row("a.md", {"author": "user", "tags": ["aura-memory"]}),
        _row("b.md", {"author": "ai"}),                            # tentative
        _row("c.md", {"author": "ai", "last_verified": _ago(400)}),  # suspect
        _row("d.md", {"author": "ai", "superseded_by": "x"}),      # retired
        _row("e.md", {"author": "user", "tags": ["daily"]}),       # EXEMPT (hand-written)
    ]
    report = fidelity_audit(rows, now=NOW, stale_days=180)
    assert report.total == 4  # e.md excluded by scope
    assert report.counts[TRUSTED] == 1
    assert report.counts[TENTATIVE] == 1
    assert report.counts[SUSPECT] == 1
    assert report.counts[RETIRED] == 1
    assert report.dial == 25  # 1 trusted / 4
    assert report.version == TRUST_FUNCTION_VERSION


def test_fidelity_audit_stale_list_prioritizes_suspect_then_oldest():
    rows = [
        _row("tentative.md", {"author": "ai"}),
        _row("suspect-old.md", {"author": "ai", "last_verified": _ago(900)}),
        _row("suspect-new.md", {"author": "ai", "last_verified": _ago(300)}),
    ]
    report = fidelity_audit(rows, now=NOW, stale_days=180)
    paths = [s["path"] for s in report.stale]
    # suspect before tentative; among suspects, oldest verification first
    assert paths == ["suspect-old.md", "suspect-new.md", "tentative.md"]


def test_fidelity_audit_exposes_source_and_verification_facts():
    verified = _ago(400)
    report = fidelity_audit([
        _row("claim.md", {
            "author": "ai",
            "last_verified": verified,
            "source": "https://example.test/source",
        })
    ], now=NOW, stale_days=180)
    claim = report.stale[0]
    assert claim["attestation"] == "inferred"
    assert claim["last_verified"] == verified
    assert claim["source"] == "https://example.test/source"


def test_fidelity_audit_budget_caps_stale_list():
    rows = [_row(f"n{i}.md", {"author": "ai"}) for i in range(50)]
    report = fidelity_audit(rows, now=NOW, budget=10)
    assert len(report.stale) == 10
    assert report.total == 50


def test_fidelity_audit_empty_dial_is_zero():
    report = fidelity_audit([], now=NOW)
    assert report.dial == 0
    assert report.total == 0


def test_fidelity_audit_custom_memory_tags():
    rows = [_row("x.md", {"tags": ["my-notes"]})]
    # default scope: my-notes is not a memory tag → excluded
    assert fidelity_audit(rows, now=NOW).total == 0
    # custom scope includes it
    assert fidelity_audit(rows, now=NOW, memory_tags=frozenset({"my-notes"})).total == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
