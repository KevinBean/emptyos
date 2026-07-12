"""kb — revision-diff engine: align → diff → digest → rollup across two revisions.

The app-side orchestration over the pure SDK helpers
(``emptyos/sdk/revision_align.py`` + ``emptyos/sdk/doc_slice.py``). Given a
document (``standard_id``) and two revisions (by ``edition``), it:

  1. loads each revision's ``kind: clause`` notes,
  2. aligns old↔new by CONTENT (title → embeddings → LLM), not clause number,
  3. diffs each matched pair's VERBATIM text (sliced from the revision's
     full-text artifact) with ``difflib``,
  4. (lazy) summarises each change with ``think`` and rolls chapters + the whole
     document up — computed on demand to bound LLM cost,
  5. caches the structured result under ``data/apps/kb/revisions/``.

Dark-flagged behind ``[apps.kb] feature.revision-diff.enabled`` (default off):
every route early-returns ``{"error": ...}`` when off, so it adds no surface
until enabled. General for ANY ``standard_id`` with ≥2 revisions — CDIM is the
first consumer.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._all_notes`` / ``self.revisions_for_document``
(indexes), ``self.vault_read_body`` (base). Do not import from ``.app``.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.doc_slice import slice_clause_text
from emptyos.sdk.revision_align import align_revisions

from .shared import _norm_edition, _slug_of

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ────────────────────────────────
#   _revision_diff_enabled   = _revisions._revision_diff_enabled
#   _revisions_cache_dir     = _revisions._revisions_cache_dir
#   _reference_for           = _revisions._reference_for
#   _revision_fulltext       = _revisions._revision_fulltext
#   _load_revision_clauses   = _revisions._load_revision_clauses
#   compute_revision_diff    = _revisions.compute_revision_diff
#   api_revisions            = _revisions.api_revisions
#   api_revision_diff        = _revisions.api_revision_diff
#   api_revision_diff_digest = _revisions.api_revision_diff_digest
#   api_revision_diff_publish= _revisions.api_revision_diff_publish
# Adding a method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────

CLAUSE_DIGEST_SYSTEM = (
    "You summarise what changed in ONE clause of a technical standard between two "
    "revisions. Given the OLD and NEW verbatim text, write 1–3 plain sentences naming "
    "the concrete differences (added/removed requirements, changed numbers, tightened "
    "limits). If only wording changed with no substantive effect, say 'Editorial only'. "
    "No preamble, no markdown — just the sentences."
)
CHAPTER_ROLLUP_SYSTEM = (
    "You summarise, in 2–4 sentences, how a CHAPTER of a technical standard changed "
    "between two revisions, given the per-clause change notes. Lead with the most "
    "material change. No preamble, no markdown."
)


def _revision_diff_enabled(self) -> bool:
    """Dark-ship gate (default off → byte-identical: every route 400s)."""
    return bool(self.app_config("feature.revision-diff.enabled", False))


def _revisions_cache_dir(self) -> Path:
    return self.data_subdir("revisions")


def _fs_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").strip().lower()).strip("-") or "x"


def _section_title(props: dict) -> str:
    """Section title for alignment, normalized across revision title conventions.

    Prefers `clause_title`; else the text after the '§X.Y — ' prefix in `title`
    so 'TransGrid HV Cable Manual §1.7 — Ratings' and 'Transgrid CDIM Rev 1.0
    §1.7 — Ratings' both reduce to 'Ratings'.
    """
    ct = props.get("clause_title")
    if ct:
        return str(ct).strip()
    t = str(props.get("title") or "")
    parts = re.split(r"\s[—–\-]\s", t, maxsplit=1)
    return (parts[1] if len(parts) > 1 else t).strip()


def _reference_for(self, standard_id: str, edition: str) -> dict | None:
    """Return the kind:reference note properties for (standard_id, edition)."""
    sid = str(standard_id or "").strip().lower()
    ed = _norm_edition(edition)
    for n in self._all_notes():
        p = n.get("properties", {}) or {}
        if p.get("kind") != "reference":
            continue
        if str(p.get("standard_id") or "").strip().lower() != sid:
            continue
        if _norm_edition(p.get("edition")) != ed:
            continue
        out = dict(p)
        out["_slug"] = _slug_of(n.get("path", ""))
        return out
    return None


def _revision_fulltext(self, ref_props: dict) -> str:
    """Read a revision's stored full text (local_text / source_file), '' if missing."""
    rel = str((ref_props or {}).get("local_text") or (ref_props or {}).get("source_file") or "").strip()
    if not rel:
        return ""
    try:
        return (self.vault_root / rel).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _load_revision_clauses(self, standard_id: str, edition: str) -> list[dict]:
    """Clause notes for (standard_id, edition): {slug, clause_no, title, text}."""
    sid = str(standard_id or "").strip().lower()
    ed = _norm_edition(edition)
    out: list[dict] = []
    for n in self._all_notes():
        p = n.get("properties", {}) or {}
        if p.get("kind") != "clause":
            continue
        if str(p.get("standard_id") or "").strip().lower() != sid:
            continue
        if _norm_edition(p.get("edition")) != ed:
            continue
        path = n.get("path", "")
        out.append({
            "slug": _slug_of(path),
            "clause_no": str(p.get("clause") or "").replace("§", "").strip(),
            "title": _section_title(p),
            "text": self.vault_read_body(path) or "",
        })
    return out


