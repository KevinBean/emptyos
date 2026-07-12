"""kb — composition doc CRUD + paragraph parsing + render.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Doc-specific helpers (paragraph parse/encode/normalize/refs, slug→path), all doc CRUD + the render endpoint, plus the `action_summarize_notes` orchestrator.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._note_path (notes), self.get_note (notes), self._lookup_ref (indexes).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from emptyos.sdk import web_route
from typing import TYPE_CHECKING

from .shared import _slug_of, _slugify

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ────────────────────────────────
#   _parse_paragraphs       = _docs._parse_paragraphs
#   _encode_paragraphs      = _docs._encode_paragraphs
#   _normalize_paragraphs   = _docs._normalize_paragraphs  # @staticmethod
#   _para_refs              = _docs._para_refs  # @staticmethod
#   list_docs               = _docs.list_docs
#   get_doc                 = _docs.get_doc
#   create_doc              = _docs.create_doc
#   update_doc              = _docs.update_doc
#   render_doc              = _docs.render_doc
#   _slug_to_path           = _docs._slug_to_path
#   api_docs                = _docs.api_docs
#   api_doc_detail          = _docs.api_doc_detail
#   api_doc_create          = _docs.api_doc_create
#   api_doc_update          = _docs.api_doc_update
#   delete_doc              = _docs.delete_doc
#   api_doc_delete          = _docs.api_doc_delete
#   action_summarize_notes  = _docs.action_summarize_notes
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _parse_paragraphs(self, props: dict) -> list[dict]:
    """Decode paragraphs from the document's flat-frontmatter field."""
    paras = self.vault_decode_json(props.get("paragraphs_json"), default=[])
    return paras if isinstance(paras, list) else []


def _encode_paragraphs(self, paras: list[dict]) -> str:
    return self.vault_encode_json(paras)


@staticmethod
def _normalize_paragraphs(paras) -> list[dict]:
    """Accept paragraphs in the docs.html-native shape `{title, content,
    noteRefs}` OR the LLM-friendly shape `{heading, text}` that personas
    and ad-hoc consumers tend to emit. Rewrite the latter into the
    former so the docs viewer renders them. Idempotent — values already
    on `title`/`content` are preserved when both shapes are present.
    """
    if not isinstance(paras, list):
        return []
    out: list[dict] = []
    for p in paras:
        if not isinstance(p, dict):
            continue
        title = p.get("title") or p.get("heading") or ""
        content = p.get("content") or p.get("text") or ""
        note_refs = p.get("noteRefs") or p.get("blockRefs") or []
        norm = {
            "title": title,
            "content": content,
            "noteRefs": list(note_refs) if isinstance(note_refs, list) else [],
        }
        out.append(norm)
    return out


@staticmethod
def _para_refs(para: dict) -> list[str]:
    """Return paragraph references, preferring `noteRefs` and falling back to
    legacy `blockRefs` for in-flight content authored before the rename."""
    refs = para.get("noteRefs") or para.get("blockRefs") or []
    return [str(r) for r in refs if r]


def list_docs(self) -> dict:
    rows = self.vault_query(tags=["kb"]) or []
    out = []
    for n in rows:
        props = n.get("properties", {}) or {}
        if props.get("kind") != "doc":
            continue
        slug = _slug_of(n.get("path", ""))
        paras = self._parse_paragraphs(props)
        out.append({
            "id": slug,
            "slug": slug,
            "path": n.get("path", ""),
            "title": props.get("title") or slug.replace("-", " "),
            "paragraph_count": len(paras),
            "note_refs": sum(len(self._para_refs(p)) for p in paras),
            "updated": props.get("updated", ""),
        })
    out.sort(key=lambda r: r["slug"])
    return {"docs": out, "count": len(out)}


async def get_doc(self, slug: str) -> dict:
    rel = self._doc_path(slug)
    if not (self.vault_root / rel).exists():
        return {"error": "not found", "slug": slug}
    props = self.vault_get_properties(rel) or {}
    body = self.vault_read_body(rel)
    return {
        "slug": slug,
        "path": rel,
        "title": props.get("title") or slug.replace("-", " "),
        "paragraphs": self._parse_paragraphs(props),
        "body": body,
        "properties": props,
    }


async def create_doc(self, title: str, paragraphs: list[dict] | None = None) -> dict:
    title = (title or "").strip()
    if not title:
        return {"error": "title required"}
    slug = _slugify(title)
    rel = self._doc_path(slug)
    if (self.vault_root / rel).exists():
        return {"error": "doc already exists", "slug": slug}
    paras = self._normalize_paragraphs(paragraphs or [])
    fm = {
        "title": title,
        "tags": ["kb"],
        "kind": "doc",
        "paragraphs_json": self._encode_paragraphs(paras),
        "created": datetime.now().date().isoformat(),
        "updated": datetime.now().date().isoformat(),
    }
    self.vault_create_note(rel, fm, f"# {title}\n\nEdit paragraphs in the KB Documents tab.\n")
    await self.emit("kb:doc_created", {"slug": slug, "path": rel})
    return {"ok": True, "slug": slug, "path": rel}


