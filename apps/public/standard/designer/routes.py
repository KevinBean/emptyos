"""designer — CRUD + non-streaming web/CLI surface.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
read/list/get/html/delete REST surface, the generate + iterate endpoints, the
meta endpoint (styles list + model ability), the timeline entity_source, and the
CLI commands.

Mirrors apps/public/standard/viz/routes.py. The key difference: `/api/meta`
folds the design-system style list (from KB) together with the active-model
ability (designer has no per-shape gate — page generation has one bar).

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.generate / self._think_html / self._persist /
self._build_system / self._reject_reason (generation); self._record_dir /
self._rel_record / self._rel_html / self._outputs_root (generation).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from emptyos.sdk import cli_command, web_route

from .shared import DESIGNER_MIN_ABILITY, _rewrite_user_msg

if TYPE_CHECKING:
    from .app import DesignerApp  # noqa: F401 — for type hints only


# ─── Bind to DesignerApp class as ────────────────────────────────────
#   _list_styles   = _routes._list_styles
#   api_meta       = _routes.api_meta
#   api_generate   = _routes.api_generate
#   api_iterate    = _routes.api_iterate
#   api_rendered   = _routes.api_rendered
#   list_items     = _routes.list_items
#   api_list       = _routes.api_list
#   api_get        = _routes.api_get
#   api_html       = _routes.api_html
#   api_delete     = _routes.api_delete
#   artifact_path  = _routes.artifact_path
#   cli_list       = _routes.cli_list
#   cli_generate   = _routes.cli_generate
#   deliver_work   = _routes.deliver_work
#   iterate        = _routes.iterate
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


async def _list_styles(self) -> list[dict]:
    """Design-system pattern notes available as styles (kind:pattern, topic:ui-design)."""
    items: list[dict] = []
    seen: set[str] = set()
    try:
        res = await self.call_app("kb", "list_notes", kind="pattern", topic="ui-design")
    except Exception:
        res = None
    if isinstance(res, dict):
        for n in res.get("notes", []) or []:
            slug = (n.get("slug") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            items.append({
                "slug": slug,
                "title": n.get("title") or slug,
                "domain": n.get("domain", ""),
            })
    items.sort(key=lambda r: r["title"])
    return items


@web_route("GET", "/api/meta")
async def api_meta(self, request) -> dict:
    """Styles list + the active model's ability (one round-trip for boot).

    Lets the UI offer the design-system dropdown and show a degrade-and-explain
    banner when the active model is below the generation bar. See
    .claude/rules/model-ability.md.
    """
    info = await self.model_ability(domain=self.app_config("think_domain", "code"))
    return {
        "styles": await self._list_styles(),
        "active_ability": info.get("ability", "standard"),
        "min_ability": DESIGNER_MIN_ABILITY,
        "provider": info.get("provider", ""),
        "model": info.get("model", ""),
        "embed_default": bool(self.app_config("embed_viz", True)),
        "edit_enabled": self._edit_enabled(),
        # Annotations pin onto the element-edit anchors, so the overlay needs
        # both flags on — expose the effective capability.
        "annotate_enabled": self._annotate_enabled() and self._edit_enabled(),
    }


@web_route("POST", "/api/generate")
async def api_generate(self, request) -> dict:
    body = await request.json()
    return await self.generate(
        body.get("prompt", ""),
        style=body.get("style") or None,
        embed=body.get("embed"),
    )


@web_route("POST", "/api/iterate")
async def api_iterate(self, request) -> dict:
    body = await request.json()
    return await self.iterate((body.get("id") or "").strip(), (body.get("prompt") or "").strip())


async def iterate(self, rid: str, prompt: str) -> dict:
    """Whole-file iterate on an existing design. Public verb — backs
    api_iterate AND cross-app refine (work app's [[contributes.work.deliverable]]
    `iterate` hook)."""
    if not rid or not prompt:
        return {"ok": False, "error": "id and prompt are required"}

    record_dir = self._record_dir(rid)
    html_path = record_dir / "page.html"
    if not html_path.exists():
        return {"ok": False, "error": f"no design with id '{rid}'"}

    existing = self.vault_get_properties(self._rel_record(rid)) or {}
    style = existing.get("style", "") or ""
    prior_html = html_path.read_text(encoding="utf-8")
    prior_prompt = existing.get("prompt", "")

    user_msg = _rewrite_user_msg(prior_prompt, prompt, prior_html)
    system = await self._build_system(style)
    html = await self._think_html(system, user_msg)
    reason = self._reject_reason(html)
    if reason:
        return {"ok": False, "error": reason}

    # Whole-file rewrite preserves already-baked <iframe srcdoc> embeds (the
    # iterate prompt tells the model to keep them); we don't re-expand.
    meta = await self._persist(rid, html, prompt, style, [], is_update=True)
    await self.emit("designer:updated", {"id": rid})
    return {"ok": True, **meta}


async def deliver_work(self, brief: dict) -> dict:
    """[[contributes.work.deliverable]] handler — styled web page from a work brief.

    `brief`: {ask, title, brief, outline: [str], sources: str, run_id}.
    Returns {id, preview_url, artifact_path, open_url} or {error}."""
    brief = brief or {}
    outline = "\n".join(f"- {s}" for s in brief.get("outline") or [])
    prompt = (
        f"Create a standalone web page titled: {brief.get('title') or brief.get('ask') or 'Untitled'}\n"
        f"Purpose: {brief.get('brief') or ''}\nAsk: {brief.get('ask') or ''}\n"
        f"Content sections:\n{outline}\n\nSource material:\n{brief.get('sources') or ''}"
    )
    res = await self.generate(prompt)
    if not res.get("ok"):
        return {"error": res.get("error") or "designer generate failed"}
    rid = res["id"]
    return {
        "id": rid,
        "preview_url": f"/designer/api/html/{rid}",
        "artifact_path": res.get("record_path", ""),
        "open_url": f"/designer/#{rid}",
    }


@web_route("POST", "/api/rendered")
async def api_rendered(self, request) -> dict:
    body = await request.json()
    rid = (body.get("id") or "").strip()
    if not rid:
        return {"ok": False, "error": "id is required"}
    await self.emit("designer:rendered", {"id": rid})
    return {"ok": True}


async def list_items(self) -> list[dict]:
    """All design records, newest first. Public verb (api_list + cli_list)."""
    rows = []
    root = self._outputs_root()
    if not root.exists():
        return []
    for sub in sorted(root.iterdir(), reverse=True):
        if not sub.is_dir():
            continue
        fm = self.vault_get_properties(self._rel_record(sub.name)) or {}
        if not fm:
            continue
        rows.append({
            "id": fm.get("designer_id") or sub.name,
            "style": fm.get("style", ""),
            "prompt": fm.get("prompt", ""),
            "created": fm.get("created", ""),
            "updated": fm.get("updated", ""),
            "size_kb": fm.get("size_kb", 0),
            "embeds": fm.get("embeds", []) or [],
        })
    return rows


@web_route("GET", "/api/list")
async def api_list(self, request) -> list[dict]:
    return await self.list_items()


@web_route("GET", "/api/get/{rid}")
async def api_get(self, request) -> dict:
    rid = request.path_params.get("rid", "")
    fm = self.vault_get_properties(self._rel_record(rid))
    if not fm:
        return {"ok": False, "error": f"no design with id '{rid}'"}
    return {
        "ok": True,
        "id": rid,
        "style": fm.get("style", ""),
        "prompt": fm.get("prompt", ""),
        "created": fm.get("created", ""),
        "updated": fm.get("updated", ""),
        "size_kb": fm.get("size_kb", 0),
        "embeds": fm.get("embeds", []) or [],
        "history": fm.get("history", []),
        "html_path": self._rel_html(rid),
    }


@web_route("GET", "/api/html/{rid}")
async def api_html(self, request):
    """Serve page.html for iframe consumption (no-store so iterate/edit shows).

    Query modifiers:
      ?edit=1     — inject the element-edit shim so clicks become edit picks
                    (only when feature.element-edit.enabled is on).
      ?annotate=1 — inject the 墨刀-style annotation overlay (numbered pins +
                    spec popovers) baked into the page; reads annotations.json.
                    Only when feature.annotations.enabled + element-edit are on.
      ?download=1 — strip data-eos-el anchors for a clean standalone export.
    """
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    rid = request.path_params.get("rid", "")
    html_path = self._record_dir(rid) / "page.html"
    if not html_path.exists():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

    qp = request.query_params
    edit = qp.get("edit") == "1" and self._edit_enabled()
    annotate = qp.get("annotate") == "1" and self._annotate_enabled() and self._edit_enabled()
    download = qp.get("download") == "1"

    if not edit and not annotate and not download:
        return FileResponse(
            html_path,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    html = html_path.read_text(encoding="utf-8")
    if download:
        from emptyos.sdk.html_anchors import strip_attr
        html = strip_attr(html, "data-eos-el")
    if edit:
        html = _inject_before_body(html, '<script src="/static/eos-edit-shim.js"></script>')
    if annotate:
        items = self.read_annotations(rid)
        # The overlay is deliberately self-contained (its own scoped .dz-annot-*
        # styles + popover) — we do NOT load eos-flipbook.css, whose page-owning
        # `body{}` / :root rules would repaint the generated page. <-escape the
        # data blob so an annotation containing "</script>" can't break out.
        blob = json.dumps(items, ensure_ascii=False).replace("<", "\\u003c")
        layer = (
            f'<script>window.__EOS_ANNOTATIONS__ = {blob};</script>'
            '<script src="/static/designer-annotate.js"></script>'
        )
        html = _inject_before_body(html, layer)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def _inject_before_body(html: str, snippet: str) -> str:
    """Insert `snippet` just before </body> (or append if no body close)."""
    if "</body>" in html:
        return html.replace("</body>", snippet + "</body>", 1)
    return html + snippet


@web_route("DELETE", "/api/items/{rid}")
async def api_delete(self, request) -> dict:
    import shutil

    rid = request.path_params.get("rid", "")
    record_dir = self._record_dir(rid)
    if not record_dir.exists():
        return {"ok": False, "error": f"no design with id '{rid}'"}
    shutil.rmtree(record_dir)
    return {"ok": True, "id": rid}


async def artifact_path(self, id: str = "") -> str:
    """Vault-relative path for the timeline aggregator's entity_source."""
    return self.vault_rel_if_exists(self._rel_record(id)) if id else ""


@cli_command("list")
async def cli_list(self) -> None:
    for r in await self.list_items():
        style = f" ({r['style']})" if r.get("style") else ""
        print(f"{r['id']}{style} {r['prompt'][:60]}")


@cli_command("generate")
async def cli_generate(self, prompt: str, style: str = "") -> None:
    """eos designer generate "<prompt>" [style-slug]"""
    result = await self.generate(prompt, style=style or None)
    print(json.dumps(result, indent=2))
