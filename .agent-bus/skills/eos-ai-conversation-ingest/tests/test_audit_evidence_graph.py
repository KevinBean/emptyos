import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1] / "scripts" / "audit_evidence_graph.py"
)
SPEC = importlib.util.spec_from_file_location("audit_evidence_graph", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_optional_read_treats_missing_local_note_like_api_404():
    def missing(_path):
        raise FileNotFoundError("missing")

    assert MODULE.optional_read(missing, "missing.md") is None


def source(provider_id):
    body = "# Example\n\n## Conversation\n\n### 001 · User · now\n\nHello\n"
    import hashlib

    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"""---
record_kind: conversation-source
archive_schema: eos-ai-conversation-v1
author: both
source_conversation_id: "{provider_id}"
source_url: "https://claude.ai/chat/{provider_id}"
capture_fidelity: export-native
raw_status: complete
message_count: 1
content_sha256: {digest}
---
{body}"""


def coverage(**overrides):
    return "\n".join(
        f"| {domain} | {overrides.get(domain, 'not-present')} | |"
        for domain in MODULE.DOMAIN_LABELS
    )


def test_source_checks_verify_contract_hash_and_count():
    provider_id = "11111111-1111-4111-8111-111111111111"
    checks = MODULE.source_checks(source(provider_id), provider_id)
    assert all(checks.values())


def test_derived_links_support_list_and_empty_list():
    digest = """---
derived_notes:
  - "[[60_Worklogs/2024/2024-09-09]]"
  - '[[30_Resources/KB/example|Example]]'
---
"""
    assert MODULE.derived_links(digest) == [
        "60_Worklogs/2024/2024-09-09",
        "30_Resources/KB/example",
    ]
    assert MODULE.derived_links("---\nderived_notes: []\n---\n") == []


def test_derived_links_stop_before_the_next_frontmatter_field():
    digest = """---
source_archive: "[[source]]"
derived_notes:
  - "[[30_Resources/KB/example]]"
related:
  - "[[ai-conversation-ingestion-ledger]]"
---
"""
    assert MODULE.derived_links(digest) == ["30_Resources/KB/example"]


def test_source_related_digest_resolves_safe_basename_to_conversation_root():
    source_text = """---
related:
  - "[[2026-07-24-career-burnout-pr-waiting-and-resignation]]"
---
"""

    assert MODULE.source_related_digest(source_text) == (
        "30_Resources/conversations/"
        "2026-07-24-career-burnout-pr-waiting-and-resignation.md"
    )


def test_digest_checks_and_skip_reason_are_explicit():
    provider_id = "44444444-4444-4444-8444-444444444444"
    source_path = (
        "40_Archive/AI Conversations/originals/claude/"
        "2024-01-01-example--44444444.md"
    )
    digest = f"""---
source_conversation_id: "{provider_id}"
derived_notes: []
---
## Digest
Summary.
## Domain coverage
{coverage()}
## Decisions and durable deltas
No new durable delta.
## Fact-check notes
No material issue.
## Routing
No living note was updated because the verified requirement already exists.
## Evidence chain
No derived notes.
## Source
[[{source_path[:-3]}]]
"""
    checks = MODULE.digest_checks(digest, provider_id, source_path)
    assert all(checks.values())
    reason = MODULE.derived_skip_reason(digest)
    assert "already exists" in reason
    assert "Transient/no durable delta" in reason


def test_markdown_table_cells_preserve_wikilink_alias_pipe():
    assert MODULE.markdown_table_cells(
        "| Knowledge fragments | reused-no-change | [[note|Label]] | Covered |"
    ) == [
        "Knowledge fragments",
        "reused-no-change",
        "[[note|Label]]",
        "Covered",
    ]


def test_no_derived_delta_requires_per_domain_disposition():
    text = f"""## Domain coverage
{coverage(**{"Knowledge fragments": "delta"})}
## Routing
No living note was updated.
"""
    statuses, dispositions, checks, reasons = MODULE.assess_no_mutation_routing(
        text,
        has_derived_notes=False,
    )
    assert statuses["Knowledge fragments"] == "delta"
    assert dispositions == []
    assert checks["delta_dispositions_required"] is True
    assert "delta-disposition-missing:Knowledge fragments" in reasons


def test_digest_contained_delta_requires_routing_search_evidence():
    text = f"""## Domain coverage
{coverage(**{"Knowledge fragments": "delta"})}
## Routing
| Domain | Disposition | Target | Reason |
|---|---|---|---|
| Knowledge fragments | digest-contained | This digest | Small verified distinction. |
"""
    _, dispositions, checks, reasons = MODULE.assess_no_mutation_routing(
        text,
        has_derived_notes=False,
    )
    assert dispositions[0]["disposition"] == "digest-contained"
    assert checks["delta_dispositions_verified"] is False
    assert checks["routing_review_required"] is True
    assert (
        "delta-digest-contained-routing-evidence-missing:Knowledge fragments"
        in reasons
    )


def test_digest_contained_delta_accepts_bilingual_search_receipt():
    text = f"""## Domain coverage
{coverage(**{"Knowledge fragments": "delta"})}
## Routing
| Domain | Disposition | Target | Reason | Routing evidence |
|---|---|---|---|---|
| Knowledge fragments | digest-contained | This digest | Small verified distinction. | queries: risk matrix; 风险矩阵 · checked: [[30_Resources/KB/risk-matrix]] |
"""
    _, dispositions, checks, reasons = MODULE.assess_no_mutation_routing(
        text,
        has_derived_notes=False,
    )
    assert dispositions[0]["routing_evidence"].startswith("queries:")
    assert checks["delta_dispositions_verified"] is True
    assert checks["routing_search_evidence_verified"] is True
    assert checks["routing_review_required"] is False
    assert reasons == []


def test_digest_contained_delta_accepts_explicit_no_candidate_result():
    evidence = (
        "queries: narrow concept; 狭窄概念 · checked: no candidate found"
    )
    assert MODULE.valid_digest_contained_routing_evidence(evidence)


def test_schema_backfill_boilerplate_is_not_a_semantic_disposition():
    text = f"""## Domain coverage
{coverage(**{"Knowledge fragments": "delta"})}
## Routing
| Domain | Disposition | Target | Reason |
|---|---|---|---|
| Knowledge fragments | digest-contained | This digest | The legacy run preserved this delta in the fact-checked digest but left no reciprocal evidence of a living-note mutation; this backfill does not invent one. |
"""
    _, _, checks, reasons = MODULE.assess_no_mutation_routing(
        text,
        has_derived_notes=False,
    )
    assert checks["delta_dispositions_verified"] is False
    assert "delta-disposition-generic:Knowledge fragments" in reasons


def test_reused_and_duplicate_dispositions_require_valid_targets():
    text = f"""## Domain coverage
{coverage(**{
    "Knowledge fragments": "delta",
    "Projects and execution": "delta",
})}
## Routing
| Domain | Disposition | Target | Reason |
|---|---|---|---|
| Knowledge fragments | reused-no-change | - | Existing note covers it. |
| Projects and execution | duplicate-of | [[20_Areas/Project]] | Same content. |
"""
    _, _, _, reasons = MODULE.assess_no_mutation_routing(
        text,
        has_derived_notes=False,
    )
    assert "delta-disposition-target-missing:Knowledge fragments" in reasons
    assert "delta-duplicate-target-invalid:Projects and execution" in reasons


def test_needs_review_and_deferred_unverified_remain_incomplete():
    text = f"""## Domain coverage
{coverage(**{
    "Knowledge fragments": "delta",
    "Open questions and contradictions": "needs-review",
})}
## Routing
| Domain | Disposition | Target | Reason |
|---|---|---|---|
| Knowledge fragments | deferred-unverified | This digest | Primary source missing. |
"""
    _, _, checks, reasons = MODULE.assess_no_mutation_routing(
        text,
        has_derived_notes=False,
    )
    assert checks["needs_review_resolved"] is False
    assert "delta-deferred-unverified:Knowledge fragments" in reasons
    assert (
        "needs-review-unresolved:Open questions and contradictions"
        in reasons
    )


def test_ledger_note_candidates_support_legacy_paths():
    provider_id = "22222222-2222-4222-8222-222222222222"

    class Helper:
        import re

        LEDGER_ROW_RE = re.compile(
            r"(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12})",
            re.IGNORECASE,
        )

    ledger = (
        f"- 2026-07-24 · claude-older-gap · {provider_id} · archived · "
        "[[40_Archive/AI Conversations/originals/claude/"
        "2026-07-24-custom--22222222|source]] · "
        "[[40_Archive/AI Conversations/claude/2026-07-24-custom|digest]] · "
        "[[30_Resources/Technology/Methodology/derived-note]]"
    )
    assert MODULE.ledger_note_candidates(ledger, Helper) == {
        provider_id: {
            "source": [
                "40_Archive/AI Conversations/originals/claude/"
                "2026-07-24-custom--22222222.md"
            ],
            "digest": [
                "40_Archive/AI Conversations/claude/2026-07-24-custom.md"
            ],
            "empty_or_transient": False,
        }
    }


def test_superseded_provider_ids_require_explicit_alias_receipt():
    superseded = "e767f9e8-9cbc-4766-a606-5870efba7f19"
    canonical = "e767f9e8-2555-4302-87da-4f2e52af0445"
    ledger = (
        f"- provider-identity-alias · superseded-provider-id {superseded} · "
        f"canonical-provider-id {canonical} · not a second conversation"
    )
    assert MODULE.superseded_provider_ids(ledger) == {superseded}
    assert MODULE.superseded_provider_ids(
        f"- historical row only · {superseded}"
    ) == set()


def test_audit_item_accepts_verified_empty_export_ledger_receipt():
    provider_id = "44444444-4444-4444-8444-444444444444"

    class Helper:
        @staticmethod
        def slugify(value):
            return "untitled"

    record = MODULE.audit_item(
        {
            "provider_id": provider_id,
            "title": "Untitled",
            "created_at": "2026-06-09T00:00:00Z",
            "message_count": 0,
            "attachment_count": 0,
            "file_count": 0,
        },
        lambda path: (_ for _ in ()).throw(FileNotFoundError(path)),
        Helper,
        {
            "source": [],
            "digest": [],
            "empty_or_transient": True,
        },
    )

    assert record["new_schema_complete"] is True
    assert record["empty_export_verified"] is True
    assert record["source_path"] is None
    assert record["digest_path"] is None
    assert record["reasons"] == []
    assert record["routing_checks"]["needs_review_resolved"] is True
    assert record["ledger_receipt"]["empty_or_transient_verified"] is True


def test_audit_item_prefers_paths_recorded_in_ledger():
    provider_id = "33333333-3333-4333-8333-333333333333"
    source_path = (
        "40_Archive/AI Conversations/originals/claude/"
        "2026-07-24-custom--33333333.md"
    )
    digest_path = "40_Archive/AI Conversations/claude/2026-07-24-custom.md"
    source_text = source(provider_id).replace(
        "---\n# Example",
        f'related:\n  - "[[{digest_path[:-3]}]]"\n---\n# Example',
    )
    digest_text = f"""---
source_conversation_id: "{provider_id}"
derived_notes: []
---
## Digest
## Domain coverage
{coverage()}
## Decisions and durable deltas
No new durable delta.
## Fact-check notes
No material issue.
## Routing
No living note was updated because the scan found no durable delta.
## Evidence chain
[[{source_path[:-3]}]]
## Source
"""
    notes = {source_path: source_text, digest_path: digest_text}

    class Helper:
        @staticmethod
        def slugify(value):
            return "wrong-derived-slug"

    record = MODULE.audit_item(
        {
            "provider_id": provider_id,
            "title": "Wrong Derived Slug",
            "created_at": "2024-01-01T00:00:00Z",
        },
        notes.__getitem__,
        Helper,
        {"source": [source_path], "digest": [digest_path]},
    )
    assert record["new_schema_complete"]
    assert record["source_path"] == source_path
    assert record["digest_path"] == digest_path
    assert record["path_resolution"] == {
        "source": "ledger",
        "digest": "source-link",
    }
    assert all(record["digest_checks"].values())
    assert record["derived_skip_reason"]
    assert record["ledger_receipt"] == {
        "path": "30_Resources/conversations/ai-conversation-ingestion-ledger.md",
        "provider_id_verified": True,
    }


def test_audit_item_prefers_explicit_ledger_digest_over_legacy_source_link():
    provider_id = "33333333-3333-4333-8333-333333333334"
    source_path = (
        "40_Archive/AI Conversations/originals/claude/"
        "2026-07-24-custom--33333333.md"
    )
    dedicated_digest_path = (
        "30_Resources/conversations/2026-07-24-dedicated-receipt.md"
    )
    legacy_digest_path = (
        "30_Resources/conversations/2026-07-24-legacy-shared-digest.md"
    )
    source_text = source(provider_id).replace(
        "---\n# Example",
        f'related:\n  - "[[{legacy_digest_path[:-3]}]]"\n---\n# Example',
    )
    digest_text = f"""---
source_conversation_id: "{provider_id}"
derived_notes: []
---
## Digest
## Domain coverage
{coverage()}
## Decisions and durable deltas
No new durable delta.
## Fact-check notes
No material issue.
## Routing
No living note was updated because the scan found no durable delta.
## Evidence chain
[[{source_path[:-3]}]]
## Source
"""
    notes = {
        source_path: source_text,
        dedicated_digest_path: digest_text,
        legacy_digest_path: digest_text.replace(provider_id, "other-id"),
    }

    class Helper:
        @staticmethod
        def slugify(value):
            return "wrong-derived-slug"

    record = MODULE.audit_item(
        {
            "provider_id": provider_id,
            "title": "Wrong Derived Slug",
            "created_at": "2024-01-01T00:00:00Z",
        },
        notes.__getitem__,
        Helper,
        {
            "source": [source_path],
            "digest": [dedicated_digest_path, legacy_digest_path],
        },
    )

    assert record["new_schema_complete"]
    assert record["digest_path"] == dedicated_digest_path
    assert record["path_resolution"]["digest"] == "ledger"

def test_ledger_note_candidates_prefers_latest_append_only_receipt():
    provider_id = "22222222-2222-4222-8222-222222222222"
    old = "30_Resources/conversations/2026-07-24-example.md"
    new = "30_Resources/conversations/2026-07-24-example--22222222.md"
    legacy = "30_Resources/conversations/legacy/2026-07-24-example.md"
    source_path = (
        "30_Resources/conversations/originals/claude/"
        "2026-07-24-example--22222222.md"
    )
    ledger = (
        f"- 2026-07-24 · claude-native · {provider_id} · old · complete · "
        f"[[{source_path[:-3]}|source]] · [[{old[:-3]}|digest]]\n"
        f"- 2026-08-02 · claude-native · {provider_id} · repaired · complete · "
        f"[[{source_path[:-3]}|source]] · [[{new[:-3]}|digest]] · "
        f"legacy [[{legacy[:-3]}]]\n"
    )

    indexed = MODULE.ledger_note_candidates(ledger, MODULE.load_queue_helper("claude"))

    assert indexed[provider_id]["digest"] == [new, legacy, old]
    assert indexed[provider_id]["source"] == [source_path]


def test_candidates_use_item_provider():
    class Helper:
        @staticmethod
        def slugify(value):
            return "example"

    item = {
        "provider_id": "1234567890abcdef",
        "provider": "gemini",
        "title": "Example",
        "created_at": "2026-07-24",
    }

    assert MODULE.source_candidates(item, Helper)[0] == (
        "30_Resources/conversations/originals/gemini/"
        "2026-07-24-example--12345678.md"
    )
    assert MODULE.digest_candidates(item, Helper)[1] == (
        "40_Archive/AI Conversations/gemini/2026-07-24-example--12345678.md"
    )


def test_ledger_inventory_preserves_browser_provider_title_and_date():
    provider_id = "1234567890abcdef"
    ledger = (
        f"- 2026-07-24 · gemini-older · {provider_id} · Example title · "
        "archived source + digest"
    )

    inventory = MODULE.ledger_inventory(ledger, "gemini", {provider_id})

    assert inventory[provider_id]["provider"] == "gemini"
    assert inventory[provider_id]["title"] == "Example title"
    assert inventory[provider_id]["created_at"] == "2026-07-24"


def test_source_paths_from_search_requires_exact_provider_and_short_id():
    files = [
        {
            "path": (
                "40_Archive/AI Conversations/originals/gemini/"
                "2026-07-24-custom--12345678.md"
            )
        },
        {
            "path": (
                "40_Archive/AI Conversations/originals/claude/"
                "2026-07-24-custom--12345678.md"
            )
        },
        {
            "path": (
                "40_Archive/AI Conversations/originals/gemini/"
                "2026-07-24-custom--12345679.md"
            )
        },
    ]

    assert MODULE.source_paths_from_search(
        files, "gemini", "1234567890abcdef"
    ) == [
        (
            "40_Archive/AI Conversations/originals/gemini/"
            "2026-07-24-custom--12345678.md"
        )
    ]


def test_frontmatter_is_line_anchored_not_a_bare_split():
    """A `---` inside a YAML VALUE must not terminate the block.

    `text.split("---", 2)` did, so a note whose asset name sanitized to
    `260610---P530678` had its body sliced mid-frontmatter and the wrong bytes
    hashed. That was patched once by sanitizing the input; the parser stayed
    wrong for every other input carrying a run of dashes, and five sibling call
    sites shared it.
    """
    note = "---\nasset: 260610---P530678\ntitle: x\n---\nREAL BODY\n"

    assert MODULE.body(note) == "REAL BODY\n"
    assert "260610---P530678" in MODULE.frontmatter(note)
    assert "REAL BODY" not in MODULE.frontmatter(note)

    # The old implementation, kept here so the regression is legible: it slices
    # at the first `---` run wherever it appears.
    naive = note.split("---", 2)
    assert naive[2].lstrip("\n") != MODULE.body(note)


def test_a_dash_run_inside_prose_does_not_end_the_block():
    note = "---\nnote: a --- b\n---\nbody text\n"
    assert MODULE.body(note) == "body text\n"
    assert MODULE.frontmatter(note) == "note: a --- b"


def test_a_note_without_frontmatter_round_trips():
    assert MODULE.body("plain note\n") == "plain note\n"
    assert MODULE.frontmatter("plain note\n") == ""


def test_crlf_is_normalised():
    assert MODULE.body("---\r\na: 1\r\n---\r\nbody\r\n") == "body\n"


def test_a_horizontal_rule_is_not_an_unclosed_fence():
    """A note opening with `----` has NO frontmatter — body is the whole note.

    Caught by differentially executing the old and new parsers rather than
    reasoning about equivalence. Two ways to get this wrong, and the first
    version of the rewrite had one of them:

      old  `text.startswith("---")` is true of `----`, so it split on the dash
           run and silently ate the first lines — or returned "" when there was
           no third part.
      new  a first draft asked the same `startswith("---")` when deciding
           whether an unclosed fence meant "no trustworthy body", so `----`
           returned "" and lost the document.

    Both feed the content hash, so either one hashes the wrong bytes.
    """
    doc = "----\na: 1\n---\nbody\n"
    assert MODULE.body(doc) == doc          # no frontmatter: body is everything
    assert MODULE.frontmatter(doc) == ""

    rule_only = "----\n\nreal content\n"
    assert MODULE.body(rule_only) == rule_only
    assert MODULE.frontmatter(rule_only) == ""


def test_an_unclosed_fence_still_yields_no_body():
    """The case the `----` fix must not break: a real opening fence, never closed."""
    assert MODULE.body("---\na: 1\nbody\n") == ""
    assert MODULE.body("---\n") == ""
    assert MODULE.frontmatter("---\na: 1\nbody\n") == ""


def test_a_fence_line_with_trailing_space_still_closes():
    doc = "---  \na: 1\n---  \nbody\n"
    assert MODULE.body(doc) == "body\n"
    assert MODULE.frontmatter(doc) == "a: 1"


def test_a_horizontal_rule_in_the_body_is_left_alone():
    """Only the FIRST closing fence ends the block; later `---` is content."""
    doc = "---\na: 1\n---\nbody\n\n---\n\nmore\n"
    assert MODULE.body(doc) == "body\n\n---\n\nmore\n"