async def update_doc(self, slug: str, title: str | None = None, paragraphs: list[dict] | None = None) -> dict:
    rel = self._doc_path(slug)
    if not (self.vault_root / rel).exists():
        return {"error": "not found", "slug": slug}
    updates: dict = {"updated": datetime.now().date().isoformat()}
    if title:
        updates["title"] = title
    if paragraphs is not None:
        updates["paragraphs_json"] = self._encode_paragraphs(self._normalize_paragraphs(paragraphs))
    self.vault_update(rel, updates)
    await self.emit("kb:doc_updated", {"slug": slug})
    return {"ok": True, "slug": slug}


async def render_doc(self, slug: str) -> dict:
    """Render a doc by resolving every noteRef to its current body.

    Each paragraph's `noteRefs` (with `blockRefs` accepted for legacy content)
    may be a bare slug or `slug#section-name`. The bare-slug form pulls the
    whole note body; the anchored form pulls just the named `##` section via
    `BaseApp.vault_read_section`.

    Returns paragraphs annotated with
        rendered: [{ref, title, body, path, kind, section?, missing?}]
    so the client can render block-by-block without re-fetching.
    """
    doc = await self.get_doc(slug)
    if "error" in doc:
        return doc
    idx = self._slug_to_path()
    for para in doc.get("paragraphs") or []:
        rendered: list[dict] = []
        for raw in self._para_refs(para):
            ref = raw.strip()
            target_slug, _, section = ref.partition("#")
            target_slug = target_slug.strip()
            section = section.strip() or None
            note_path = idx.get(target_slug)
            if not note_path:
                rendered.append({"ref": ref, "slug": target_slug, "section": section, "missing": True})
                continue
            props = self.vault_get_properties(note_path) or {}
            if section:
                body = self.vault_read_section(note_path, section) or ""
            else:
                body = self.vault_read_body(note_path)
            rendered.append({
                "ref": ref,
                "slug": target_slug,
                "section": section,
                "title": props.get("title") or target_slug.replace("-", " "),
                "kind": props.get("kind", ""),
                "body": body,
                "path": note_path,
            })
        para["rendered"] = rendered
    return doc


def _slug_to_path(self) -> dict[str, str]:
    """Build a slug → path map across every kb-tagged note. Used by render_doc."""
    out: dict[str, str] = {}
    for n in self._all_notes():
        slug = _slug_of(n.get("path", ""))
        if slug:
            out[slug] = n.get("path", "")
    return out


@web_route("GET", "/api/docs")
async def api_docs(self, request):
    return self.list_docs()


@web_route("GET", "/api/docs/{slug}")
async def api_doc_detail(self, request):
    return await self.render_doc(request.path_params.get("slug", ""))


@web_route("POST", "/api/docs")
async def api_doc_create(self, request):
    body = await request.json()
    return await self.create_doc(
        title=body.get("title", ""),
        paragraphs=body.get("paragraphs"),
    )


@web_route("POST", "/api/docs/{slug}/update")
async def api_doc_update(self, request):
    body = await request.json()
    slug = request.path_params.get("slug", "")
    return await self.update_doc(
        slug,
        title=body.get("title"),
        paragraphs=body.get("paragraphs"),
    )


async def delete_doc(self, slug: str) -> dict:
    rel = self._doc_path(slug)
    abs_path = self.vault_root / rel
    if not abs_path.exists():
        return {"error": "not found", "slug": slug}
    abs_path.unlink()
    vi = self.kernel.services.get_optional("vault_index")
    if vi:
        vi.index_file(rel)
    await self.emit("kb:doc_deleted", {"slug": slug})
    return {"ok": True, "slug": slug}


@web_route("DELETE", "/api/docs/{slug}")
async def api_doc_delete(self, request):
    return await self.delete_doc(request.path_params.get("slug", ""))


async def action_summarize_notes(self, items: list | None = None, style: str = "bullet", **_) -> dict:
    """Action template: collapse N KB notes into one summary.

    items — list of note slugs (or note paths; basename is taken as slug).
    style — "bullet" (5-9 tight bullets) or "paragraph" (3-5 prose sentences).
    Returns {summary, used_slugs, missing} so the UI can show what landed.
    """
    slugs = []
    for it in items or []:
        s = str(it).strip()
        if not s:
            continue
        slugs.append(Path(s).stem if "/" in s or "\\" in s else s)
    if not slugs:
        return {"error": "no notes selected"}
    bodies = []
    missing = []
    for s in slugs:
        n = await self.get_note(s)
        if "error" in n:
            missing.append(s)
        else:
            title = (n.get("properties") or {}).get("title") or n.get("name") or s
            bodies.append(f"## {title}\n{n.get('body','')}\n")
    if not bodies:
        return {"error": "no readable notes", "missing": missing}
    joined = "\n".join(bodies)
    if style == "paragraph":
        tail = "Write 3-5 sentences as a single paragraph. No bullets."
    else:
        tail = "Write 5-9 tight bullets, each a single line. No prose."
    prompt = f"Summarize these notes. {tail}\n\nNotes:\n{joined}"
    summary = await self.think(
        prompt,
        system=self.SUMMARIZE_NOTES_SYSTEM,
        domain="text",
        temperature=0.4,
    )
    return {
        "summary": summary,
        "used_slugs": [s for s in slugs if s not in missing],
        "missing": missing,
        "style": style,
    }
