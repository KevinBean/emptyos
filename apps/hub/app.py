"""Hub — generic home dashboard.

Aggregates [[contributes.hub.panel]] from every installed app and renders them
in priority order. Zero hard app dependencies — works on a fresh clone with
only core apps, gracefully gains panels as more apps are installed.

The aggregator pattern (resolve_panels) is portable from the prior personal
dashboard but stripped of all life-domain logic (wellness, AI narrative, slot
framework). Personal richer variants live as separate apps that subscribe to
the same contribution slot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route


class HubApp(BaseApp):

    # ── Panel aggregator ──

    async def resolve_panels(self, *, include_lazy: bool = False) -> list[dict]:
        """Gather every [[contributes.hub.panel]], call its method, return
        a list of {id, title, renderer, group, priority, source, data, lazy} items.

        Fail-soft per panel: a contributor that raises drops out of the list
        (logged to syslog) rather than breaking the whole page.

        Lazy panels are emitted as placeholders unless include_lazy=True.
        """
        contributions = self.kernel.apps.get_contributions("hub", "panel")
        if not contributions:
            return []

        def _placeholder(contrib: dict) -> dict:
            app_id = contrib.get("_app_id")
            method = contrib.get("method")
            return {
                "id": contrib.get("id") or f"{app_id}:{method}",
                "title": contrib.get("title") or "",
                "renderer": contrib.get("renderer") or "plain-list",
                "group": contrib.get("group") or "",
                "priority": int(contrib.get("priority", 100) or 100),
                "source": app_id,
                "data": None,
                "lazy": True,
            }

        async def _call_one(contrib: dict) -> dict | None:
            app_id = contrib.get("_app_id")
            method = contrib.get("method")
            if not app_id or not method:
                return None
            try:
                data = await self.call_app(app_id, method)
            except Exception as e:
                self.kernel.syslog.warn(
                    "hub", f"panel '{contrib.get('id')}' ({app_id}.{method}) failed: {e}",
                )
                return None
            if data is None:
                return None
            cap = contrib.get("limit")
            if isinstance(data, list) and isinstance(cap, int) and cap > 0:
                data = data[:cap]
            return {
                "id": contrib.get("id") or f"{app_id}:{method}",
                "title": contrib.get("title") or "",
                "renderer": contrib.get("renderer") or "plain-list",
                "group": contrib.get("group") or "",
                "priority": int(contrib.get("priority", 100) or 100),
                "source": app_id,
                "data": data,
                "lazy": False,
            }

        eager: list[dict] = []
        lazy_placeholders: list[dict] = []
        eager_contribs: list[dict] = []
        for c in contributions:
            if c.get("lazy") and not include_lazy:
                lazy_placeholders.append(_placeholder(c))
            else:
                eager_contribs.append(c)

        results = await asyncio.gather(*[_call_one(c) for c in eager_contribs], return_exceptions=False)
        eager = [r for r in results if r is not None]

        panels = eager + lazy_placeholders
        panels.sort(key=lambda p: (p["priority"], p["id"]))
        return panels

    # ── HTTP API ──

    @web_route("GET", "/api/panels")
    async def api_panels(self, request):
        """All panels in layout order, grouped where applicable."""
        panels = await self.resolve_panels()
        blocks: list[dict] = []
        seen_groups: dict[str, dict] = {}
        for p in panels:
            g = p["group"]
            if g and g in seen_groups:
                # Defense-in-depth: a panel can only join a group if its
                # renderer matches. Mixed-renderer groups silently render the
                # wrong shape (e.g. stat-tile data flattened into chip text).
                if p["renderer"] != seen_groups[g]["renderer"]:
                    self.log(
                        f"hub: panel '{p['id']}' renderer '{p['renderer']}' "
                        f"does not match group '{g}' renderer "
                        f"'{seen_groups[g]['renderer']}' — dropping",
                        level="warn",
                    )
                    continue
                seen_groups[g]["items"].append(p)
                continue
            block = {
                "kind": "group" if g else "panel",
                "id": g or p["id"],
                "title": p["title"],
                "renderer": p["renderer"],
                "priority": p["priority"],
                "items": [p],
            }
            blocks.append(block)
            if g:
                seen_groups[g] = block
        await self.emit("hub:refreshed", {"blocks": len(blocks)})
        return {"blocks": blocks}

    @web_route("GET", "/api/panel/{panel_id}")
    async def api_panel(self, request):
        """Refresh a single panel by id. Forces lazy panels to execute."""
        panel_id = request.path_params.get("panel_id", "")
        panels = await self.resolve_panels(include_lazy=True)
        for p in panels:
            if p["id"] == panel_id:
                return p
        return {"error": "not found", "id": panel_id}

    @web_route("GET", "/api/panels/all")
    async def api_panels_all(self, request):
        """Like /api/panels but runs lazy contributors too. Used for debug."""
        panels = await self.resolve_panels(include_lazy=True)
        return {"panels": panels}

    @web_route("GET", "/debug/panels")
    async def debug_panels(self, request):
        from fastapi.responses import HTMLResponse
        debug_file = Path(self.manifest.path) / "pages" / "debug.html"
        if not debug_file.exists():
            return HTMLResponse(
                "<h1>debug.html missing</h1><p>See /hub/api/panels/all for raw data.</p>",
                status_code=404,
            )
        return HTMLResponse(debug_file.read_text(encoding="utf-8"))

    # ── Hub's own panels ──

    async def panel_welcome(self) -> dict | None:
        """Welcome card — only shown when there are no other contributing apps.

        We can't know that at panel-method time, so we always return it; the
        chips launcher below renders the actual app list. The welcome card
        becomes self-evidently extra context once other panels are present.
        """
        if not self.app_config("show_welcome", True):
            return None
        return {
            "label": "EmptyOS",
            "text": "A mind companion. Capture a thought, write a note, or pick an app below.",
            "url": "/quick-action/",
            "button_label": "Capture",
        }

    async def panel_launcher(self) -> list[dict] | None:
        """Grid launcher — every loaded app with name + description + link.

        Hub itself and any app with no web prefix are skipped. Sorted
        alphabetically by display name so the grid is stable across loads.
        """
        cards: list[dict] = []
        for app_id, app in self.kernel.apps.apps.items():
            if app_id == "hub":
                continue
            prefix = getattr(app.manifest, "web_prefix", None)
            if not prefix:
                continue
            href = prefix + "/" if not prefix.endswith("/") else prefix
            cards.append({
                "id": app_id,
                "title": app.manifest.name or app_id,
                "href": href,
                "description": (app.manifest.description or "").strip(),
            })
        cards.sort(key=lambda c: (c["title"] or c["id"]).lower())
        return cards or None

    # ── CLI ──

    @cli_command("hub")
    def cmd_hub(self):
        """Print panel summary."""
        import asyncio
        panels = asyncio.run(self.resolve_panels())
        if not panels:
            print("No panels contributed yet. Install more apps to populate the dashboard.")
            return
        print(f"{len(panels)} panels:")
        for p in panels:
            print(f"  [{p['priority']:>4}] {p['source']:>20}  {p['id']}  ({p['renderer']})")
