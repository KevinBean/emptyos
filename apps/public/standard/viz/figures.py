"""viz — static figure export: an artifact becomes a KB/article figure.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the figure-export dark flag, the shape gate, the SVG+PNG write,
the ``used_in`` provenance stamp, and the ``/api/figure`` endpoint.

Why this exists: a viz artifact is `scene.html`, which the vault index cannot
see (md only) and which renders nowhere but this app — not in a KB note, not
in an article, not in RSS. Exporting a static figure into the consumer's
*existing* asset path is what lets a generated diagram be used where diagrams
are actually read.

**viz does not know where anyone's assets live, on purpose.** It asks the
target app for the path (`figure_asset_path`) and for the embed
(`attach_figure`). kb resolves `notes_dir` from its own config; publish
resolves `source_folder` per-site at runtime from settings. Hardcoding either
here would be a second copy of a path that moves — the coupling
`projects/viz_scene.py` already carries a "keep in sync if viz moves" comment
about. A target app that doesn't implement the verb simply can't receive a
figure, which is the honest outcome.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach (path helpers come from
generation.py via ``self``).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.html_artifact import extract_svg
from emptyos.sdk.utils import require_path_segment

from .shared import SHAPE_SUPPORTS_FIGURE

if TYPE_CHECKING:
    from .app import VizApp  # noqa: F401 — for type hints only


# ─── Bind to VizApp class as ────────────────────────────────
#   _figure_enabled        = _figures._figure_enabled
#   _shape_supports_figure = _figures._shape_supports_figure
#   _parse_figure_target   = _figures._parse_figure_target
#   _rasterize_figure      = _figures._rasterize_figure
#   export_figure          = _figures.export_figure
#   api_figure             = _figures.api_figure
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# Target apps that can receive a figure. The value is the kwargs key set the
# app's `figure_asset_path` expects, so a new consumer is one row plus two
# verbs on its own side.
FIGURE_TARGETS = {
    "kb": ("slug",),
    "publish": ("post", "concept"),
}


def _figure_enabled(self) -> bool:
    """True when static-figure export is on for this machine.

    Dark default (per project_feature_pipeline_flag_default_dark) — with the
    flag off `/api/figure` refuses and nothing else in viz changes, so saved
    artifacts stay byte-identical to the pre-feature output.

    Read through `setting_or_config`, not `app_config`, because the flag is
    declared in `[provides.settings]`: the panel writes it to the settings
    service under the schema key verbatim, and `app_config` reads a different
    store — so reading only the latter would ship a toggle that does nothing
    (.claude/rules/app-ui-patterns.md). `config_key=` keeps the plain
    `feature.figure-export.enabled` TOML form working, which is also the form
    `scripts/check_dark_flags.py` inventories.
    """
    return bool(self.setting_or_config(
        "viz.feature.figure-export.enabled", False,
        config_key="feature.figure-export.enabled",
    ))


def _shape_supports_figure(self, shape: str) -> bool:
    return shape in SHAPE_SUPPORTS_FIGURE


def _parse_figure_target(self, target: str) -> dict:
    """``kb:<slug>`` / ``publish:<post>/<concept>`` -> ``{app, kwargs, label}``.

    Every caller-supplied segment goes through ``require_path_segment``: the
    value becomes a filename and this endpoint is reachable over HTTP, so a
    `../` or a Windows reserved stem must be refused here rather than at the
    write.
    """
    raw = (target or "").strip()
    if ":" not in raw:
        return {"ok": False, "error": "target must be 'kb:<slug>' or 'publish:<post>/<concept>'"}
    app_id, rest = (p.strip() for p in raw.split(":", 1))
    app_id = app_id.lower()
    if app_id not in FIGURE_TARGETS:
        return {"ok": False, "error": f"unknown target app '{app_id}' (want {' | '.join(FIGURE_TARGETS)})"}
    keys = FIGURE_TARGETS[app_id]
    parts = [p for p in rest.split("/") if p]
    if len(parts) != len(keys):
        return {"ok": False, "error": f"target '{app_id}:' expects {'/'.join(f'<{k}>' for k in keys)}"}
    try:
        parts = [require_path_segment(p) for p in parts]
    except ValueError as exc:
        return {"ok": False, "error": f"invalid target segment: {exc}"}
    return {"ok": True, "app": app_id, "kwargs": dict(zip(keys, parts)),
            "label": f"{app_id}/{'-'.join(parts)}"}


async def export_figure(
    self, id: str, target: str, *, rasterize: bool = True, alt: str = "", embed: bool = True
) -> dict:
    """Export artifact `id` as a standalone SVG (+2x PNG) into `target`.

    Public verb — callable via `call_app`, so a consumer can pull a figure
    instead of viz needing to push to it.
    """
    if not self._figure_enabled():
        return {"ok": False, "error": "figure export is disabled"}

    rid = (id or "").strip()
    if not rid:
        return {"ok": False, "error": "id is required"}
    try:
        rid = require_path_segment(rid)
    except ValueError as exc:
        return {"ok": False, "error": f"invalid id: {exc}"}

    html_path = self._record_dir(rid) / "scene.html"
    if not html_path.exists():
        return {"ok": False, "error": f"no artifact with id '{rid}'"}

    fm = self.vault_get_properties(self._rel_record(rid)) or {}
    shape = str(fm.get("shape") or "")
    if not self._shape_supports_figure(shape):
        return {"ok": False, "error": (
            f"shape '{shape}' cannot be exported as a static figure "
            f"(have: {sorted(SHAPE_SUPPORTS_FIGURE)})"
        )}

    dest = self._parse_figure_target(target)
    if not dest.get("ok"):
        return dest

    # Ask the owning app where its assets live. A missing app / missing verb is
    # a refusal, not a guess at a path.
    try:
        svg_rel = await self.call_app(dest["app"], "figure_asset_path", **dest["kwargs"])
    except Exception as exc:
        return {"ok": False, "error": f"{dest['app']} cannot receive a figure: {exc}"}
    if not isinstance(svg_rel, str) or not svg_rel.strip():
        return {"ok": False, "error": f"{dest['app']} returned no asset path for this target"}
    svg_rel = svg_rel.strip()

    svg = extract_svg(html_path.read_text(encoding="utf-8"))
    if not svg:
        # extract_svg returns None for "no svg" AND for unbalanced tags. Both
        # would write a file that renders wrong, so refuse rather than ship it.
        return {"ok": False, "error": "no complete <svg> found in the artifact"}

    self.vault_write_at(svg_rel, svg)
    png_rel = await self._rasterize_figure(svg_rel) if rasterize else ""

    # Let the consumer place the embed in its own idiom (kb: a `![[…]]`
    # wikilink, which is what earns the vault-graph edge; publish: the
    # `![alt|637](name.png)` house line). Soft — the asset is written either way.
    embedded = False
    hint = ""
    if embed:
        try:
            res = await self.call_app(
                dest["app"], "attach_figure",
                asset=svg_rel, png=png_rel, viz_id=rid,
                alt=alt or str(fm.get("title") or ""), **dest["kwargs"],
            )
            # A consumer may accept the asset but decline to place it —
            # publish hands back a body line instead, because placement +
            # alt text are editorial. Report what actually happened.
            if isinstance(res, dict) and res.get("ok"):
                embedded = bool(res.get("embedded", True))
                hint = res.get("markdown") or ""
        except Exception as exc:
            self.log(f"figure embed skipped for {dest['label']}: {exc}", level="warning")

    # Provenance: the artifact records where it is used. The consumer's embed
    # is the forward half; this is what stops the artifact looking orphaned
    # when you open it directly.
    used = [u for u in (fm.get("used_in") or []) if isinstance(u, str)]
    if dest["label"] not in used:
        used.append(dest["label"])
        self.vault_update(self._rel_record(rid), {"used_in": used})

    await self.emit("viz:figure_exported", {"id": rid, "target": dest["label"], "svg": svg_rel})
    return {"ok": True, "id": rid, "shape": shape, "target": dest["label"],
            "svg_path": svg_rel, "png_path": png_rel, "embedded": embedded,
            "markdown": hint}


async def _rasterize_figure(self, svg_rel: str) -> str:
    """Sibling 2x PNG via the shared rasterizer, or "" when it can't run.

    `rasterize_svgs` drives Playwright's SYNC api, which refuses to run inside
    a running event loop — hence to_thread (the sync-call-in-async wedge,
    .claude/rules/dev-gotchas.md). Failure is soft: the SVG is the editable
    source and is already written, and on the publish path the build hook
    re-derives the PNG on the next build anyway.
    """
    try:
        from emptyos.sdk.svg_raster import rasterize_svgs

        pngs = await asyncio.to_thread(rasterize_svgs, [self.vault_root / svg_rel], 2)
    except Exception as exc:  # Playwright absent, chromium absent, bad viewBox
        self.log(f"figure rasterize skipped for {svg_rel}: {exc}", level="warning")
        return ""
    return str(Path(svg_rel).with_suffix(".png")).replace("\\", "/") if pngs else ""


@web_route("POST", "/api/figure")
async def api_figure(self, request) -> dict:
    """Export an artifact as a static figure.

    Body: ``{id, target, rasterize?, embed?, alt?}``.
    """
    body = await request.json()
    return await self.export_figure(
        (body.get("id") or "").strip(),
        (body.get("target") or "").strip(),
        rasterize=body.get("rasterize", True) is not False,
        embed=body.get("embed", True) is not False,
        alt=(body.get("alt") or "").strip(),
    )
