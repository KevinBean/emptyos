"""kb — KB note CRUD + path helpers + reference endpoints.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Note path helpers (_doc_path, _note_path, _notes_dir, _docs_dir), the public note APIs (list_domains, list_notes, get_note, api_note_*, api_references, api_resolve_reference, api_health), and `create_note`.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._resolve_references / self._lookup_ref (indexes) for citation parsing.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from emptyos.sdk import extract_wikilinks, web_route
from emptyos.sdk.utils import path_segment_error, require_path_segment
from typing import TYPE_CHECKING

from .shared import (
    DEFAULT_DOCS_DIR,
    DEFAULT_NOTES_DIR,
    DEFAULT_STALE_DAYS,
    KINDS,
    SOURCE_TAG,
    _note_has_visual,
    _related_targets,
    _slug_of,
    _slugify,
    VERIFIED_KINDS,
    note_staleness,
    note_verification,
    reference_freshness,
)
from .reference_coverage import reference_clause_match_score

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ────────────────────────────────
#   _docs_dir              = _notes._docs_dir
#   _doc_path              = _notes._doc_path
#   _notes_dir             = _notes._notes_dir
#   _note_path             = _notes._note_path
#   _all_notes             = _notes._all_notes
#   _summarize             = _notes._summarize
#   _title_for             = _notes._title_for
#   list_domains           = _notes.list_domains
#   list_notes             = _notes.list_notes
#   get_note               = _notes.get_note
#   note_path              = _notes.note_path
#   voice_kb_search        = _notes.voice_kb_search
#   search_bodies          = _notes.search_bodies
#   api_search_body        = _notes.api_search_body
#   health                 = _notes.health
#   _note_health_flags     = _notes._note_health_flags
#   api_domains            = _notes.api_domains
#   api_notes              = _notes.api_notes
#   api_note_detail        = _notes.api_note_detail
#   api_note_section       = _notes.api_note_section
#   api_health             = _notes.api_health
#   api_references         = _notes.api_references
#   api_resolve_reference  = _notes.api_resolve_reference
#   resolve_reference      = _notes.resolve_reference
#   create_note            = _notes.create_note
#   deliver_work_notes     = _notes.deliver_work_notes
#   upsert_note            = _notes.upsert_note
#   update_implemented_in  = _notes.update_implemented_in
#   api_note_create        = _notes.api_note_create
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _docs_dir(self) -> str:
    return self.app_config("docs_dir", DEFAULT_DOCS_DIR)


def _safe_slug(slug: str) -> str:
    """A KB slug that is safe to interpolate into a vault path.

    Slugs arrive from a DELETE path param and from the create/upsert body,
    and the result is both written and unlink()ed. A raw `..` walked out of
    the notes dir; deep enough, it left the vault entirely. The platform
    containment in VaultIndex now stops the ESCAPE, but only the app can
    stop a write landing somewhere unintended INSIDE the vault -- so this
    guard is not redundant with it.
    """
    return require_path_segment(slug, "slug")


def _doc_path(self, slug: str) -> str:
    return f"{self._docs_dir()}/{_safe_slug(slug)}.md"


def _notes_dir(self) -> str:
    return self.app_config("notes_dir", DEFAULT_NOTES_DIR)


def _note_path(self, slug: str) -> str:
    return f"{self._notes_dir()}/{_safe_slug(slug)}.md"


def _all_notes(self, include_project: bool = False) -> list[dict]:
    """The KB corpus: `kb`-tagged notes, i.e. global reusable knowledge.

    ``include_project=True`` adds the project-scoped source documents (a
    client's spec, tagged ``kb-source`` — see ``shared.SOURCE_TAG``). Only
    slug-addressed lookups (open a note, a section, a timeline) and the
    citation index ask for them; every listing, health, board, graph and
    domain surface stays on the default and never shows a client's clauses.
    """
    # Exclude backup copies under 99_Attachments/temp-backup — they retain the
    # original `tags: [kb]` and would otherwise pollute every KB surface
    # (browser, graph, domain counts, health). Mirrors the exclusion in
    # scripts/audit_kb_app_alignment.py so the live health check and the
    # release gate agree on what counts as a real KB note.
    notes = list(self.vault_query(tags=["kb"]) or [])
    if include_project:
        notes += list(self.vault_query(tags=[SOURCE_TAG]) or [])
    return [
        n for n in notes
        if "99_Attachments/temp-backup/" not in (n.get("path") or "").replace("\\", "/")
    ]


def _title_for(self, slug: str, all_notes: list[dict]) -> str:
    """Human title for a slug, scanning the already-loaded note set (no new read)."""
    for n in all_notes:
        if _slug_of(n.get("path", "")) == slug:
            p = n.get("properties", {}) or {}
            return p.get("title") or n.get("name") or slug.replace("-", " ")
    return slug.replace("-", " ")


def _summarize(self, n: dict) -> dict:
    props = n.get("properties", {}) or {}
    out = {
        "slug": _slug_of(n.get("path", "")),
        "path": n.get("path", ""),
        "name": n.get("name", ""),
        "title": props.get("title", ""),
        "kind": props.get("kind", ""),
        "domain": props.get("domain", ""),
        "topic": props.get("topic", ""),
        # '' for global knowledge; a project id for a client source document.
        "project": str(props.get("project") or "").strip(),
        "tags": n.get("tags", []) or [],
        "has_visual": _note_has_visual(props),
        # Derived transcription-verification state (clause/case only; the
        # tier is "" for other kinds). Read-time, from dated keys — never a
        # stored field.
        "verification": note_verification(props, n.get("sections") or []),
    }
    # Flag-gated: the key is ABSENT when off, so list JSON is byte-identical
    # to pre-feature. When on, carries the immediate-successor slug or None
    # (explicit per-note edge, else derived document-level cascade).
    if self._supersession_enabled():
        out["superseded_by"] = (
            self._supersession_forward.get(out["slug"])
            or self._supersession_doc_cascade.get(out["slug"])
        )
    return out


def list_domains(self) -> dict:
    notes = self._all_notes()
    domains: dict[str, dict[str, int]] = {}
    for n in notes:
        d = (n.get("properties", {}) or {}).get("domain") or "unknown"
        k = (n.get("properties", {}) or {}).get("kind") or "unknown"
        domains.setdefault(d, {"_total": 0})
        domains[d]["_total"] += 1
        domains[d][k] = domains[d].get(k, 0) + 1
    out = []
    for d, counts in sorted(domains.items()):
        out.append({"domain": d, "total": counts.pop("_total"), "by_kind": counts})
    return {"domains": out, "total_notes": len(notes)}


def list_notes(
    self, domain: str = "", kind: str = "", topic: str = "", enrich: bool = False,
    project: str = "",
) -> dict:
    """List KB note summaries.

    Project-scoped source notes (a client document ingested for one project,
    ``project:`` set) are NOT global knowledge: the default listing leaves them
    out, and ``project=<id>`` lists only that project's. ``project="*"`` lists
    everything.

    When ``enrich`` is True, each row also carries ``modified`` (file mtime
    float, always present — the reliable sort key for "recently updated"),
    ``updated`` (the frontmatter ``updated:`` date string, "" if absent),
    ``backlink_count`` (int), and ``health_flag`` ("error" | "warn" | "").
    These drive the search-first list's Recent / Most-linked / Needs-attention
    buckets. Enrichment builds the whole-corpus backlink map (the same ~16s
    body-read pass ``health()`` pays), so it is OFF by default — only the kb
    page requests ``?enrich=1``; other callers (vault-graph, agents) stay cheap.
    """
    project = (project or "").strip()
    all_notes = self._all_notes(include_project=bool(project))
    bl_map: dict = {}
    flags: dict = {}
    if enrich:
        bl_map = self._build_backlink_map(all_notes)
        flags = self._note_health_flags(all_notes, bl_map)
    out = []
    for n in all_notes:
        s = self._summarize(n)
        if project != "*":
            if project and s["project"] != project:
                continue
            if not project and s["project"]:
                continue
        if domain and s["domain"] != domain:
            continue
        if kind and s["kind"] != kind:
            continue
        if topic and s["topic"] != topic:
            continue
        if enrich:
            props = n.get("properties", {}) or {}
            s["modified"] = n.get("modified") or 0
            s["updated"] = props.get("updated", "") or ""
            s["backlink_count"] = len(bl_map.get(s["slug"], []))
            s["health_flag"] = flags.get(s["slug"], "")
        out.append(s)
    out.sort(key=lambda x: (x["domain"], x["kind"], x["slug"]))
    return {"notes": out, "count": len(out)}


# Canonical KB per-note health rules — ONE source of truth shared by both the
# Health modal (health(), grouped by category) and the list "Needs attention"
# severity flags (_note_health_flags). The map's keys are exactly health()'s
# output list keys; the value is the severity the flag reducer assigns.
_HEALTH_SEVERITY = {
    "duplicate_slugs": "error",
    "invalid_kind": "error",
    "broken_implemented_in": "error",
    "unresolved_verified_against": "error",
    "verification_target_not_case": "warn",
    "formulas_missing_verification": "warn",
    "uncited_references": "warn",
    "orphans": "warn",
    "stale_reference": "warn",
    "stale_note": "warn",
    # Transcription verification (kb-fact-integrity T4). Only the engine-backed
    # subset of unverified notes is a finding; the rest is a coverage COUNT on
    # health() (`unverified_clause`), never a listing — flagging 1200 notes
    # trains the reader to ignore the bucket (audits.md failure mode 1).
    "engine_backed_transcribed": "warn",
    "verification_stale": "warn",
    "verification_without_receipt": "warn",
}

# Kinds `note_staleness` skips: `reference` already has its own review_due-
# based freshness check (`stale_reference`), and `clause` notes are verbatim
# excerpts from a standard — static by nature, never expected to be "kept
# fresh" the way a concept/lesson/case note is.
_STALE_NOTE_SKIP_KINDS = frozenset({"reference", "clause"})


def _iter_health_findings(self, all_notes: list[dict], cited_by: dict, today: date | None = None):
    """Yield ``(category, slug, record)`` for every KB health issue, where
    ``category`` is a key of ``_HEALTH_SEVERITY`` and ``record`` is the exact
    dict the Health modal groups under that category. Single source of truth so
    the modal and the per-note severity flags can never drift apart (#9).

    ``today`` is injectable so the date-dependent ``stale_reference`` rule is
    testable without freezing the clock."""
    repo_root = Path(self.kernel.config.path).parent
    today = today or date.today()
    stale_days = int(self.setting_or_config("kb.stale_days", DEFAULT_STALE_DAYS) or DEFAULT_STALE_DAYS)
    by_slug: dict[str, dict] = {}
    slug_paths: dict[str, list[str]] = {}
    references = [
        n for n in all_notes
        if ((n.get("properties", {}) or {}).get("kind")) == "reference"
    ]
    for n in all_notes:
        slug = _slug_of(n.get("path", ""))
        slug_paths.setdefault(slug, []).append(n.get("path", ""))
        by_slug.setdefault(slug, n)
    for slug, paths in slug_paths.items():
        if len(paths) > 1:
            yield ("duplicate_slugs", slug, {"slug": slug, "paths": paths})
    for n in all_notes:
        s = self._summarize(n)
        slug = s["slug"]
        props = n.get("properties", {}) or {}
        if s["kind"] not in KINDS:
            yield ("invalid_kind", slug,
                   {"slug": slug, "kind": s["kind"], "path": s["path"]})
        impl = props.get("implemented_in") or []
        if isinstance(impl, str):
            impl = [impl]  # scalar string — never iterate it char-by-char
        for ref in impl:
            rkind, code_path, _ = self._parse_impl_ref(ref)
            if rkind == "method":
                continue
            if code_path and not (repo_root / code_path).exists():
                yield ("broken_implemented_in", slug, {"slug": slug, "ref": ref})
        vrefs = props.get("verified_against")
        if isinstance(vrefs, str):
            vrefs = [vrefs]
        vrefs = vrefs or []
        for target in vrefs:
            tslug = str(target).strip().strip("[]")
            if not tslug:
                continue
            tnote = by_slug.get(tslug)
            if tnote is None:
                yield ("unresolved_verified_against", slug,
                       {"slug": slug, "target": tslug, "path": s["path"]})
            elif ((tnote.get("properties", {}) or {}).get("kind")) != "case":
                yield ("verification_target_not_case", slug, {
                    "slug": slug, "target": tslug,
                    "target_kind": (tnote.get("properties", {}) or {}).get("kind"),
                    "path": s["path"],
                })
        if s["kind"] == "formula" and not vrefs:
            yield ("formulas_missing_verification", slug, s)
        if s["kind"] in VERIFIED_KINDS:
            ver = note_verification(props, n.get("sections") or [])
            if ver["tier"] == "transcribed" and impl:
                # An engine reads this note's numbers and nobody has compared
                # them to the print — the exact shape of the IEC 60949 incident.
                yield ("engine_backed_transcribed", slug, {**s, "implemented_in": list(impl)})
            if ver["stale"]:
                yield ("verification_stale", slug, {**s, **ver, "updated": str(props.get("updated") or "")})
            if ver["tier"] != "transcribed" and not ver["receipt"]:
                yield ("verification_without_receipt", slug, {**s, **ver})
        if s["kind"] not in _STALE_NOTE_SKIP_KINDS:
            stale = note_staleness(props, today, stale_days)
            if stale["state"] == "stale":
                yield ("stale_note", slug, {**s, **stale})
        if s["kind"] == "reference":
            fresh = reference_freshness(props, today)
            # Only a *missed commitment* is a finding. A reference with no
            # `review_due` is unscheduled, not stale — see shared.py.
            if fresh["state"] in ("overdue", "malformed"):
                yield ("stale_reference", slug, {**s, **fresh})
        if s["kind"] == "clause":
            # A clause owned by a reference is already used by the composed
            # document, even when no authored note cites it directly. The
            # reference-coverage matcher is canonical for this ownership edge;
            # unmatched standalone clauses remain actionable here.
            if not cited_by.get(slug):
                has_reference_owner = any(
                    reference_clause_match_score(reference, n) is not None
                    for reference in references
                )
                if not has_reference_owner:
                    yield ("uncited_references", slug, s)
            continue
        if not cited_by.get(slug) and not (props.get("related") or []):
            yield ("orphans", slug, s)


def _note_health_flags(self, all_notes: list[dict], cited_by: dict | None = None) -> dict:
    """Per-note health severity ``{slug: "error" | "warn" | ""}``, derived from
    the same ``_iter_health_findings`` the Health modal uses (so they agree by
    construction). ``error`` is sticky — never downgraded to ``warn``."""
    if cited_by is None:
        cited_by = self._build_backlink_map(all_notes)
    flags: dict[str, str] = {}
    for category, slug, _record in _iter_health_findings(self, all_notes, cited_by):
        sev = _HEALTH_SEVERITY[category]
        cur = flags.get(slug, "")
        if cur == "error":
            continue  # error is sticky
        if sev == "error" or cur == "":
            flags[slug] = sev
    return flags


async def get_note(self, slug: str) -> dict:
    all_notes = self._all_notes(include_project=True)  # slug-addressed: a client clause opens too
    match = next(
        (n for n in all_notes if _slug_of(n.get("path", "")) == slug),
        None,
    )
    if not match:
        return {"error": "not found", "slug": slug}
    path = match.get("path", "")
    props = self.vault_get_properties(path) or (match.get("properties") or {})
    # Resolve free-text references into structured {text, target_slug, in_kb} so the
    # UI can render them as clickable links to reference notes. In-place mutation
    # preserves the existing `data.properties.references` consumer contract.
    if isinstance(props.get("references"), list):
        props["references"] = self._resolve_references(
            props.get("references") or [],
            project=str(props.get("project") or "").strip() or None,
        )
    body = self.vault_read_body(path)
    idx = self._kind_index(all_notes)

    # Outgoing edges: frontmatter related/verified_against + body [[wikilinks]] that resolve to KB notes
    out_slugs = _related_targets(props)
    for v in props.get("verified_against") or []:
        s = str(v).strip().strip("[]").strip()
        if s:
            out_slugs.add(s)
    body_slugs = extract_wikilinks(body) & set(idx.keys())
    out_slugs |= body_slugs

    def _enrich(s: str) -> dict:
        meta = idx.get(s, {})
        return {"slug": s, "kind": meta.get("kind", ""), "domain": meta.get("domain", ""), "in_kb": s in idx}

    outgoing = sorted([_enrich(s) for s in out_slugs if s != slug], key=lambda r: (not r["in_kb"], r["kind"], r["slug"]))

    # Reference-landing aggregation: when this note is `kind: reference` with a
    # declared `standard_id:`, surface every clause note that belongs to that
    # source for the UI's "Clauses" sidebar.
    clauses: list[dict] = []
    chapters: list[dict] = []
    if props.get("kind") == "reference" and props.get("standard_id"):
        clauses = self._clauses_for_standard(
            str(props["standard_id"]),
            str(props.get("standard") or ""),
            str(props.get("edition") or ""),
        )
        # Real chapter titles from the archive's own TOC, for the reference-view
        # TOC tree. {} when no archive → UI falls back to bare "Chapter N".
        if clauses:
            titles = self._chapter_titles(props)
            chs = sorted(
                {str(c.get("clause") or "").split(".")[0] for c in clauses if c.get("clause")},
                key=lambda x: int(x) if x.isdigit() else 999,
            )
            chapters = [{"no": ch, "title": titles.get(ch, "")} for ch in chs]

    # Temporal supersession (flag-gated). When off, the spread below adds ZERO
    # keys, so detail JSON is byte-identical to pre-feature. When on:
    #   superseded_by: {slug,title,kind,via} | None  — successor (explicit note
    #                  edge `via:"note"`, else derived document cascade `via:"document"`)
    #   supersedes:    [{slug,title,kind}, ...]   — notes this one replaced (derived)
    #   current_slug:  the live note to jump to (chain endpoint / successor doc)
    supersession: dict = {}
    if self._supersession_enabled():
        explicit = self._supersession_forward.get(slug)
        derived = self._supersession_doc_cascade.get(slug)
        succ = explicit or derived
        via = "note" if explicit else ("document" if derived else None)
        superseded_by = None
        current_slug = None
        if succ:
            m = idx.get(succ, {})
            superseded_by = {"slug": succ, "kind": m.get("kind", ""),
                             "title": self._title_for(succ, all_notes), "via": via}
            # jump-to-current: explicit chains follow the terminal endpoint; a
            # document-cascade successor is already the current reference.
            current_slug = self._supersession_terminal.get(slug, succ) if explicit else succ
        supersedes = []
        for pred in self._supersession_reverse.get(slug, []):
            m = idx.get(pred, {})
            supersedes.append({"slug": pred, "kind": m.get("kind", ""),
                               "title": self._title_for(pred, all_notes)})
        supersession = {"superseded_by": superseded_by, "supersedes": supersedes,
                        "current_slug": current_slug}

    return {
        "slug": slug,
        "path": path,
        "name": match.get("name", ""),
        "properties": props,
        "body": body,
        "backlinks": self._kb_backlinks(slug, all_notes),
        "outgoing": outgoing,
        "clauses": clauses,
        "chapters": chapters,
        "implemented_in_status": self._check_implemented_in(props.get("implemented_in") or []),
        "has_visual": _note_has_visual(props),
        "verification": note_verification(props, match.get("sections") or []),
        **supersession,
    }


async def note_path(self, id: str = "") -> str:
    """Vault-relative path for a KB note slug, or "" if not found.

    ``[provides.timeline]`` entity_source — the platform 4D-timeline aggregator
    calls this to resolve a detail-view entity id into its vault note
    (mirrors ``projects.project_path``).
    """
    slug = (id or "").strip()
    if not slug:
        return ""
    match = next(
        (n for n in self._all_notes(include_project=True) if _slug_of(n.get("path", "")) == slug),
        None,
    )
    return str((match or {}).get("path", "") or "").replace("\\", "/")


async def health(self) -> dict:
    """KB integrity report, grouped by category. Per-note rules come from the
    shared ``_iter_health_findings`` generator (also backing the list's
    severity flags) so the modal and the list cannot drift (#9). Backlink
    adjacency is built once (O(1) orphan/uncited lookups; was ~16s on ~387
    notes when each rescanned with body reads)."""
    all_notes = self._all_notes()
    cited_by = self._build_backlink_map(all_notes)
    buckets: dict[str, list[dict]] = {k: [] for k in _HEALTH_SEVERITY}
    for category, _slug, record in _iter_health_findings(self, all_notes, cited_by):
        buckets[category].append(record)
    return {
        "broken_implemented_in": buckets["broken_implemented_in"],
        "formulas_missing_verification": buckets["formulas_missing_verification"],
        "orphans": buckets["orphans"],
        "uncited_references": buckets["uncited_references"],
        "invalid_kind": buckets["invalid_kind"],
        "duplicate_slugs": buckets["duplicate_slugs"],
        "unresolved_verified_against": buckets["unresolved_verified_against"],
        "verification_target_not_case": buckets["verification_target_not_case"],
        "stale_reference": buckets["stale_reference"],
        "stale_note": buckets["stale_note"],
        "engine_backed_transcribed": buckets["engine_backed_transcribed"],
        "verification_stale": buckets["verification_stale"],
        "verification_without_receipt": buckets["verification_without_receipt"],
        # Coverage, not a finding: how many clause/case notes nobody has
        # compared to the source yet (see _HEALTH_SEVERITY).
        "unverified_clause": sum(
            1 for n in all_notes
            if note_verification(n.get("properties", {}) or {}, n.get("sections") or [])["tier"] == "transcribed"
        ),
    }


# ── Aura voice intent (registry: [[provides.verbs]] in manifest) ──

def _first_prose_line(body: str, limit: int = 220) -> str:
    """First non-heading, non-frontmatter prose line of a note body, lightly
    de-markdowned for TTS. Empty string when the body has no prose."""
    for line in (body or "").splitlines():
        s = re.sub(r"<!--.*?(-->|$)", "", line).strip()
        if not s or s.startswith(("#", ">", "|", "```", "---", "![", "- [")):
            continue
        s = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), s)
        s = re.sub(r"[*_`]", "", s)
        return s[:limit].rstrip() + ("…" if len(s) > limit else "")
    return ""


async def voice_kb_search(self, query: str = "") -> dict:
    """Voice: 'what do I know about X' — match KB notes by slug/title/topic/tags,
    speak the closest note's opening line, card the matches."""
    q = (query or "").strip()
    if not q:
        return {"say": "What topic should I look up?"}
    terms = [t for t in q.lower().split() if len(t) > 2] or [q.lower()]
    scored = []
    for s in self.list_notes()["notes"]:
        hay = " ".join(
            [s.get("slug", ""), s.get("title", ""), s.get("topic", ""),
             s.get("domain", ""), " ".join(s.get("tags") or [])]
        ).lower()
        score = sum(1 for t in terms if t in hay)
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    top = [s for _, s in scored[:6]]
    if not top:
        return {
            "say": f"Nothing in the knowledge base matches {q} yet.",
            "link": {"text": "Open KB", "href": "/kb/"},
        }
    best = top[0]
    best_title = best.get("title") or best.get("slug")
    say = f"I have {len(top)} KB note{'s' if len(top) != 1 else ''} on {q}. Closest: {best_title}."
    try:
        note = await self.get_note(best["slug"])
        gist = _first_prose_line(note.get("body", ""))
        if gist:
            say += f" {gist}"
    except Exception:
        pass
    items = [
        {"text": s.get("title") or s.get("slug"), "tag": s.get("kind", "")}
        for s in top
    ]
    return {
        "say": say,
        "card": {"renderer": "task-list", "data": items, "title": f"KB — {q}"},
        "link": {"text": "Open note", "href": f"/kb/#{best['slug']}"},
    }


@web_route("GET", "/api/domains")
async def api_domains(self, request):
    return self.list_domains()


_BODY_SEARCH_MIN = 3        # a 1–2 char term matches half the corpus (kb.js repeats it)
_BODY_SEARCH_FILES = 5000   # ripgrep's file cap for the lead term, whole vault
_BODY_SEARCH_CANDIDATES = 300
_BODY_SEARCH_LIMIT = 50


def _snippet(body: str, term: str, width: int = 140) -> str:
    """The line holding `term`, trimmed around it — what the list row shows."""
    for line in body.splitlines():
        i = line.lower().find(term)
        if i == -1:
            continue
        line = line.strip().lstrip("#>-* ").strip()
        i = line.lower().find(term)
        start = max(0, i - width // 3)
        text = line[start:start + width]
        return ("…" if start else "") + text + ("…" if start + width < len(line) else "")
    return ""


async def search_bodies(self, q: str = "") -> dict:
    """KB notes whose BODY contains every term of `q` (case-insensitive).

    The page already matches slug/title/topic client-side; this adds what it
    cannot see. ripgrep narrows the vault by the longest term, the KB set
    intersects it, and each candidate's body is checked for all the terms.
    """
    import asyncio

    terms = [t for t in (q or "").lower().split() if t]
    if not terms or max(len(t) for t in terms) < _BODY_SEARCH_MIN:
        return {"q": q, "hits": []}
    vault = self.kernel.config.notes_path
    if not vault:
        return {"q": q, "hits": []}
    by_path = {(n.get("path") or "").replace("\\", "/"): n for n in self._all_notes()}
    lead = max(terms, key=len)
    # Escape only regex metacharacters: ripgrep's engine rejects some of the
    # extra escapes re.escape emits. Pin grep so an empty chain can never fall
    # through to the human provider and wait on a person.
    pattern = re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", lead)
    try:
        found = await self.search(pattern, path=str(vault), glob="*.md", only_provider="grep",
                                  limit=_BODY_SEARCH_FILES, case_insensitive=True)
    except Exception:  # noqa: BLE001 — no search provider: no body hits, title search still works
        return {"q": q, "hits": []}
    root = str(vault).replace("\\", "/").rstrip("/") + "/"
    candidates = []
    for f in found or []:
        p = str(f.get("path") or "").replace("\\", "/")
        rel = p[len(root):] if p.lower().startswith(root.lower()) else p
        if rel in by_path:
            candidates.append(rel)
        if len(candidates) >= _BODY_SEARCH_CANDIDATES:
            break
    hits = []
    for rel in candidates:
        body = await asyncio.to_thread(self.vault_read_body, rel)
        low = body.lower()
        if all(t in low for t in terms):
            hits.append({"slug": _slug_of(rel), "snippet": _snippet(body, lead)})
            if len(hits) >= _BODY_SEARCH_LIMIT:
                break
    # Any of the three caps can leave matches unread; the page says so rather
    # than presenting an arbitrary subset as the whole answer.
    truncated = (len(hits) >= _BODY_SEARCH_LIMIT or len(candidates) >= _BODY_SEARCH_CANDIDATES
                 or len(found or []) >= _BODY_SEARCH_FILES)
    return {"q": q, "hits": hits, "truncated": truncated}


@web_route("GET", "/api/search/body")
async def api_search_body(self, request):
    return await self.search_bodies(request.query_params.get("q", ""))


@web_route("GET", "/api/notes")
async def api_notes(self, request):
    return self.list_notes(
        domain=request.query_params.get("domain", ""),
        kind=request.query_params.get("kind", ""),
        topic=request.query_params.get("topic", ""),
        enrich=(request.query_params.get("enrich", "") == "1"),
        project=request.query_params.get("project", ""),
    )


@web_route("GET", "/api/notes/{slug}")
async def api_note_detail(self, request):
    slug = request.path_params.get("slug", "")
    result = await self.get_note(slug)
    if isinstance(result, dict) and "error" not in result:
        await self.emit("kb:viewed", {"slug": slug})
    return result


@web_route("GET", "/api/notes/{slug}/section/{section:path}")
async def api_note_section(self, request):
    """Return one section of a KB note's body.

    `section` is the heading text (`## Section Name`) — case-sensitive match
    on the visible heading text after the `#`s. Used by EOS_UI.transclude when
    a noteRef carries a `#section` anchor.
    """
    slug = request.path_params.get("slug", "")
    section = request.path_params.get("section", "")
    all_notes = self._all_notes()
    match = next((n for n in all_notes if _slug_of(n.get("path", "")) == slug), None)
    if not match:
        return {"error": "not found", "slug": slug}
    path = match.get("path", "")
    body = self.vault_read_section(path, section) or ""
    props = self.vault_get_properties(path) or {}
    return {
        "slug": slug,
        "section": section,
        "title": props.get("title") or slug.replace("-", " "),
        "kind": props.get("kind", ""),
        "body": body,
        "path": path,
    }


@web_route("GET", "/api/health")
async def api_health(self, request):
    return await self.health()


@web_route("GET", "/api/references")
async def api_references(self, request):
    """Return the (standard, edition, clause) → slug index for chatbot / agents."""
    return {
        "references": [
            {
                "standard": std,
                "edition": ed,
                "clause": cl,
                "slug": slug,
            }
            for (std, ed, cl), slug in sorted(self._ref_index.items())
        ],
        "count": len(self._ref_index),
    }


@web_route("POST", "/api/resolve-reference")
async def api_resolve_reference(self, request):
    """Resolve a single citation string to a KB clause slug.

    Used cross-app via ``call_app("kb", "resolve_reference",
    reference=...)`` so consumers (vault-graph for KB clause edges,
    future linkers) can map free-text refs without duplicating
    ``_parse_citation`` + ``_lookup_ref``.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    ref_str = (body.get("reference") if isinstance(body, dict) else None) or ""
    project = (body.get("project") if isinstance(body, dict) else None) or ""
    return await self.resolve_reference(reference=str(ref_str), project=str(project))


async def resolve_reference(self, reference: str = "", project: str = "") -> dict:
    """Programmatic version of api_resolve_reference for call_app.

    ``project`` widens the grammar to that project's registered source
    documents (a client specification ingested via ``/api/sources/ingest``) and
    searches its clauses before the global KB. ``scope`` in the reply says which
    half answered.
    """
    ref_str = (reference or "").strip()
    project = (project or "").strip()
    if not ref_str:
        return {"slug": None, "matched": False, "reason": "empty"}
    parsed = self._parse_any_citation(ref_str, project=project or None)
    if not parsed:
        return {"slug": None, "matched": False, "reason": "unparseable"}
    standard, edition, clause = parsed
    slug = self._lookup_ref(standard, edition, clause, project=project or None)
    scope = ""
    if slug is not None:
        in_project = slug in set(
            (getattr(self, "_project_ref_index", {}) or {}).get(project, {}).values()
        ) if project else False
        scope = "project" if in_project else "global"
    return {
        "slug": slug,
        "matched": slug is not None,
        "standard": standard,
        "edition": edition,
        "clause": clause,
        "scope": scope,
    }


async def create_note(
    self,
    *,
    kind: str,
    title: str,
    body: str = "",
    domain: str = "",
    topic: str = "",
    references: list[str] | None = None,
    related: list[str] | None = None,
    source: str = "",
    slug: str = "",
    author: str = "",
) -> dict:
    """Create a general KB note (concept / formula / reference / case / lesson / guide / moc).

    Docs (`kind: doc`) use `create_doc` instead — they carry `paragraphs_json`
    and live in a separate folder. Clauses (`kind: clause`) are typically
    captured by the citation-ingest path, not this method.

    Returns ``{ok, slug, path}`` on success, ``{error}`` on validation failure.
    """
    normalized, err = _normalize_note_params(kind, title, slug)
    if err:
        return err
    kind, title, slug = normalized
    rel = self._note_path(slug)
    if (self.vault_root / rel).exists():
        return {"error": "note already exists", "slug": slug, "path": rel}
    fm: dict = {
        "tags": ["kb"],
        "kind": kind,
        "title": title,
        "created": datetime.now().date().isoformat(),
        "updated": datetime.now().date().isoformat(),
    }
    if domain:
        fm["domain"] = domain
    if topic:
        fm["topic"] = topic
    if references:
        fm["references"] = _clean_list(references)
    if related:
        fm["related"] = _clean_list(related)
    if source:
        fm["source"] = source
    if author:
        fm["author"] = author
    body_text = _body_with_title(title, body)
    self.vault_create_note(rel, fm, body_text)
    await self.emit("kb:note_created", {"slug": slug, "path": rel, "kind": kind, "domain": domain})
    return {"ok": True, "slug": slug, "path": rel}


KB_NOTE_SYSTEM = (
    "You are a knowledge-base editor. Write ONE durable `concept` note in clean "
    "Markdown that a reader will return to months later.\n"
    "Rules:\n"
    "- Start directly with the explanation. Do NOT repeat the title as a heading "
    "(it is added automatically).\n"
    "- Use `##` section headings that follow the given outline, with short prose "
    "and bullet lists under each. Keep it tight and factual.\n"
    "- Explain in plain language; define each term the first time it appears.\n"
    "- Ground claims in the supplied source material when present; do not invent "
    "specifics (numbers, product names, APIs) that the sources don't support.\n"
    "- Do NOT add YAML frontmatter, a code fence around the whole note, a "
    "'Sources' dump, or any meta-commentary about being an AI.\n"
    "Output only the note body in Markdown."
)

KB_NOTE_USER = (
    "Write a concept note titled: {title}\n\n"
    "Cover these sections (outline):\n{outline}\n\n"
    "Brief: {brief}\n"
    "Original ask: {ask}\n\n"
    "Source material gathered from the vault (may be empty):\n{sources}"
)


async def deliver_work_notes(self, brief: dict) -> dict:
    """[[contributes.work.deliverable]] handler — a durable `concept` note
    filed into the KB from a work brief.

    Synthesises one concept note (Markdown body via ``think``, grounded in the
    gathered sources) and creates it via ``create_note`` with ``author: ai``.
    One note per run, per work's one-deliverable-per-run model.

    `brief`: {ask, title, brief, outline: [str], sources: str, run_id}.
    Returns {id, artifact_path, open_url} or {error}."""
    brief = brief or {}
    title = (brief.get("title") or brief.get("ask") or "Untitled").strip()
    outline = "\n".join(f"- {s}" for s in brief.get("outline") or []) or "- (no outline given)"
    user = KB_NOTE_USER.format(
        title=title,
        outline=outline,
        brief=brief.get("brief") or "",
        ask=brief.get("ask") or "",
        sources=(brief.get("sources") or "")[:6000],
    )
    body = await self.think(user, system=KB_NOTE_SYSTEM, domain="text", temperature=0.4)
    body = (body or "").strip()
    if body.startswith("```"):  # strip an accidental wrapping fence
        body = re.sub(r"^```[a-zA-Z]*\n", "", body)
        body = re.sub(r"\n```$", "", body).strip()
    if not body:
        return {"error": "kb note generation returned empty"}
    res = await self.create_note(kind="concept", title=title, body=body, author="ai")
    if not res.get("ok"):
        return {"error": res.get("error") or "kb create_note failed"}
    slug = res["slug"]
    return {
        "id": slug,
        "artifact_path": res.get("path", ""),
        "open_url": f"/kb/#{slug}",
    }


def _clean_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    return [str(v).strip() for v in value if str(v).strip()]


def _body_with_title(title: str, body: str = "") -> str:
    body_text = (body or "").strip()
    if not body_text:
        return f"# {title}\n"
    if not body_text.startswith("# "):
        return f"# {title}\n\n{body_text}".rstrip() + "\n"
    return body_text.rstrip() + "\n"


def _normalize_note_params(kind: str, title: str, slug: str):
    """Shared create/upsert prologue. Returns ((kind, title, slug), None) on
    success or (None, {"error": ...}) on validation failure."""
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        return None, {"error": f"kind must be one of {KINDS}"}
    if kind == "doc":
        return None, {"error": "use create_doc for kind='doc'"}
    title = (title or "").strip()
    if not title:
        return None, {"error": "title required"}
    slug = (slug or _slugify(title)).strip().lower()
    if not slug:
        return None, {"error": "could not derive slug"}
    if bad := path_segment_error(slug, "slug"):
        return None, {"error": bad}
    return (kind, title, slug), None


# Frontmatter keys upsert_note itself owns — never settable via **extra_fields
# (a caller's tags= would otherwise drop the mandatory "kb" tag and make the
# note invisible to every vault_query(tags=["kb"]) surface).
_UPSERT_RESERVED_KEYS = frozenset({"tags", "created", "updated", "path"})


async def upsert_note(
    self,
    *,
    kind: str,
    title: str,
    body: str | None = None,
    domain: str = "",
    topic: str = "",
    references: list[str] | str | None = None,
    related: list[str] | str | None = None,
    source: str = "",
    slug: str = "",
    author: str = "",
    **extra_fields,
) -> dict:
    """Create or replace a KB note by slug, preserving existing paths.

    This is the app-to-app companion to ``create_note`` for generators that
    refresh durable KB material (pronunciation guides, mined lessons, etc.).
    Unknown scalar/list kwargs become frontmatter fields so callers do not need
    KB-specific schema glue for narrow domains like ``target_phone``.

    Field-clear semantics (uniform across named and extra fields): ``None``
    means "not provided — keep the existing value"; an empty string or empty
    list removes the field from the frontmatter.
    """
    normalized, err = _normalize_note_params(kind, title, slug)
    if err:
        return err
    kind, title, slug = normalized

    match = next((n for n in self._all_notes(include_project=True)
                  if _slug_of(n.get("path", "")) == slug), None)
    match_kind = str(((match or {}).get("properties") or {}).get("kind") or "").strip().lower()
    if match_kind == "doc":
        # Slug collides with a KB doc (docs share the kb tag but live in
        # docs/ with paragraphs_json) — overwriting it here would corrupt it.
        return {"error": f"slug '{slug}' is an existing doc — use update_doc", "slug": slug}
    existed = match is not None
    rel = (match or {}).get("path") or self._note_path(slug)

    today = datetime.now().date().isoformat()
    existing = self.vault_get_properties(rel) or ((match or {}).get("properties") or {})
    fm = dict(existing)
    tags = _clean_list(fm.get("tags") or (match or {}).get("tags") or [])
    if "kb" not in tags:
        tags.insert(0, "kb")
    fm.update(
        {
            "tags": tags,
            "kind": kind,
            "title": title,
            "updated": today,
        }
    )
    if not fm.get("created"):
        fm["created"] = today

    if domain:
        fm["domain"] = domain
    if topic:
        fm["topic"] = topic
    if references is not None:
        refs = _clean_list(references)
        if refs:
            fm["references"] = refs
        else:
            fm.pop("references", None)
    if related is not None:
        rels = _clean_list(related)
        if rels:
            fm["related"] = rels
        else:
            fm.pop("related", None)
    if source:
        fm["source"] = source
    if author:
        fm["author"] = author

    for key, value in extra_fields.items():
        if not key or key.startswith("_") or key in _UPSERT_RESERVED_KEYS:
            continue
        if value is None:
            continue  # not provided — keep any existing value
        if isinstance(value, (list, tuple, set)):
            cleaned = _clean_list(value)
            if cleaned:
                fm[key] = cleaned
            else:
                fm.pop(key, None)
            continue
        if value == "":
            fm.pop(key, None)
            continue
        fm[key] = value

    if body is None and existed:
        body_text = self.vault_read_body(rel)
        if not body_text:
            body_text = _body_with_title(title)
    else:
        body_text = _body_with_title(title, body or "")

    # Overwrites in place + indexes in one step; also backfills the inferred
    # `lifecycle:` field, keeping upsert- and create-path notes identical.
    self.vault_create_note(rel, fm, body_text)
    await self.emit(
        "kb:note_upserted",
        {"slug": slug, "path": rel, "kind": kind, "domain": fm.get("domain", ""), "created": not existed},
    )
    return {"ok": True, "slug": slug, "path": rel, "created": not existed}


async def update_implemented_in(self, *, slug: str, old_ref: str,
                                new_ref: str = "") -> dict:
    """Replace (or remove, when ``new_ref`` is empty) one ``implemented_in:``
    entry on a KB note. The apply target for kb-butler's broken-code-path repair
    proposals, surfaced through the rooms review gate (never auto-applied — the
    user clicks Apply on each). Returns ``{ok, slug, implemented_in}`` or
    ``{error}``. Idempotent: a no-op replace returns ok."""
    slug = (slug or "").strip().lower()
    match = next((n for n in self._all_notes()
                  if _slug_of(n.get("path", "")) == slug), None)
    if not match:
        return {"error": "not found", "slug": slug}
    path = match.get("path", "")
    props = self.vault_get_properties(path) or {}
    refs = props.get("implemented_in") or []
    if isinstance(refs, str):
        refs = [refs]
    refs = [str(r) for r in refs]
    if old_ref not in refs:
        return {"error": "old_ref not present", "slug": slug, "old_ref": old_ref}
    new_list: list[str] = []
    for r in refs:
        if r == old_ref:
            if new_ref and new_ref not in new_list:
                new_list.append(new_ref)
        elif r not in new_list:
            new_list.append(r)
    self.vault_update(path, {"implemented_in": new_list})
    await self.emit("kb:implemented_in_updated",
                    {"slug": slug, "old": old_ref, "new": new_ref})
    return {"ok": True, "slug": slug, "implemented_in": new_list}


@web_route("POST", "/api/notes")
async def api_note_create(self, request):
    body = await self.read_json(request)
    return await self.create_note(
        kind=body.get("kind", ""),
        title=body.get("title", ""),
        body=body.get("body", ""),
        domain=body.get("domain", ""),
        topic=body.get("topic", ""),
        references=body.get("references"),
        related=body.get("related"),
        source=body.get("source", ""),
        slug=body.get("slug", ""),
    )