def _unified_diff(old_v: str, new_v: str) -> dict:
    old_lines = (old_v or "").splitlines()
    new_lines = (new_v or "").splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    return {"diff": "\n".join(diff), "added": added, "removed": removed,
            "has_text": bool(old_v or new_v)}


async def _align_think(self, system: str, user: str) -> str:
    try:
        out = await self.think(user, system=system, domain="text", min_ability="standard")
        return out if isinstance(out, str) else str(out)
    except Exception:
        return ""


def _edge_key(e: dict) -> str:
    return f"{e.get('old_slug') or '∅'}|{e.get('new_slug') or '∅'}"


async def compute_revision_diff(self, standard_id: str, rev_a: str, rev_b: str,
                                *, refresh: bool = False) -> dict:
    """Align + verbatim-diff two revisions. Caches; semantic digests are lazy."""
    ref_a = self._reference_for(standard_id, rev_a)
    ref_b = self._reference_for(standard_id, rev_b)
    if not ref_a or not ref_b:
        return {"error": "revision not found",
                "have": [r["edition"] for r in self.revisions_for_document(standard_id)]}

    old = self._load_revision_clauses(standard_id, rev_a)
    new = self._load_revision_clauses(standard_id, rev_b)

    cache_path = self._revisions_cache_dir() / (
        f"{_fs_token(standard_id)}__{_fs_token(rev_a)}__{_fs_token(rev_b)}.json")
    # Input signature: clause slugs/text of both sides → invalidates on edit.
    import hashlib
    sig = hashlib.sha1(
        json.dumps([[c["slug"], len(c["text"])] for c in old + new], sort_keys=True).encode()
    ).hexdigest()[:16]
    if cache_path.exists() and not refresh:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("sig") == sig:
                return cached
        except Exception:
            pass

    embed_fn = self.embed_texts if getattr(self, "embeddings_available", False) else None

    async def think_fn(system, user):
        return await _align_think(self, system, user)

    edges = await align_revisions(old, new, embed_fn=embed_fn, think_fn=think_fn)

    ft_a = self._revision_fulltext(ref_a)
    ft_b = self._revision_fulltext(ref_b)
    for e in edges:
        old_v = slice_clause_text(ft_a, e["old_no"]) if e.get("old_no") else ""
        new_v = slice_clause_text(ft_b, e["new_no"]) if e.get("new_no") else ""
        e["verbatim_diff"] = _unified_diff(old_v, new_v)
        e["digest"] = None  # lazy — filled by /digest

    # Group by top-level chapter for the rollup view.
    def _chapter(no):
        m = re.match(r"\s*(\d+)", str(no or ""))
        return m.group(1) if m else "?"
    chapters: dict[str, list[str]] = {}
    for e in edges:
        ch = _chapter(e.get("new_no") or e.get("old_no"))
        chapters.setdefault(ch, []).append(_edge_key(e))

    counts: dict[str, int] = {}
    for e in edges:
        counts[e["status"]] = counts.get(e["status"], 0) + 1

    result = {
        "ok": True, "sig": sig,
        "standard_id": standard_id,
        "rev_a": ref_a.get("edition"), "rev_b": ref_b.get("edition"),
        "rev_a_slug": ref_a.get("_slug"), "rev_b_slug": ref_b.get("_slug"),
        "rev_a_title": ref_a.get("title"), "rev_b_title": ref_b.get("title"),
        "edges": edges,
        "chapters": chapters,
        "counts": counts,
        "doc_summary": None,  # lazy rollup
    }
    try:
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return result


# ── Routes ────────────────────────────────────────────────

@web_route("GET", "/api/revisions/{standard_id:path}")
async def api_revisions(self, request):
    """List the revisions (kind:reference notes) of a document, newest first."""
    if not self._revision_diff_enabled():
        return {"error": "revision-diff disabled"}
    sid = request.path_params.get("standard_id", "")
    return {"standard_id": sid, "revisions": self.revisions_for_document(sid)}


