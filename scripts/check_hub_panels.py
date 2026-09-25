#!/usr/bin/env python
"""Validate `[[contributes.hub.panel]]` declarations against the hub's renderers.

Two deterministic invariants, both stated in `.claude/rules/hub-panels.md` and
neither enforced by anything until now:

1. **Renderer must exist.** A panel declaring a renderer the hub doesn't know
   renders a literal red `Unknown renderer: <name>` box on the home screen
   (`hub.js::renderExploreBlock`). Verifiable: the renderer name is either a key
   of hub.js's `RENDERERS` map or it isn't.
2. **Panel id must be unique across all apps.** Two apps contributing the same
   id collide in the DOM, and lazy hydration (`/hub/api/panel/{id}`) then fetches
   whichever the aggregator sorted first.

Both gate. Both are silent on a healthy tree (0 hits as of 2026-07-10).

**What is deliberately NOT gated.** `hub.js:744` drops every panel with
`priority >= 150` from the Explore list — 64 of 104 contributions sit there.
That is intentional ("decision 3", the home-companion redesign), and those panels
still render on the personal `hub-life` dashboard. Gating it would fail on 62% of
a healthy tree, which `.claude/rules/audits.md` calls heuristic noise. It is
reported as a one-line advisory instead, so the count never goes silent.

Scope note: only the `hub` contribution namespace is checked (the
`hub-life` namespace targets `apps/personal/hub-life/`, gitignored and
personal — declaring `[[contributes.hub-life.panel]]` IS the opt-out for a
life-only panel, never gated). But a `hub.panel` contribution is rendered by
**both** dashboards (`hub-life` reads the union of `hub.panel` +
`hub-life.panel`, per `.claude/rules/hub-panels.md`), so its renderer must
exist in **both** maps to actually render everywhere it's promised. When
`apps/personal/hub-life/pages/index.html` is present (this machine), a
`hub.panel` renderer missing from hub-life's own `RENDERERS` map gates too —
found live 2026-08-05: `app-grid`/`entity-card`/`garden-mini`/`quick-add`
painted a red box on hub-life while passing this checker, because it only
ever looked at core `hub.js`. On a fresh clone hub-life doesn't exist, so
that half degrades to "unchecked", not "pass" — never a false negative that
reads as green.

Deliberate exception at a manifest site (a renderer provided some other way):

    # hub-panel-check: ignore renderer my-custom-thing

Pure stdlib + `emptyos.sdk.app_layout` (no kernel boot). Exit code = number of
gating violations.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from emptyos.sdk.app_layout import iter_app_dirs  # noqa: E402
from scanner_lib import emit_json  # noqa: E402

HUB_JS = REPO / "apps" / "public" / "core" / "hub" / "pages" / "hub.js"
HUB_LIFE_INDEX = REPO / "apps" / "personal" / "hub-life" / "pages" / "index.html"

# `RENDERERS` is an object literal of `'name': function(...)` entries. Keys sit
# at one or two levels of indentation; nothing else in the file matches at that
# depth, so a windowed line-anchored scan is exact enough to be trustworthy.
_RENDERER_KEY = re.compile(r"^\s{0,4}'([a-z0-9-]+)'\s*:", re.M)
_IGNORE_MARKER = re.compile(r"#\s*hub-panel-check:\s*ignore\s+renderer\s+([a-z0-9-]+)")

AMBIENT_FLOOR = 150  # hub.js:744 — `priority >= 150` is dropped from Explore


def hub_renderers(hub_js: Path = HUB_JS) -> set[str]:
    """Renderer names hub.js can dispatch. Empty set means we couldn't parse."""
    try:
        src = hub_js.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    i = src.find("RENDERERS")
    if i < 0:
        return set()
    return set(_RENDERER_KEY.findall(src[i : i + 30_000]))


def hub_life_renderers(index_html: Path = HUB_LIFE_INDEX) -> set[str] | None:
    """Renderer names hub-life's own RENDERERS map can dispatch.

    ``None`` means "unresolvable here" (gitignored/personal — absent on a
    fresh clone, or present but unparseable) and must degrade to
    "unchecked", never to a pass. An empty set is a real, checkable zero.
    """
    try:
        src = index_html.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    i = src.find("RENDERERS")
    if i < 0:
        return None
    found = set(_RENDERER_KEY.findall(src[i : i + 30_000]))
    return found or None


def _panels(manifest: dict) -> list[dict]:
    entries = (manifest.get("contributes", {}).get("hub", {}) or {}).get("panel") or []
    return [entries] if isinstance(entries, dict) else list(entries)


def collect(apps_root: Path) -> list[dict]:
    """Every `hub.panel` contribution from every app the loader would load.

    `iter_app_dirs` skips `_retired/` and `_example/`, so retired apps — which
    never contribute at runtime — cannot produce findings.
    """
    out: list[dict] = []
    for app_id, app_dir in iter_app_dirs(apps_root, include_personal=True):
        man_path = app_dir / "manifest.toml"
        try:
            raw = man_path.read_text(encoding="utf-8")
            manifest = tomllib.loads(raw)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        ignored = set(_IGNORE_MARKER.findall(raw))
        for p in _panels(manifest):
            out.append(
                {
                    "app": app_id,
                    "id": p.get("id") or "",
                    "renderer": p.get("renderer") or "",
                    "priority": int(p.get("priority", 100) or 100),
                    "manifest": _display_path(man_path),
                    "ignored": p.get("renderer") in ignored,
                }
            )
    return out


