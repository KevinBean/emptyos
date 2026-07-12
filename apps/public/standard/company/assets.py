"""company — org assets — KB slugs + vault paths fed into every scenario.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The `assets:` array on an org note: resolving refs into reader records (KB via kb_explain, vault via body read), adding, and removing. Each asset body capped to ASSET_BODY_CHARS. Source of truth for what context an org carries into scenario prompts..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._replace_section (spine) to keep the `## Assets` body in sync with frontmatter; self.kb_explain + self.call_app('kb', ...) for KB resolution.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from .shared import _vault_rel_org
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import CompanyApp  # noqa: F401 — for type hints only


# ─── Bind to CompanyApp class as ────────────────────────────────
#   _is_vault_path       = _assets._is_vault_path  # @staticmethod
#   _read_assets_refs    = _assets._read_assets_refs
#   _render_assets_body  = _assets._render_assets_body
#   list_assets          = _assets.list_assets
#   add_asset            = _assets.add_asset
#   remove_asset         = _assets.remove_asset
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@staticmethod
def _is_vault_path(ref: str) -> bool:
    """A ref looks like a vault path if it has a slash or ends in .md."""
    r = (ref or "").strip()
    return ("/" in r) or r.endswith(".md")


def _read_assets_refs(self, oid: str) -> list[str]:
    fm = self.vault_get_properties(_vault_rel_org(oid)) or {}
    raw = fm.get("assets") or []
    # Frontmatter may parse a single-entry list as a bare string — coerce.
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in raw if str(x).strip()]


def _render_assets_body(self, refs: list[str]) -> str:
    """Render the `## Assets` body section as wikilinks (vault paths) or
    bare slugs (KB) for human navigation. Frontmatter is the queryable
    index; body is for reading."""
    if not refs:
        return "_No assets linked yet. Add via the Assets panel or `POST /orgs/api/orgs/<id>/assets`._"
    lines = []
    for ref in refs:
        if self._is_vault_path(ref):
            lines.append(f"- [[{ref}]]")
        else:
            lines.append(f"- KB: `{ref}`")
    return "\n".join(lines)


async def list_assets(self, org_id: str) -> list[dict]:
    """Resolve an org's `assets:` array into reader-friendly records.
    Hybrid: bare slug → KB note via kb_explain; vault path → vault body.
    Each body capped to ASSET_BODY_CHARS so 4 assets stay under ~8k.

    Returns [{kind, ref, title, body}, ...]. Failed lookups become
    {kind, ref, title: ref, body: "", error: "..."} so the caller still
    sees the broken link without crashing the scenario.
    """
    oid = (org_id or "").strip()
    if not oid or not self.vault_get_properties(_vault_rel_org(oid)):
        return []
    out: list[dict] = []
    for ref in self._read_assets_refs(oid):
        try:
            if self._is_vault_path(ref):
                props = self.vault_get_properties(ref) or {}
                body = self.vault_read_body(ref) or ""
                title = (
                    props.get("title")
                    or props.get("name")
                    or ref.rsplit("/", 1)[-1].replace(".md", "")
                )
                out.append({
                    "kind": "vault",
                    "ref": ref,
                    "title": str(title),
                    "body": (body or "")[: self.ASSET_BODY_CHARS],
                })
            else:
                note = await self.kb_explain(ref) or {}
                if note.get("error"):
                    out.append({
                        "kind": "kb", "ref": ref, "title": ref,
                        "body": "", "error": note.get("error"),
                    })
                    continue
                # kb.get_note returns kind + title inside `properties`, not
                # top-level. Docs additionally store their content in a
                # `paragraphs_json` field that get_note doesn't decode — the
                # full structured body is only available via kb.get_doc.
                props = note.get("properties") or {}
                kb_kind = (props.get("kind") or note.get("kind") or "").strip()
                title = (
                    props.get("title")
                    or note.get("title")
                    or note.get("name")
                    or ref
                )
                body = note.get("body") or ""
                if kb_kind == "doc":
                    try:
                        doc = await self.call_app("kb", "get_doc", slug=ref) or {}
                    except Exception:
                        doc = {}
                    paragraphs = doc.get("paragraphs") or []
                    if paragraphs:
                        # Canonical shape is {title, content}; tolerate
                        # the older {heading, text} aliases on read.
                        rendered = "\n\n".join(
                            f"## {(p.get('title') or p.get('heading') or '')}\n"
                            f"{(p.get('content') or p.get('text') or '')}".strip()
                            for p in paragraphs
                            if p.get("title") or p.get("heading")
                               or p.get("content") or p.get("text")
                        )
                        if rendered:
                            body = rendered
                    title = doc.get("title") or title
                out.append({
                    "kind": "kb",
                    "ref": ref,
                    "title": str(title),
                    "body": body[: self.ASSET_BODY_CHARS],
                    "kb_kind": kb_kind,
                })
        except Exception as e:
            out.append({
                "kind": "vault" if self._is_vault_path(ref) else "kb",
                "ref": ref, "title": ref, "body": "", "error": str(e)[:200],
            })
    return out


async def add_asset(self, org_id: str, ref: str) -> dict:
    oid = (org_id or "").strip()
    ref = (ref or "").strip()
    if not oid or not ref:
        return {"error": "org_id and ref required"}
    rel = _vault_rel_org(oid)
    if not self.vault_get_properties(rel):
        return {"error": f"org '{oid}' not found"}
    # Validate the ref points somewhere readable. Don't block on KB
    # unavailability — a freshly-clipped clause may be authored before
    # the KB note exists.
    if self._is_vault_path(ref) and not self.vault_get_properties(ref):
        return {"error": f"vault path '{ref}' not found"}
    refs = self._read_assets_refs(oid)
    if ref in refs:
        return {"ok": True, "ref": ref, "note": "already linked"}
    refs.append(ref)
    self.vault_update(rel, {"assets": refs})
    self._replace_section(rel, "Assets", self._render_assets_body(refs))
    await self.emit("orgs:org_updated", {
        "id": oid, "field": "assets", "value": ref, "op": "add",
    })
    return {"ok": True, "ref": ref, "asset_count": len(refs)}


async def remove_asset(self, org_id: str, ref: str) -> dict:
    oid = (org_id or "").strip()
    ref = (ref or "").strip()
    if not oid or not ref:
        return {"error": "org_id and ref required"}
    rel = _vault_rel_org(oid)
    if not self.vault_get_properties(rel):
        return {"error": f"org '{oid}' not found"}
    refs = self._read_assets_refs(oid)
    if ref not in refs:
        return {"error": f"ref '{ref}' not linked"}
    refs = [r for r in refs if r != ref]
    self.vault_update(rel, {"assets": refs})
    self._replace_section(rel, "Assets", self._render_assets_body(refs))
    await self.emit("orgs:org_updated", {
        "id": oid, "field": "assets", "value": ref, "op": "remove",
    })
    return {"ok": True, "ref": ref, "asset_count": len(refs)}
