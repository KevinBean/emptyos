"""Guideline — parent docs with many ## clauses.

A guideline is a single vault note containing many `## Clause heading` sections.
Each clause is freeform markdown (rule + rationale + how-to-apply). Parent owns
title/category/status/created/updated in frontmatter; clauses are body content.

Storage: `30_Resources/EmptyOS/guideline/<slug>.md`, `tags: [guideline]`.

Section CRUD pattern mirrors `apps/journal/app.py` — read body, parse `## `
splits, mutate, write back via vault_create_note (re-indexes). Per-guideline
asyncio.Lock serializes section writes to dodge the read-modify-write race
documented in CLAUDE.md § Development Gotchas.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route

log = logging.getLogger("emptyos.guideline")

VALID_STATUSES = ("draft", "active", "deprecated")
SECTION_RE = re.compile(r"^##\s+(?!#)(.+)$")
CITE_RE = re.compile(r"\[\[kb:([a-z0-9][a-z0-9-]*)\]\]")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or f"x-{secrets.token_hex(3)}"


def _slug_of(path: str) -> str:
    return Path(path).stem


def _split_clauses(body: str) -> list[dict]:
    """Parse a body string into [{heading, slug, body}] in document order.

    Anything before the first `## ` heading is dropped (parent-level prose
    is not represented as a clause). `### ` and deeper are not clause
    boundaries — they belong to the current clause's body.
    """
    clauses: list[dict] = []
    current_heading: str | None = None
    current_body: list[str] = []
    for line in body.splitlines():
        m = SECTION_RE.match(line)
        if m:
            if current_heading is not None:
                clauses.append({
                    "heading": current_heading,
                    "slug": _slug(current_heading),
                    "body": "\n".join(current_body).strip(),
                })
            current_heading = m.group(1).strip()
            current_body = []
        elif current_heading is not None:
            current_body.append(line)
    if current_heading is not None:
        clauses.append({
            "heading": current_heading,
            "slug": _slug(current_heading),
            "body": "\n".join(current_body).strip(),
        })
    return clauses


def _serialize_clauses(clauses: list[dict]) -> str:
    parts = []
    for c in clauses:
        body = (c.get("body") or "").rstrip()
        parts.append(f"## {c['heading']}\n\n{body}".rstrip())
    return ("\n\n".join(parts) + "\n") if parts else ""


class GuidelineApp(BaseApp):
    SETTABLE_FIELDS = {"status", "category", "title"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._locks: dict[str, asyncio.Lock] = {}

    async def on_start(self):
        log.info("guideline started")

    def _gid_lock(self, gid: str) -> asyncio.Lock:
        self._locks.setdefault(gid, asyncio.Lock())
        return self._locks[gid]

    # ── Core queries ──────────────────────────────────────────────

    def _all(self) -> list[dict]:
        return self.vault_query(tags=["guideline"]) or []

    def _find(self, gid: str) -> dict | None:
        for n in self._all():
            if _slug_of(n.get("path", "")) == gid:
                return n
        return None

    def _summarize(self, n: dict) -> dict:
        props = n.get("properties", {}) or {}
        path = n.get("path", "")
        gid = _slug_of(path)
        return {
            "id": gid,
            "file": gid,
            "path": path,
            "title": str(props.get("title") or n.get("name", "")),
            "category": str(props.get("category") or "general"),
            "status": str(props.get("status") or "active"),
            "clause_count": len(self.vault_sections(path)) if path else 0,
            "created": str(props.get("created") or ""),
            "updated": str(props.get("updated") or ""),
        }

    # ── Read-side ─────────────────────────────────────────────────

    async def list_all(self) -> list[dict]:
        """Boards-shape — one row per parent guideline."""
        return sorted(
            [self._summarize(n) for n in self._all()],
            key=lambda r: (r["category"], r["title"].lower()),
        )

    async def list_categories(self) -> list[dict]:
        by_cat: dict[str, dict[str, int]] = {}
        for n in self._all():
            s = self._summarize(n)
            cat = s["category"]
            row = by_cat.setdefault(cat, {"total": 0, "active": 0, "draft": 0, "deprecated": 0})
            row["total"] += 1
            if s["status"] in row:
                row[s["status"]] += 1
        return [{"category": c, **counts} for c, counts in sorted(by_cat.items())]

    async def get(self, gid: str) -> dict:
        n = self._find(gid)
        if not n:
            return {"error": "not found", "id": gid}
        s = self._summarize(n)
        body = self.vault_read_body(n.get("path", "")) or ""
        clauses = _split_clauses(body)
        # Resolve [[kb:slug]] cites across all clause bodies in one pass so
        # the kb index is hit at most 3× per detail load, not per-clause.
        if clauses:
            resolver = self._build_kb_resolver()
            for c in clauses:
                c["cites"] = self._resolve_cites_in(c.get("body", ""), resolver)
        s["clauses"] = clauses
        return s

    def _build_kb_resolver(self) -> dict[str, dict]:
        """Return slug → {slug, kind, title, link} for every reachable kb entry.

        Single corpus under tag `kb`; we route by `kind` instead of separate tags.
        Doc landing pages live under /kb/pages/docs.html#<slug>; everything else
        renders in the unified detail view at /kb/#<slug>.
        """
        out: dict[str, dict] = {}
        for n in self.vault_query(tags=["kb"]) or []:
            slug = _slug_of(n.get("path", ""))
            if not slug:
                continue
            props = n.get("properties", {}) or {}
            kind = str(props.get("kind") or "note")
            link = f"/kb/pages/docs.html#{slug}" if kind == "doc" else f"/kb/#{slug}"
            out[slug] = {
                "slug": slug,
                "kind": kind,
                "title": str(props.get("title") or n.get("name", "") or slug),
                "link": link,
            }
        return out

    def _resolve_cites_in(self, text: str, resolver: dict[str, dict]) -> list[dict]:
        """Find every `[[kb:slug]]` marker in *text*, return resolved metadata.

        Each result has the standard cite shape plus `resolved: bool` so the UI
        can show broken cites distinctly from working ones (chip + warning).
        Unique by slug — duplicate markers in the same clause coalesce.
        """
        slugs: list[str] = []
        seen: set[str] = set()
        for m in CITE_RE.finditer(text or ""):
            slug = m.group(1)
            if slug not in seen:
                seen.add(slug)
                slugs.append(slug)
        out = []
        for slug in slugs:
            meta = resolver.get(slug)
            if meta:
                out.append({**meta, "resolved": True})
            else:
                out.append({
                    "slug": slug, "kind": "unknown", "title": slug,
                    "link": f"/kb/#{slug}", "resolved": False,
                })
        return out

    # ── Write-side: parent ────────────────────────────────────────

    async def add(self, title: str, category: str = "") -> dict:
        title = (title or "").strip()
        if not title:
            return {"error": "title required"}
        category = (category or self.app_config("default_category", "general")).strip() or "general"

        gid = _slug(title)
        existing = {_slug_of(n.get("path", "")) for n in self._all()}
        if gid in existing:
            gid = f"{gid}-{secrets.token_hex(2)}"

        now = datetime.now().date().isoformat()
        vault_dir = self.vault_config("path", "30_Resources/EmptyOS/guideline")
        path = f"{vault_dir.rstrip('/')}/{gid}.md"
        self.vault_create_note(
            path,
            {
                "title": title,
                "category": category,
                "status": "active",
                "tags": ["guideline"],
                "created": now,
                "updated": now,
            },
            "",
        )
        await self.emit("guideline:added", {"id": gid, "category": category})
        return {"ok": True, "id": gid, "path": path}

    async def set_field(self, id: str, field: str, value) -> dict:
        if field not in self.SETTABLE_FIELDS:
            return {"error": f"field '{field}' not settable"}
        n = self._find(id)
        if not n:
            return {"error": "not found"}
        if field == "status" and value not in VALID_STATUSES:
            return {"error": f"invalid status '{value}'"}
        self.vault_update(n["path"], {field: value, "updated": datetime.now().date().isoformat()})
        if field == "status" and value == "deprecated":
            await self.emit("guideline:deprecated", {"id": id})
        else:
            await self.emit("guideline:updated", {"id": id, "field": field})
        return {"ok": True}

    async def deprecate(self, gid: str) -> dict:
        return await self.set_field(gid, "status", "deprecated")

    async def remove(self, gid: str) -> dict:
        """Hard-delete a parent guideline (.md file gone, no recovery).

        Mirrors `apps/sim/app.py:api_delete_run` — unlink + pop vault_index
        cache. Use deprecate() for soft-delete; this is for real removal
        (typos, test cleanup, retiring a whole document).
        """
        n = self._find(gid)
        if not n:
            return {"error": "not found", "id": gid}
        async with self._gid_lock(gid):
            vault = self.kernel.config.notes_path
            if not vault:
                return {"error": "vault not configured"}
            rel_path = n["path"]
            abs_path = Path(vault) / rel_path
            if abs_path.exists():
                abs_path.unlink()
            vi = self.kernel.services.get_optional("vault_index")
            if vi and hasattr(vi, "_files"):
                vi._files.pop(rel_path, None)
            self._locks.pop(gid, None)
        await self.emit("guideline:removed", {"id": gid})
        return {"ok": True, "id": gid}

    # ── Write-side: clauses ───────────────────────────────────────

    async def add_clause(self, gid: str, heading: str, body: str = "") -> dict:
        heading = (heading or "").strip().lstrip("#").strip()
        if not heading:
            return {"error": "heading required"}
        n = self._find(gid)
        if not n:
            return {"error": "not found"}
        async with self._gid_lock(gid):
            existing_body = self.vault_read_body(n["path"]) or ""
            clauses = _split_clauses(existing_body)
            new_slug = _slug(heading)
            if any(c["slug"] == new_slug for c in clauses):
                return {"error": f"clause '{new_slug}' already exists"}
            clauses.append({"heading": heading, "slug": new_slug, "body": (body or "").strip()})
            self._rewrite(n, clauses)
        await self.emit("guideline:clause_added", {"id": gid, "slug": new_slug})
        return {"ok": True, "id": gid, "slug": new_slug}

    async def update_clause(self, gid: str, slug: str, body: str) -> dict:
        n = self._find(gid)
        if not n:
            return {"error": "not found"}
        async with self._gid_lock(gid):
            clauses = _split_clauses(self.vault_read_body(n["path"]) or "")
            target = next((c for c in clauses if c["slug"] == slug), None)
            if not target:
                return {"error": f"clause '{slug}' not found"}
            target["body"] = (body or "").strip()
            self._rewrite(n, clauses)
        await self.emit("guideline:clause_updated", {"id": gid, "slug": slug})
        return {"ok": True}

    async def delete_clause(self, gid: str, slug: str) -> dict:
        n = self._find(gid)
        if not n:
            return {"error": "not found"}
        async with self._gid_lock(gid):
            clauses = _split_clauses(self.vault_read_body(n["path"]) or "")
            new_clauses = [c for c in clauses if c["slug"] != slug]
            if len(new_clauses) == len(clauses):
                return {"error": f"clause '{slug}' not found"}
            self._rewrite(n, new_clauses)
        await self.emit("guideline:clause_removed", {"id": gid, "slug": slug})
        return {"ok": True}

    def _rewrite(self, n: dict, clauses: list[dict]) -> None:
        """Re-serialize parent with new clauses; preserves frontmatter, bumps updated."""
        props = self.vault_get_properties(n["path"]) or (n.get("properties") or {})
        # Frontmatter normalization — vault_get_properties may strip `tags`,
        # always re-attach the guideline tag.
        tags = props.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if "guideline" not in tags:
            tags = list(tags) + ["guideline"]
        new_props = {
            "title": props.get("title", ""),
            "category": props.get("category", "general"),
            "status": props.get("status", "active"),
            "tags": tags,
            "created": props.get("created", datetime.now().date().isoformat()),
            "updated": datetime.now().date().isoformat(),
        }
        self.vault_create_note(n["path"], new_props, _serialize_clauses(clauses))

    # ── Web API ───────────────────────────────────────────────────

    @web_route("GET", "/api/items")
    async def api_list(self, request):
        category = request.query_params.get("category", "")
        status = request.query_params.get("status", "")
        items = await self.list_all()
        if category:
            items = [i for i in items if i["category"] == category]
        if status:
            items = [i for i in items if i["status"] == status]
        return {"items": items, "count": len(items)}

    @web_route("GET", "/api/categories")
    async def api_categories(self, request):
        return {"categories": await self.list_categories()}

    @web_route("GET", "/api/items/{gid}")
    async def api_get(self, request):
        return await self.get(request.path_params.get("gid", ""))

    @web_route("POST", "/api/items")
    async def api_add(self, request):
        body = await request.json()
        return await self.add(title=body.get("title", ""), category=body.get("category", ""))

    @web_route("POST", "/api/items/{gid}/field")
    async def api_set_field(self, request):
        gid = request.path_params.get("gid", "")
        body = await request.json()
        return await self.set_field(gid, body.get("field", ""), body.get("value"))

    @web_route("POST", "/api/items/{gid}/deprecate")
    async def api_deprecate(self, request):
        return await self.deprecate(request.path_params.get("gid", ""))

    @web_route("DELETE", "/api/items/{gid}")
    async def api_remove(self, request):
        return await self.remove(request.path_params.get("gid", ""))

    @web_route("POST", "/api/items/{gid}/clauses")
    async def api_add_clause(self, request):
        gid = request.path_params.get("gid", "")
        body = await request.json()
        return await self.add_clause(gid, body.get("heading", ""), body.get("body", ""))

    @web_route("POST", "/api/items/{gid}/clauses/{slug}")
    async def api_update_clause(self, request):
        gid = request.path_params.get("gid", "")
        slug = request.path_params.get("slug", "")
        body = await request.json()
        return await self.update_clause(gid, slug, body.get("body", ""))

    @web_route("DELETE", "/api/items/{gid}/clauses/{slug}")
    async def api_delete_clause(self, request):
        gid = request.path_params.get("gid", "")
        slug = request.path_params.get("slug", "")
        return await self.delete_clause(gid, slug)

    # ── Hub panel — random clause from random active guideline ────

    async def panel_daily(self) -> dict | None:
        if not self.app_config("hub_panel_enabled", True):
            return None
        items: list[tuple[dict, str, str]] = []
        for n in self._all():
            props = n.get("properties", {}) or {}
            if (props.get("status") or "active") != "active":
                continue
            path = n.get("path", "")
            for heading in self.vault_sections(path):
                items.append((n, heading, path))
        if not items:
            return None
        pick = items[hash(datetime.now().date().isoformat()) % len(items)]
        n, heading, path = pick
        props = n.get("properties", {}) or {}
        body = self.vault_read_section(path, heading) or ""
        excerpt = body[:140] + ("…" if len(body) > 140 else "")
        gid = _slug_of(path)
        slug = _slug(heading)
        cites = self._resolve_cites_in(body, self._build_kb_resolver()) if CITE_RE.search(body) else []
        fields = [{"label": "excerpt", "value": excerpt}]
        if cites:
            fields.append({
                "label": "cites",
                "value": ", ".join(c["title"] for c in cites[:3]),
            })
        return {
            "title": heading,
            "subtitle": f"{props.get('title','')} · {props.get('category','')}",
            "fields": fields,
            "link": f"/guideline/#{gid}/{slug}",
        }

    # ── Voice intents ─────────────────────────────────────────────

    async def voice_show(self, category: str = "") -> dict:
        items = await self.list_all()
        if category:
            items = [i for i in items if i["category"].lower() == category.lower()]
        items = [i for i in items if i["status"] == "active"]
        if not items:
            return {"say": f"No active guidelines in {category}." if category else "No active guidelines."}
        return {
            "say": (
                f"{len(items)} guideline{'s' if len(items) != 1 else ''} in {category}."
                if category else f"{len(items)} active guidelines."
            ),
            "card": {
                "renderer": "task-list",
                "data": [{"text": f"{i['title']} ({i['clause_count']} clauses)", "tag": i["category"]} for i in items[:10]],
            },
        }

    async def voice_random(self) -> dict:
        panel = await self.panel_daily()
        if not panel:
            return {"say": "No active guidelines yet."}
        excerpt = (panel.get("fields") or [{}])[0].get("value", "")
        return {"say": f"{panel['title']}. {excerpt}"}

    # ── CLI ───────────────────────────────────────────────────────

    @cli_command("guideline")
    async def cli_guideline(self, action: str = "list", arg: str = "", arg2: str = ""):
        """eos guideline {list|categories|show <id>|add <title>|deprecate <id>|clause-add <id> <heading>}"""
        if action == "categories":
            for c in await self.list_categories():
                self.print_rich(
                    f"  [bold]{c['category']}[/bold]  {c['total']} (active {c['active']}, "
                    f"draft {c['draft']}, deprecated {c['deprecated']})"
                )
        elif action == "show":
            data = await self.get(arg)
            if "error" in data:
                self.print_rich(f"[red]{data['error']}[/red]")
                return
            self.print_rich(f"[bold]{data['title']}[/bold]  [dim]({data['category']} · {data['status']})[/dim]")
            for c in data.get("clauses", []):
                self.print_rich(f"\n  [bold]## {c['heading']}[/bold]")
                self.print_rich(f"  {c['body']}" if c["body"] else "  [dim](empty)[/dim]")
        elif action == "add":
            if not arg:
                self.print_rich("[red]title required[/red]")
                return
            r = await self.add(arg)
            self.print_rich(f"Added: {r.get('id')}" if r.get("ok") else f"[red]{r.get('error')}[/red]")
        elif action == "clause-add":
            if not arg or not arg2:
                self.print_rich("[red]usage: guideline clause-add <id> <heading>[/red]")
                return
            r = await self.add_clause(arg, arg2)
            self.print_rich(f"Clause added: {r.get('slug')}" if r.get("ok") else f"[red]{r.get('error')}[/red]")
        elif action == "deprecate":
            r = await self.deprecate(arg)
            self.print_rich("Deprecated" if r.get("ok") else f"[red]{r.get('error')}[/red]")
        else:
            items = await self.list_all()
            for i in items:
                dim = " [dim](deprecated)[/dim]" if i["status"] == "deprecated" else ""
                self.print_rich(f"  [{i['category']:14}] {i['title']}  [dim]{i['clause_count']} clauses[/dim]{dim}")
            self.print_rich(f"\n[dim]{len(items)} guideline(s)[/dim]")
