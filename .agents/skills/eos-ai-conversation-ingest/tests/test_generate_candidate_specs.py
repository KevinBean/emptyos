import importlib.util
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "generate_candidate_specs.py"
)
SPEC = importlib.util.spec_from_file_location(
    "generate_candidate_specs",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def candidate(**overrides):
    value = {
        "digest": "A cautious digest.",
        "source_summary": "A short exchange.",
        "tags": ["One Tag", "data/schema"],
        "domain_coverage": [
            {
                "domain": domain,
                "status": (
                    "delta"
                    if domain == "Transient/no durable delta"
                    else "not-present"
                ),
                "notes": "No durable content."
            }
            for domain in MODULE.DOMAIN_LABELS
        ],
        "decisions": [],
        "fact_checks": [],
        "privacy": "standard",
        "derived_action": "none",
        "derived_rationale": "No durable delta.",
        "review_flags": ["none"],
        "confidence": 0.9,
    }
    value.update(overrides)
    return value


def queue_item(**overrides):
    value = {
        "provider_id": "11111111-1111-4111-8111-111111111111",
        "title": "Example",
        "created_at": "2026-07-28T00:00:00Z",
        "message_count": 2,
        "attachment_count": 0,
        "capture_fidelity": "export-native",
    }
    value.update(overrides)
    return value


def test_validate_candidate_requires_exact_domain_inventory():
    normalized = MODULE.validate_candidate(candidate())
    assert set(normalized["domain_coverage"]) == set(MODULE.DOMAIN_LABELS)
    assert normalized["tags"] == ["one-tag", "data-schema"]

    duplicate = candidate()
    duplicate["domain_coverage"][-1] = dict(
        duplicate["domain_coverage"][0]
    )
    with pytest.raises(ValueError, match="Duplicate domain"):
        MODULE.validate_candidate(duplicate)


def test_response_schema_requires_nonempty_explanations():
    schema = MODULE.response_schema()
    coverage = schema["properties"]["domain_coverage"]["items"]
    assert coverage["properties"]["notes"]["minLength"] == 1
    assert coverage["properties"]["notes"]["maxLength"] == 240
    assert schema["properties"]["digest"]["minLength"] == 1
    assert schema["properties"]["digest"]["maxLength"] == 1800
    fact = schema["properties"]["fact_checks"]["items"]["properties"]
    assert fact["claim"]["minLength"] == 1
    assert fact["check"]["minLength"] == 1


def test_prompt_distinguishes_absence_from_review():
    prompt = MODULE.system_prompt()
    assert "Absence\n  of content is not uncertainty" in prompt
    assert "Never put" in prompt
    assert "`route-existing` in `decisions`" in prompt


def test_safe_auto_apply_accepts_only_small_transient_complete_sources():
    normalized = MODULE.validate_candidate(candidate())
    safe, reasons = MODULE.safe_auto_apply(
        normalized,
        item=queue_item(),
        source_chars=5000,
    )
    assert safe
    assert reasons == []

    normalized["domain_coverage"]["Work and career"] = {
        "status": "delta",
        "notes": "Possible durable work fact.",
    }
    safe, reasons = MODULE.safe_auto_apply(
        normalized,
        item=queue_item(attachment_count=1),
        source_chars=5000,
    )
    assert not safe
    assert "durable-domain:Work and career:delta" in reasons
    assert "attachments" in reasons


def test_build_spec_is_private_and_retains_model_privacy_as_metadata():
    normalized = MODULE.validate_candidate(candidate(privacy="standard"))
    item = MODULE.build_spec_item(
        provider="chatgpt",
        queue_item=queue_item(),
        candidate=normalized,
        captured_at="2026-07-28T12:00:00+10:00",
        ingestion_date="2026-07-28",
        source_chars=5000,
        model="local-test",
        envelope={},
    )
    assert item["private"] is True
    assert (
        item["candidate_review"]["model_suggested_privacy"]
        == "standard"
    )
    assert item["candidate_review"]["safe_auto_apply"] is True


def test_digest_contained_rows_carry_an_empty_routing_evidence_cell():
    """The Vault-blind worker must declare the gap, not omit the column.

    Omitting ``routing_evidence`` is what silently minted the routing-review
    debt: ``audit_evidence_graph`` then reports
    ``delta-digest-contained-routing-evidence-missing`` for every such row.
    """
    normalized = MODULE.validate_candidate(
        candidate(derived_action="digest-contained")
    )
    normalized["domain_coverage"]["Knowledge fragments"] = {
        "status": "delta",
        "notes": "A durable knowledge fragment.",
    }
    rows = MODULE.delta_dispositions(normalized)
    assert [row["domain"] for row in rows] == ["Knowledge fragments"]
    assert "routing_evidence" in rows[0]
    assert rows[0]["routing_evidence"] == ""


def test_unevidenced_digest_contained_blocks_auto_apply():
    normalized = MODULE.validate_candidate(
        candidate(derived_action="digest-contained")
    )
    normalized["domain_coverage"]["Knowledge fragments"] = {
        "status": "delta",
        "notes": "A durable knowledge fragment.",
    }
    assert MODULE.unevidenced_digest_contained(normalized) == [
        "Knowledge fragments"
    ]
    safe, reasons = MODULE.safe_auto_apply(
        normalized,
        item=queue_item(),
        source_chars=5000,
    )
    assert not safe
    assert "routing-evidence-missing:Knowledge fragments" in reasons


def test_transient_only_candidate_needs_no_routing_evidence():
    """A conversation with no durable delta stays cheaply auto-appliable."""
    normalized = MODULE.validate_candidate(candidate())
    assert MODULE.delta_dispositions(normalized) == []
    assert MODULE.unevidenced_digest_contained(normalized) == []
    safe, reasons = MODULE.safe_auto_apply(
        normalized,
        item=queue_item(),
        source_chars=5000,
    )
    assert safe
    assert reasons == []


def test_transient_yields_when_the_conversation_has_real_deltas():
    """A conversation cannot be wholly throwaway and also carry findings.

    The model marks the transient row whenever *parts* of an exchange were
    throwaway -- measured at 73% of 878 real candidates -- producing a
    coverage table that contradicts itself. The two are definitionally
    exclusive, so the blanket claim yields to the specific ones.
    """
    value = candidate()
    for row in value["domain_coverage"]:
        if row["domain"] == "Knowledge fragments":
            row["status"] = "delta"
    normalized = MODULE.validate_candidate(value)
    transient = normalized["domain_coverage"]["Transient/no durable delta"]
    assert transient["status"] == "mentioned-no-delta"
    assert "Knowledge fragments" in transient["notes"]
    assert (
        normalized["domain_coverage"]["Knowledge fragments"]["status"]
        == "delta"
    )


def test_a_genuinely_transient_conversation_keeps_its_transient_delta():
    normalized = MODULE.validate_candidate(candidate())
    assert (
        normalized["domain_coverage"]["Transient/no durable delta"]["status"]
        == "delta"
    )


def test_provider_rows_are_upserted_and_success_can_clear_error():
    old = {
        "provider_id": "same",
        "error": "old",
    }
    new = {
        "provider_id": "same",
        "digest": "new",
    }
    other = {
        "provider_id": "other",
        "digest": "keep",
    }
    assert MODULE.upsert_provider_row([old, other], new) == [new, other]
    assert MODULE.without_provider_id([old, other], "same") == [other]