def _display_path(path: Path) -> str:
    """Repo-relative when possible; absolute otherwise (tests scan tmp dirs)."""
    try:
        return str(path.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def analyze(
    panels: list[dict],
    renderers: set[str],
    hub_life_renderers_: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return (gating violations, advisory notes).

    ``hub_life_renderers_`` is ``None`` when hub-life is unresolvable here
    (gitignored, absent) — that half of the check is skipped entirely, not
    passed. When it's a set, every ``hub.panel`` renderer must also be a key
    of it, since hub-life renders the union of ``hub.panel`` +
    ``hub-life.panel`` (`.claude/rules/hub-panels.md`).
    """
    violations: list[dict] = []

    for p in panels:
        if p["ignored"] or not p["renderer"]:
            continue
        if p["renderer"] not in renderers:
            violations.append({
                "kind": "unknown_renderer",
                "app": p["app"],
                "panel": p["id"],
                "renderer": p["renderer"],
                "manifest": p["manifest"],
                "fix": (
                    f"add a '{p['renderer']}' renderer to hub.js RENDERERS, pick an "
                    f"existing one, or move this to [[contributes.hub-life.panel]]"
                ),
            })
        elif hub_life_renderers_ is not None and p["renderer"] not in hub_life_renderers_:
            violations.append({
                "kind": "unknown_renderer_hub_life",
                "app": p["app"],
                "panel": p["id"],
                "renderer": p["renderer"],
                "manifest": p["manifest"],
                "fix": (
                    f"'{p['renderer']}' renders on the core hub but hub-life's own "
                    f"RENDERERS map (apps/personal/hub-life/pages/index.html) has no "
                    f"matching entry — this panel paints a red box there. Port the "
                    f"renderer, or move this panel to [[contributes.hub-life.panel]] "
                    f"if it should only ever render on hub-life"
                ),
            })

    by_id: dict[str, list[dict]] = defaultdict(list)
    for p in panels:
        if p["id"]:
            by_id[p["id"]].append(p)
    for pid, group in sorted(by_id.items()):
        if len(group) > 1:
            violations.append({
                "kind": "duplicate_id",
                "panel": pid,
                "apps": sorted(g["app"] for g in group),
                "manifest": group[0]["manifest"],
                "fix": f"panel ids are global; rename all but one of {pid!r}",
            })

    ambient = [p for p in panels if p["priority"] >= AMBIENT_FLOOR]
    advisory = [{
        "kind": "ambient_band",
        "count": len(ambient),
        "total": len(panels),
        "note": (
            f"{len(ambient)} of {len(panels)} panels sit at priority >= {AMBIENT_FLOOR} "
            f"and are dropped from /hub/ Explore by design (they render on hub-life). "
            f"Author below {AMBIENT_FLOOR} to appear on the core hub."
        ),
    }] if ambient else []

    return violations, advisory


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--json", action="store_true", help="emit the agent-cli envelope")
    ap.add_argument("--apps-root", default=str(REPO / "apps"))
    args = ap.parse_args()

    renderers = hub_renderers()
    if not renderers:
        msg = f"could not parse the RENDERERS map in {HUB_JS.relative_to(REPO)}"
        if args.json:
            return emit_json(False, "error", msg, None)
        print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    panels = collect(Path(args.apps_root))
    violations, advisory = analyze(panels, renderers, hub_life_renderers())

    if args.json:
        return emit_json(
            not violations,
            "ok" if not violations else "hub_panel_violations",
            f"{len(violations)} violation(s) across {len(panels)} panel contributions",
            {"violations": violations, "advisory": advisory, "renderers": sorted(renderers)},
        )

    for note in advisory:
        print(f"note: {note['note']}")
    if not violations:
        print(f"OK: {len(panels)} hub.panel contributions, {len(renderers)} renderers, no violations")
        return 0

    print(f"\n{len(violations)} hub.panel violation(s):\n", file=sys.stderr)
    for v in violations:
        if v["kind"] == "unknown_renderer":
            print(f"  {v['manifest']}: panel '{v['panel']}' ({v['app']}) "
                  f"uses unknown renderer '{v['renderer']}'\n    fix: {v['fix']}", file=sys.stderr)
        elif v["kind"] == "unknown_renderer_hub_life":
            print(f"  {v['manifest']}: panel '{v['panel']}' ({v['app']}) "
                  f"renderer '{v['renderer']}' missing from hub-life\n    fix: {v['fix']}", file=sys.stderr)
        else:
            print(f"  duplicate panel id '{v['panel']}' in {', '.join(v['apps'])}"
                  f"\n    fix: {v['fix']}", file=sys.stderr)
    return len(violations)


if __name__ == "__main__":
    raise SystemExit(main())
