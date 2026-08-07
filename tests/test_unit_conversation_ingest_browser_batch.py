"""Unit tests for browser-batch conversation ingestion helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "eos-ai-conversation-ingest"
    / "scripts"
    / "apply_no_mutation_browser_batch.py"
)
SPEC = importlib.util.spec_from_file_location("browser_batch_ingest", SCRIPT)
assert SPEC and SPEC.loader
INGEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INGEST)


def _spec(mutation: dict | None = None) -> dict:
    coverage = {
        domain: {"status": "not-present", "notes": "-"}
        for domain in INGEST.DOMAIN_LABELS
    }
    return {
        "provider_id": "1234567890abcdef",
        "title": "Example",
        "slug": "example",
        "ingestion_date": "2026-07-28",
        "captured_at": "2026-07-28T01:00:00+10:00",
        "provider_list_date": "2026-01-01",
        "provider_list_label": "1 Jan",
        "domain_coverage": coverage,
        "digest": "Digest.",
        "derived_mutations": [mutation] if mutation else [],
    }


def test_digest_declares_derived_note_and_evidence_link():
    spec = _spec(
        {
            "path": "30_Resources/example.md",
            "mode": "create",
            "content": "example",
        }
    )
    digest = INGEST.digest_markdown(
        spec,
        "1234567890abcdef",
        "30_Resources/conversations/originals/gemini/source.md",
        "30_Resources/conversations/digest.md",
        2,
    )
    assert 'derived_notes: \n  - "[[30_Resources/example]]"' in digest
    assert "- [[30_Resources/example]]" in digest
    assert "living Vault note mutations were completed" in digest


def test_create_mutation_renders_reciprocal_placeholders(monkeypatch):
    monkeypatch.setattr(INGEST, "api_read", lambda *_args: None)
    spec = _spec(
        {
            "path": "30_Resources/example.md",
            "mode": "create",
            "content": "{source_link}\n{digest_link}\n{provider_id}",
        }
    )
    plans = INGEST.plan_derived_mutations(
        base_url="http://example",
        headers={},
        spec=spec,
        provider_id="1234567890abcdef",
        source_path="30_Resources/source.md",
        digest_path="30_Resources/digest.md",
    )
    assert plans[0]["before"] is None
    assert "[[30_Resources/source]]" in plans[0]["after"]
    assert "[[30_Resources/digest]]" in plans[0]["after"]
    assert "1234567890abcdef" in plans[0]["after"]


def test_append_mutation_is_idempotent_when_marker_exists(monkeypatch):
    existing = "body\n\n<!-- evidence:1234567890abcdef -->\n"
    monkeypatch.setattr(INGEST, "api_read", lambda *_args: existing)
    spec = _spec(
        {
            "path": "20_Areas/example.md",
            "mode": "append",
            "marker": "evidence:1234567890abcdef",
            "content": "<!-- evidence:1234567890abcdef -->\nnew",
        }
    )
    plans = INGEST.plan_derived_mutations(
        base_url="http://example",
        headers={},
        spec=spec,
        provider_id="1234567890abcdef",
        source_path="30_Resources/source.md",
        digest_path="30_Resources/digest.md",
    )
    assert plans[0]["after"] == existing


def test_create_mutation_allows_only_declared_later_appends(monkeypatch):
    base = "original create body"
    existing = base + "\n\nlater evidence 802e23e77aedd6ad"
    monkeypatch.setattr(INGEST, "api_read", lambda *_args: existing)
    spec = _spec(
        {
            "path": "20_Areas/example.md",
            "mode": "create",
            "allow_appended_markers": ["802e23e77aedd6ad"],
            "content": base,
        }
    )
    plans = INGEST.plan_derived_mutations(
        base_url="http://example",
        headers={},
        spec=spec,
        provider_id="1234567890abcdef",
        source_path="30_Resources/source.md",
        digest_path="30_Resources/digest.md",
    )
    assert plans[0]["after"] == existing


def test_replace_exact_mutation_is_bounded_and_idempotent(monkeypatch):
    existing = "| 2024-03 | wrong |\n"
    monkeypatch.setattr(INGEST, "api_read", lambda *_args: existing)
    spec = _spec(
        {
            "path": "10_Projects/example.md",
            "mode": "replace-exact",
            "marker": "1234567890abcdef",
            "match": "| 2024-03 | wrong |",
            "content": (
                "| 2024-03 | corrected | "
                "<!-- {provider_id} {source_link} {digest_link} -->"
            ),
        }
    )
    plans = INGEST.plan_derived_mutations(
        base_url="http://example",
        headers={},
        spec=spec,
        provider_id="1234567890abcdef",
        source_path="30_Resources/source.md",
        digest_path="30_Resources/digest.md",
    )
    corrected = plans[0]["after"]
    assert "| 2024-03 | corrected |" in corrected
    assert "1234567890abcdef" in corrected
    assert "[[30_Resources/source]]" in corrected
    assert "[[30_Resources/digest]]" in corrected

    monkeypatch.setattr(INGEST, "api_read", lambda *_args: corrected)
    plans = INGEST.plan_derived_mutations(
        base_url="http://example",
        headers={},
        spec=spec,
        provider_id="1234567890abcdef",
        source_path="30_Resources/source.md",
        digest_path="30_Resources/digest.md",
    )
    assert plans[0]["after"] == corrected


def test_replace_exact_rejects_non_unique_match(monkeypatch):
    monkeypatch.setattr(
        INGEST,
        "api_read",
        lambda *_args: "duplicate row\nduplicate row\n",
    )
    spec = _spec(
        {
            "path": "10_Projects/example.md",
            "mode": "replace-exact",
            "match": "duplicate row",
            "content": "corrected row",
        }
    )
    try:
        INGEST.plan_derived_mutations(
            base_url="http://example",
            headers={},
            spec=spec,
            provider_id="1234567890abcdef",
            source_path="30_Resources/source.md",
            digest_path="30_Resources/digest.md",
        )
    except RuntimeError as exc:
        assert "not unique" in str(exc)
    else:
        raise AssertionError("Expected a non-unique replace-exact match to fail")


def test_domain_coverage_can_use_explicit_default():
    spec = _spec()
    spec["domain_coverage"] = {
        "*": {"status": "not-present", "notes": "Scanned; not present."},
        "Knowledge fragments": {
            "status": "delta",
            "notes": "Reusable method captured.",
        },
    }
    table = INGEST.coverage_table(spec)
    assert "| Knowledge fragments | delta | Reusable method captured. |" in table
    assert table.count("| not-present | Scanned; not present. |") == 14


def test_source_body_redacts_credential_shaped_values():
    fake_google_key = "AIza" + "A" * 35
    body = INGEST.source_body(
        {
            "messages": [
                {
                    "role": "user",
                    "text": f"request?key={fake_google_key}",
                }
            ]
        },
        {"title": "Credential incident"},
    )

    assert fake_google_key not in body
    assert "[REDACTED Google API key]" in body
    assert "Security redaction:" in body
