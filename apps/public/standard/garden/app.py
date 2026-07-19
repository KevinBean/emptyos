"""Garden — programmatic SVG rendering layer over the wellbeing wheel.

Read-only overlay app. Garden CONSUMES from wheel / people / kb / projects;
nothing reads back. The 8-dimension wheel signals + entity `last_touched`
drive plant species + stage in 4 plots (V1). Five themes (sumi-e / appleton /
scroll / cottage / edo) live as data dicts in `themes.py`; each owns its
visual identity end-to-end (palette + filters + per-species grammars +
chrome).

Architecture (data flow strictly one-way):

  wheel.collect_signals / vault_query / call_app
       │
       ▼
  _plot_sources.py     →   data/apps/garden/state.json   (6h tick)
       │
       ▼
  _render.py           →   /garden/api/plot/<slug>.svg
       │                   /hub/* via panel_mini → garden-mini renderer
       ▼
  pages/index.html     →   click plant → EOS_UI.timeline4D(entity_path)

`git rm -rf apps/garden/ data/apps/garden/` removes every trace.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from emptyos.sdk import BaseApp, scheduled, web_route
from fastapi.responses import HTMLResponse, JSONResponse

from . import _plot_sources, _render, themes

log = logging.getLogger("emptyos.garden")


class GardenApp(BaseApp):
    async def setup(self):
        await super().setup()
        self._state_path: Path = self.data_dir / "state.json"
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self._tick()
        except Exception as e:
            log.warning("garden initial tick failed: %s", e)

    # ── Settings helpers ───────────────────────────────────────────
    def _theme(self) -> str:
        t = str(self.setting_or_config("garden.theme", themes.DEFAULT_THEME) or themes.DEFAULT_THEME).lower()
        return t if t in themes.THEMES else themes.DEFAULT_THEME

    def _window_days(self) -> int:
        try:
            return max(1, min(365, int(self.setting_or_config("garden.window_days", 30) or 30)))
        except Exception:
            return 30

    # ── State recompute + cache ────────────────────────────────────
    async def _tick(self) -> dict:
        window = self._window_days()
        plots = await _plot_sources.read_all(self, window)
        total_plants = sum(len((p or {}).get("plants") or []) for p in plots.values())
        state = {
            "computed_at": int(time.time()),
            "window_days": window,
            "theme": self._theme(),
            "plots": plots,
            "total_plants": total_plants,
        }
        try:
            self._state_path.write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            log.debug("garden state cache write failed: %s", e)
        try:
            await self.emit("garden:tick", {"total_plants": total_plants})
        except Exception as e:
            log.debug("garden:tick emit failed: %s", e)
        return state

    async def _state(self, fresh: bool = False) -> dict:
        if fresh:
            return await self._tick()
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            return json.loads(raw)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return await self._tick()

    # ── Scheduled 6h tick (Animal-Crossing model — independent of visits) ──
    @scheduled("0 */6 * * *", id="garden:tick")
    async def scheduled_tick(self):
        try:
            await self._tick()
        except Exception as e:
            log.warning("garden scheduled tick failed: %s", e)

    # ── Web API ────────────────────────────────────────────────────
    @web_route("GET", "/api/state")
    async def api_state(self, request):
        fresh = (request.query_params.get("fresh") or "").lower() in ("1", "true", "yes")
        return await self._state(fresh=fresh)

    @web_route("POST", "/api/tick")
    async def api_tick(self, request):
        """Force a recompute. Cheap; useful after manual vault edits."""
        return await self._tick()

    @web_route("GET", "/api/themes")
    async def api_themes(self, request):
        return {
            "themes": [
                {"id": tid, "name": t.get("name", tid), "lineage": t.get("lineage", "")}
                for tid, t in themes.THEMES.items()
            ],
            "active": self._theme(),
            "default": themes.DEFAULT_THEME,
        }

    @web_route("GET", "/api/plot/{slug}.svg")
    async def api_plot_svg(self, request):
        slug = request.path_params.get("slug", "")
        if slug not in dict(_plot_sources.READERS):
            return JSONResponse(
                {"error": f"unknown plot: {slug}"}, status_code=404
            )
        theme = (request.query_params.get("theme") or "").lower()
        if theme not in themes.THEMES:
            theme = self._theme()
        state = await self._state()
        plot = (state.get("plots") or {}).get(slug) or {
            "slug": slug, "species": "grass", "plants": [],
        }
        svg = _render.render_plot(plot, theme)
        return HTMLResponse(content=svg, media_type="image/svg+xml")

    # ── Hub panel contribution ─────────────────────────────────────
    async def panel_mini(self) -> dict | None:
        try:
            state = await self._state()
        except Exception:
            return None
        plots = state.get("plots") or {}
        if not plots:
            return None
        theme = state.get("theme") or themes.DEFAULT_THEME
        return {
            "theme": theme,
            "svg": _render.render_mini(state, theme),
            "href": "/garden/",
            "total_plants": state.get("total_plants", 0),
        }
