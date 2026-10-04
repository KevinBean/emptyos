"""KB — in-app algorithm-doc → proposed KB notes (engineering pipeline stage 3).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md rule
4). Owns the lightweight in-app path that turns pasted algorithm / standard text
into candidate `formula` / `concept` / `reference` / `case` notes, each surfaced
as a review-gated pending action via ``BaseApp.propose_kb_note`` — never a silent
vault write (.claude/rules/proposed-action.md). The heavyweight multi-PDF path
stays the ``vault-source-digest`` Claude Code skill; this module is the on-page
fallback for a single algorithm excerpt.

Pipeline context: docs/ENGINEERING-APP-WORKFLOW.md stage 3. The proposed
`formula` note bodies carry a TODO block reminding the engineer to promote
``implemented_in:`` + ``verified_against:`` to frontmatter once the engine + the
conformance case exist (stages 4-6), gated by scripts/kb_claim_audit.py.

Reaches into other modules: none. Do not import from ``.app`` (it imports us).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import parse_llm_json, web_route

from .shared import KINDS

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ──────────────────────────────────────────
#   api_digest_doc = _digest.api_digest_doc
#   _digest_extract = _digest._digest_extract
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

# Kinds the in-app digest is allowed to propose. `clause` is excluded — clauses
# need standard/edition/clause frontmatter the citation-ingest path sets, not the
# generic create_note. `doc` uses create_doc. `moc`/`pattern`/`lesson` aren't
# extraction-shaped from a raw algorithm excerpt.
_DIGEST_KINDS = ("formula", "concept", "reference", "case")

_MAX_PROPOSALS = 8

DIGEST_DOC_SYSTEM = """You convert a pasted engineering algorithm / standard excerpt into a small set
of EmptyOS Knowledge Base notes. You extract — you do not invent.

Return ONLY a JSON array (no prose, no code fence). Each element:
{
  "kind": "formula" | "concept" | "reference" | "case",
  "title": "short noun phrase",
  "topic": "2-4 word subject",
  "body": "markdown — equations in $$...$$ (KaTeX), variables defined",
  "references": ["IEC 60287-1-1:2023 §2.1", "CIGRE TB 880 §4.6"]
}

Kind guide:
- formula  → ONE implementable equation/spec. Define every symbol + units.
- concept  → an explanatory idea that isn't a single equation.
- reference → a whole cited source (the standard/paper itself), as a landing note.
- case     → a worked example WITH the published input + output numbers.

Rules:
- references[] are verbatim citation strings copied from the source — never made up.
- For every `formula` note, END the body with this exact HTML-comment block so the
  engineer promotes it to frontmatter after wiring the engine + the reference case:
  <!-- TODO promote to frontmatter once implemented + verified:
       implemented_in: engines/<domain>/...::<fn>
       verified_against: <case-slug> -->
- If the excerpt has no published numbers, do NOT emit a `case` note.
- Prefer 1-4 notes. Quality over coverage. Do not split one equation across notes.
- Output strictly the JSON array and nothing else."""


@web_route("POST", "/api/digest-doc")
async def api_digest_doc(self, request):
    """Propose KB notes from a pasted algorithm/standard excerpt (review-gated).

    Body: ``{text, domain?, source?}``. Returns
    ``{ok, proposed: [...], skipped: [...], count}``. Each proposal is a pending
    action targeting ``kb.create_note`` — reviewed + Applied in the pending
    dashboard.
    """
    body = await self.read_json(request)
    text = (body.get("text") or "").strip()
    domain = (body.get("domain") or "").strip()
    source = (body.get("source") or "").strip()
    room_id = (body.get("room_id") or "").strip()
    if not text:
        return {"error": "text required"}
    if len(text) > 40000:
        text = text[:40000]

    candidates = await self._digest_extract(text)
    if not candidates:
        return {"ok": True, "proposed": [], "skipped": [], "count": 0,
                "note": "no extractable notes found"}

    proposed: list[dict] = []
    skipped: list[dict] = []
    for c in candidates[:_MAX_PROPOSALS]:
        kind = (c.get("kind") or "").strip().lower()
        title = (c.get("title") or "").strip()
        if kind not in _DIGEST_KINDS or not title:
            skipped.append({"title": title or "(untitled)", "reason": f"unsupported kind '{kind}'"})
            continue
        refs = [str(r).strip() for r in (c.get("references") or []) if str(r).strip()]
        try:
            res = await self.propose_kb_note(
                kind=kind,
                title=title,
                body=(c.get("body") or "").strip(),
                domain=domain,
                topic=(c.get("topic") or "").strip(),
                references=refs,
                source=source,
                room_id=room_id,
            )
        except Exception as e:  # noqa: BLE001 — surface, don't crash the digest
            skipped.append({"title": title, "reason": f"propose failed: {e}"})
            continue
        proposed.append({"kind": kind, "title": title, "references": refs, "proposal": res})

    # Provenance of the extraction think-call — the UI renders the FDL §6 chip
    # next to the proposed-notes list so the user sees who drafted them.
    return {"ok": True, "proposed": proposed, "skipped": skipped, "count": len(proposed),
            "provenance": self.last_provenance()}


async def _digest_extract(self, text: str) -> list[dict]:
    """Run the extraction think-call and return a list of candidate note dicts."""
    raw = await self.think(
        text,
        system=DIGEST_DOC_SYSTEM,
        domain="text",
        temperature=0.2,
    )
    data = parse_llm_json(raw)
    if isinstance(data, dict):
        data = data.get("notes") or data.get("items") or [data]
    if not isinstance(data, list):
        return []
    return [c for c in data if isinstance(c, dict)]
