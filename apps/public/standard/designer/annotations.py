"""designer — 墨刀/Figma-style interface-spec annotations on a generated page.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
annotation pass that documents an already-generated page as a spec — one note
per significant element (logic / validation / exceptions), each pinned to a
`data-eos-el` anchor so the `?annotate=1` overlay (designer-annotate.js) can
position a numbered pin over it. This is the connect that lands the
"interactive prototype + native annotations in one artifact" story: designer
makes the interactive HTML, this layer documents it on the elements, reusing
the same anchor space as the element-edit loop (.claude/rules/artifact-element-edit.md).

The annotation *menu* + *output validation* are pure helpers in shared.py
(`build_annotate_menu`, `normalize_annotations`) — unit-tested offline. This
module owns the I/O seams: the think call, annotations.json read/write, and the
two endpoints.

Pins reuse the same `data-eos-el` anchors stamped by anchors.py `_maybe_anchor`,
so annotating requires the element-edit feature to be on (a page generated with
edit off carries no anchors) — `api_annotate` checks that dependency explicitly.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._record_dir / self._rel_record (generation);
self._annotate_enabled / self._edit_enabled (anchors).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.html_anchors import list_anchors
from emptyos.sdk.utils import parse_llm_json

from .shared import (
    DESIGNER_ANNOTATE_SYSTEM,
    build_annotate_menu,
    normalize_annotations,
)

if TYPE_CHECKING:
    from .app import DesignerApp  # noqa: F401 — for type hints only


# ─── Bind to DesignerApp class as ────────────────────────────────────
#   _annotations_path   = _annotations._annotations_path
#   read_annotations    = _annotations.read_annotations
#   _write_annotations  = _annotations._write_annotations
#   api_annotate        = _annotations.api_annotate
#   api_annotations_get = _annotations.api_annotations_get
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

_ANNOTATE_CAP = 10


def _annotations_path(self, rid: str):
    """Path to a design's annotations sidecar (next to page.html)."""
    return self._record_dir(rid) / "annotations.json"


def read_annotations(self, rid: str) -> list[dict]:
    """Return a design's saved annotations (``[]`` if none / unreadable).

    Public verb — backs api_annotations_get AND the ?annotate=1 server-side
    inject in routes.py (the baked overlay reads the same store)."""
    p = self._annotations_path(rid)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def _write_annotations(self, rid: str, items: list[dict]) -> None:
    p = self._annotations_path(rid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")


@web_route("POST", "/api/annotate")
async def api_annotate(self, request) -> dict:
    """Generate interface-spec annotations for an existing design.

    Reads the anchored page.html, asks the model for one spec note per
    significant element keyed to a real `data-eos-el` anchor, validates the
    output against the actual anchor set, and persists annotations.json.
    """
    if not self._annotate_enabled():
        return {"ok": False, "error": "annotations are disabled"}
    if not self._edit_enabled():
        return {
            "ok": False,
            "error": "annotations need element anchors — also enable "
                     "feature.element-edit.enabled and regenerate the page",
        }

    body = await request.json()
    rid = (body.get("id") or "").strip()
    if not rid:
        return {"ok": False, "error": "id is required"}

    html_path = self._record_dir(rid) / "page.html"
    if not html_path.exists():
        return {"ok": False, "error": f"no design with id '{rid}'"}
    doc = html_path.read_text(encoding="utf-8")

    anchors = list_anchors(doc)
    if not anchors:
        return {
            "ok": False,
            "error": "this page has no element anchors — regenerate it with "
                     "feature.element-edit.enabled on",
        }

    menu = build_annotate_menu(anchors)
    user = f"ANCHOR MENU (copy `el` ids verbatim):\n{menu}\n\nPAGE HTML:\n{doc}"
    raw = await self.think(
        user,
        system=DESIGNER_ANNOTATE_SYSTEM,
        domain=self.app_config("think_domain", "code"),
        temperature=0.2,
        max_tokens=2048,
    )
    text = raw if isinstance(raw, str) else str(raw)
    parsed = parse_llm_json(text, fallback=[])
    valid_ids = {a["el"] for a in anchors}
    items = normalize_annotations(parsed, valid_ids, cap=_ANNOTATE_CAP)

    self._write_annotations(rid, items)
    await self.emit("designer:updated", {"id": rid})
    return {"ok": True, "id": rid, "count": len(items), "items": items}


@web_route("GET", "/api/annotations/{rid}")
async def api_annotations_get(self, request) -> dict:
    """Return a design's saved annotations (read-only)."""
    rid = request.path_params.get("rid", "")
    if not rid:
        return {"ok": False, "error": "id is required"}
    return {"ok": True, "id": rid, "items": self.read_annotations(rid)}
