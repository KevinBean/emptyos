"""Product Tour — aggregates [[contributes.tour.step]] across all apps.

The tour is not a static onboarding overlay. It walks the user through real
pages: each step declares a route and a CSS selector to spotlight. The
orchestrator (eos-tour.js) reads /tour/api/steps, navigates via EOS.navigate,
and uses EOS_UI.spotlight() to highlight the target element.

Steps that need a capability (e.g. `requires = ["think"]`) are auto-rewritten
to point at /system?capability=<missing> when no provider is available, so the
tour never lands on a button that can't fire.
"""

from __future__ import annotations

import json
from pathlib import Path

from emptyos.sdk import BaseApp, web_route


class TourApp(BaseApp):
    """Aggregates tour-step contributions and tracks completion state."""

    async def setup(self):
        self._state_dir = Path(self.kernel.config.path).parent / "data" / "apps" / "tour"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._state_dir / "state.json"

    def _read_state(self) -> dict:
        if not self._state_path.exists():
            return {"dismissed": False, "completed_at": None, "last_step": None}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"dismissed": False, "completed_at": None, "last_step": None}

    def _write_state(self, state: dict) -> None:
        self._state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    async def _missing_capabilities(self, requires: list[str]) -> list[str]:
        """Return the subset of `requires` for which no provider is available."""
        if not requires:
            return []
        snapshot = await self.kernel.capabilities.status()
        missing = []
        for cap in requires:
            rows = snapshot.get(cap) or []
            if not any(r.get("available") for r in rows):
                missing.append(cap)
        return missing

    @web_route("GET", "/api/steps")
    async def api_steps(self, request):
        """Return the flattened, capability-filtered tour step list.

        Each step is rewritten to point at /system when a required capability
        is missing, so users always land somewhere actionable.
        """
        entries = self.kernel.apps.get_contributions("tour", "step")
        steps = []
        for e in entries:
            requires = e.get("requires") or []
            missing = await self._missing_capabilities(requires) if requires else []
            step = {
                "id": e.get("id") or "",
                "group": e.get("group") or "core",
                "priority": int(e.get("priority") or 100),
                "route": e.get("route") or "/",
                "spotlight": e.get("spotlight") or "",
                "title": e.get("title") or "",
                "body": e.get("body") or "",
                "requires": requires,
                "missing": missing,
                "_app_id": e.get("_app_id"),
            }
            if missing:
                first = missing[0]
                step["route"] = f"/system?capability={first}"
                step["spotlight"] = f"#cap-{first}"
                step["body"] = (
                    f"This step needs the <b>{first}</b> capability, but no provider "
                    f"is set up yet. Get it running here, then continue the tour."
                )
            steps.append(step)
        steps.sort(key=lambda s: (s["priority"], s["id"]))
        return {"steps": steps, "state": self._read_state()}

    @web_route("POST", "/api/dismiss")
    async def api_dismiss(self, request):
        """Mark the tour as dismissed/completed so the first-run banner stops."""
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        import time

        state = self._read_state()
        state["dismissed"] = True
        if body.get("completed"):
            state["completed_at"] = time.time()
        self._write_state(state)
        if body.get("completed"):
            await self.emit("tour:completed", {"last_step": state.get("last_step")})
        return {"ok": True, "state": state}

    @web_route("POST", "/api/state")
    async def api_state_set(self, request):
        """Persist last-step (for resume across reloads — UI also uses localStorage)."""
        body = await request.json()
        state = self._read_state()
        if "last_step" in body:
            state["last_step"] = body["last_step"]
        self._write_state(state)
        if "last_step" in body:
            await self.emit("tour:step_advanced", {"step": body["last_step"]})
        return {"ok": True, "state": state}

    @web_route("GET", "/debug/steps")
    async def debug_steps(self, request):
        """Dev introspection — raw step contributions + their resolved form."""
        from starlette.responses import HTMLResponse

        steps_resp = await self.api_steps(request)
        rows = []
        for s in steps_resp["steps"]:
            rows.append(
                f"<tr><td>{s['priority']}</td><td><code>{s['id']}</code></td>"
                f"<td>{s['_app_id']}</td><td><code>{s['route']}</code></td>"
                f"<td><code>{s['spotlight']}</code></td>"
                f"<td>{', '.join(s['requires']) or '—'}</td>"
                f"<td>{', '.join(s['missing']) or '—'}</td></tr>"
            )
        html = (
            "<!doctype html><meta charset=utf-8><title>Tour debug</title>"
            "<link rel=stylesheet href=/static/theme.css>"
            "<style>body{font-family:system-ui;padding:24px;max-width:1100px;margin:auto}"
            "table{width:100%;border-collapse:collapse;font-size:13px}"
            "th,td{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}"
            "th{color:var(--text-muted);font-weight:600;font-size:11px;text-transform:uppercase}"
            "code{font-family:var(--mono,Consolas);font-size:12px}</style>"
            f"<h1>Tour steps ({len(steps_resp['steps'])})</h1>"
            f"<p style='color:var(--text-muted)'>State: <code>{json.dumps(steps_resp['state'])}</code></p>"
            "<table><thead><tr><th>Pri</th><th>id</th><th>app</th><th>route</th><th>spotlight</th><th>requires</th><th>missing</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
        return HTMLResponse(html)
