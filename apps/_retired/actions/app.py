"""Actions — run named templates over selected vault items.

Apps register templates via:
    [[contributes.actions.template]]
    id = "summarize-blocks"
    method = "action_summarize_blocks"   # callable on the contributing app
    label = "Summarize selected"
    icon = "📝"
    kind = "llm"                         # "plain" or "llm"
    description = "Render N blocks into a single summary"
    args_schema = "[{\"key\":\"style\",\"type\":\"select\",\"options\":[\"bullet\",\"paragraph\"],\"default\":\"bullet\"}]"

For LLM templates, the action method receives `items` + the user-supplied
args; it formats whatever prompt it needs and calls `self.think()` itself.
For plain templates, the method just runs whatever side effect it owns.

Workflows are markdown notes in `30_Resources/EmptyOS/workflows/` tagged
`workflow`, with frontmatter `steps_json: "[{...}, ...]"` (one JSON-encoded
array because vault frontmatter is flat-only).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route


class ActionsApp(BaseApp):
    async def setup(self):
        await super().setup()

    # ---------- template registry ----------

    def _list_templates(self) -> list[dict]:
        """Aggregate every [[contributes.actions.template]] across installed apps."""
        entries = self.kernel.apps.get_contributions("actions", "template")
        out = []
        for e in entries:
            schema_raw = e.get("args_schema") or "[]"
            try:
                schema = json.loads(schema_raw) if isinstance(schema_raw, str) else schema_raw
            except Exception:
                schema = []
            out.append({
                "id": e.get("id") or "",
                "app": e.get("_app_id") or "",
                "method": e.get("method") or "",
                "label": e.get("label") or e.get("id") or "",
                "icon": e.get("icon") or "",
                "kind": (e.get("kind") or "plain").lower(),
                "description": e.get("description") or "",
                "is_risky": bool(e.get("is_risky")),
                "args_schema": schema if isinstance(schema, list) else [],
            })
        return out

    def _find_template(self, template_id: str) -> dict | None:
        for t in self._list_templates():
            if t["id"] == template_id:
                return t
        return None

    # ---------- run ----------

    async def run_template(self, template_id: str, items: list, args: dict | None = None) -> dict:
        tpl = self._find_template(template_id)
        if not tpl:
            return {"error": f"template '{template_id}' not registered"}
        args = args or {}
        await self.emit("actions:run", {
            "template": template_id, "app": tpl["app"], "item_count": len(items or []),
        })
        try:
            result = await self.call_app(tpl["app"], tpl["method"], items=items or [], **args)
        except Exception as e:
            await self.emit("actions:failed", {"template": template_id, "error": str(e)})
            return {"error": f"{type(e).__name__}: {e}"}
        await self.emit("actions:completed", {
            "template": template_id, "app": tpl["app"], "item_count": len(items or []),
        })
        return {
            "ok": True,
            "template": template_id,
            "result": result,
            "provenance": self.last_provenance() if tpl.get("kind") == "llm" else {"mode": "plain"},
        }

    # ---------- workflows ----------

    def _workflow_path(self, wid: str) -> str:
        return f"{self.vault_config('workflows_dir', '30_Resources/EmptyOS/workflows')}/{wid}.md"

    def list_workflows(self) -> dict:
        rows = self.vault_query(tags=["workflow"]) or []
        out = []
        for n in rows:
            props = n.get("properties", {}) or {}
            slug = Path(n.get("path", "")).stem
            steps = self._parse_steps(props)
            out.append({
                "id": slug,
                "title": props.get("title") or slug.replace("-", " "),
                "step_count": len(steps),
                "path": n.get("path", ""),
            })
        out.sort(key=lambda r: r["id"])
        return {"workflows": out, "count": len(out)}

    def _parse_steps(self, props: dict) -> list[dict]:
        steps = self.vault_decode_json(props.get("steps_json"), default=[])
        return steps if isinstance(steps, list) else []

    def _encode_steps(self, steps: list[dict]) -> str:
        return self.vault_encode_json(steps)

    async def create_workflow(self, title: str, steps: list[dict]) -> dict:
        title = (title or "").strip()
        if not title:
            return {"error": "title required"}
        from re import sub as _sub
        wid = _sub(r"[^\w\-]+", "-", title.lower()).strip("-") or "workflow"
        rel = self._workflow_path(wid)
        if (self.vault_root / rel).exists():
            return {"error": "workflow already exists", "id": wid}
        fm = {
            "title": title,
            "tags": ["workflow"],
            "steps_json": self._encode_steps(steps),
            "created": datetime.now().date().isoformat(),
        }
        self.vault_create_note(rel, fm, f"# {title}\n\nEdit `steps_json` in this note's frontmatter, or replace via /actions UI.\n")
        return {"ok": True, "id": wid}

    async def run_workflow(self, wid: str, items: list) -> dict:
        rel = self._workflow_path(wid)
        if not (self.vault_root / rel).exists():
            return {"error": f"workflow '{wid}' not found"}
        props = self.vault_get_properties(rel) or {}
        steps = self._parse_steps(props)
        if not steps:
            return {"error": "workflow has no steps"}
        results = []
        prev_output = None
        for i, step in enumerate(steps):
            tpl_id = step.get("template") or step.get("action")
            args = dict(step.get("args") or {})
            if prev_output is not None and "prev_output" not in args:
                args["prev_output"] = prev_output
            step_items = items if step.get("inherit_items", True) else step.get("items", [])
            r = await self.run_template(tpl_id, step_items, args)
            results.append({"step": i, "template": tpl_id, "result": r})
            if "error" in r:
                await self.emit("actions:workflow_completed", {
                    "workflow": wid, "ok": False, "failed_step": i,
                })
                return {"ok": False, "failed_step": i, "results": results}
            prev_output = r.get("result")
        await self.emit("actions:workflow_completed", {
            "workflow": wid, "ok": True, "step_count": len(results),
        })
        return {"ok": True, "workflow": wid, "results": results, "final": prev_output}

    # ---------- API ----------

    @web_route("GET", "/api/templates")
    async def api_templates(self, request):
        return {"templates": self._list_templates()}

    @web_route("POST", "/api/run")
    async def api_run(self, request):
        body = await request.json()
        return await self.run_template(
            body.get("template_id", ""),
            body.get("items", []),
            body.get("args", {}),
        )

    @web_route("GET", "/api/workflows")
    async def api_workflows(self, request):
        return self.list_workflows()

    @web_route("POST", "/api/workflows")
    async def api_workflows_create(self, request):
        body = await request.json()
        return await self.create_workflow(body.get("title", ""), body.get("steps", []))

    @web_route("POST", "/api/workflows/{wid}/run")
    async def api_workflow_run(self, request):
        body = await request.json()
        wid = request.path_params.get("wid", "")
        return await self.run_workflow(wid, body.get("items", []))

    # ---------- CLI ----------

    @cli_command("actions", help="Run action templates and workflows over selected items")
    async def cli_actions(
        self,
        action: str = "list",
        template: str = "",
        workflow: str = "",
        on: str = "",
        args: str = "",
    ):
        """eos actions {list|run|run-workflow}

        Examples:
            eos actions list
            eos actions run --template kb.summarize-blocks --on '["block-a","block-b"]' --args '{"style":"bullet"}'
            eos actions run-workflow --workflow daily-roundup --on '[...]'
        """
        if action == "list":
            tpls = self._list_templates()
            if not tpls:
                self.print_rich("[dim]No action templates registered.[/dim]")
                return
            for t in tpls:
                tag = t.get("icon") or ""
                self.print_rich(
                    f"  {tag} [bold]{t['app']}.{t['id']}[/bold]  [{t['kind']}]  {t.get('description','')}"
                )
            return
        if action == "run":
            if not template:
                self.print_rich("[red]--template <id> required[/red]")
                return
            items = json.loads(on) if on else []
            args_obj = json.loads(args) if args else {}
            r = await self.run_template(template, items, args_obj)
            self.print_rich(json.dumps(r, indent=2, default=str))
            return
        if action == "run-workflow":
            if not workflow:
                self.print_rich("[red]--workflow <id> required[/red]")
                return
            items = json.loads(on) if on else []
            r = await self.run_workflow(workflow, items)
            self.print_rich(json.dumps(r, indent=2, default=str))
            return
        self.print_rich("[dim]Usage: eos actions {list|run|run-workflow} [--template id] [--on json] [--args json][/dim]")