@web_route("POST", "/api/revision-diff")
async def api_revision_diff(self, request):
    """Align + verbatim-diff two revisions of a document. Body: {standard_id, rev_a, rev_b, refresh?}."""
    if not self._revision_diff_enabled():
        return {"error": "revision-diff disabled"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    sid = (body.get("standard_id") or "").strip()
    rev_a = (body.get("rev_a") or "").strip()
    rev_b = (body.get("rev_b") or "").strip()
    if not sid or not rev_a or not rev_b:
        return {"error": "standard_id, rev_a, rev_b required"}
    return await self.compute_revision_diff(sid, rev_a, rev_b, refresh=bool(body.get("refresh")))


@web_route("POST", "/api/revision-diff/digest")
async def api_revision_diff_digest(self, request):
    """Lazily compute + cache one clause-pair's semantic 'what changed' digest.

    Body: {standard_id, rev_a, rev_b, old_slug?, new_slug?}. Returns {digest}.
    """
    if not self._revision_diff_enabled():
        return {"error": "revision-diff disabled"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    sid = (body.get("standard_id") or "").strip()
    rev_a = (body.get("rev_a") or "").strip()
    rev_b = (body.get("rev_b") or "").strip()
    old_slug = body.get("old_slug")
    new_slug = body.get("new_slug")
    if not sid or not rev_a or not rev_b:
        return {"error": "standard_id, rev_a, rev_b required"}
    result = await self.compute_revision_diff(sid, rev_a, rev_b)
    if "edges" not in result:
        return result
    edge = next((e for e in result["edges"]
                 if e.get("old_slug") == old_slug and e.get("new_slug") == new_slug), None)
    if not edge:
        return {"error": "edge not found"}
    if edge.get("digest"):
        return {"digest": edge["digest"], "cached": True}
    if edge["status"] in ("added", "removed"):
        digest = ("New in " + str(result["rev_b"]) if edge["status"] == "added"
                  else "Present in " + str(result["rev_a"]) + ", removed in " + str(result["rev_b"]))
    else:
        old_v = "\n".join(l[1:] for l in (edge["verbatim_diff"]["diff"].splitlines())
                          if l.startswith("-") and not l.startswith("---"))
        new_v = "\n".join(l[1:] for l in (edge["verbatim_diff"]["diff"].splitlines())
                          if l.startswith("+") and not l.startswith("+++"))
        user = f"OLD ({result['rev_a']}):\n{old_v or '(unchanged text)'}\n\nNEW ({result['rev_b']}):\n{new_v or '(unchanged text)'}"
        digest = await _align_think(self, CLAUSE_DIGEST_SYSTEM, user) or "Editorial only"
    edge["digest"] = digest
    # persist back into the cache
    cache_path = self._revisions_cache_dir() / (
        f"{_fs_token(sid)}__{_fs_token(rev_a)}__{_fs_token(rev_b)}.json")
    try:
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return {"digest": digest, "cached": False}


@web_route("POST", "/api/revision-diff/publish")
async def api_revision_diff_publish(self, request):
    """Write a durable kind:doc change-summary note for a revision pair.

    Body: {standard_id, rev_a, rev_b}. AI-authored; marked author: ai. v1 writes
    directly (the propose/preview gate is the documented graduation).
    """
    if not self._revision_diff_enabled():
        return {"error": "revision-diff disabled"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    sid = (body.get("standard_id") or "").strip()
    rev_a = (body.get("rev_a") or "").strip()
    rev_b = (body.get("rev_b") or "").strip()
    if not sid or not rev_a or not rev_b:
        return {"error": "standard_id, rev_a, rev_b required"}
    result = await self.compute_revision_diff(sid, rev_a, rev_b)
    if "edges" not in result:
        return result
    lines = [f"# Change summary — {result['rev_a']} → {result['rev_b']}", ""]
    lines.append(f"Document `{sid}`. {result['counts']}.")
    lines.append("")
    for e in result["edges"]:
        if e["status"] == "unchanged":
            continue
        tag = e["status"]
        old_ref = f"§{e['old_no']}" if e.get("old_no") else "—"
        new_ref = f"§{e['new_no']}" if e.get("new_no") else "—"
        title = e.get("new_title") or e.get("old_title") or ""
        d = e.get("digest") or ""
        lines.append(f"- **{tag}** {old_ref} → {new_ref} · {title}" + (f" — {d}" if d else ""))
    slug = f"changes-{_fs_token(sid)}-{_fs_token(rev_a)}-to-{_fs_token(rev_b)}"
    rel = f"30_Resources/EmptyOS/kb/docs/{slug}.md"
    fm = {
        "tags": ["kb"], "kind": "doc", "author": "ai",
        "title": f"Change summary: {result['rev_a']} → {result['rev_b']}",
        "standard_id": sid, "topic": "revision-diff",
        "related": [result.get("rev_a_slug"), result.get("rev_b_slug")],
    }
    self.vault_create_note(rel, fm, "\n".join(lines))
    return {"ok": True, "slug": slug, "path": rel}
