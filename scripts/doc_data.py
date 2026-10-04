"""Shared deterministic extractor for the EmptyOS documentation system.

One source of truth for the *facts* every doc renderer needs — apps, capabilities,
plugins, release tiers — so APPS.md, TIERS.md, and the public site
(`generate_emptyos_site.py`) can't drift from each other or from the code. Pure
stdlib + `tomllib` + `ast`; **never boots the kernel**, so it runs in a bare CI
checkout and from `/preflight` without a daemon.

The split this module enforces (see `docs/DOC-SYSTEM.md`):
  - facts that drift  → extracted here, rendered by the generators (generated docs)
  - judgment/prose    → hand-authored in README / DESIGN / CLAUDE.md

Usage:
    python scripts/doc_data.py --dump     # human summary (caps, plugins, app counts, tiers)
    python scripts/doc_data.py --json     # machine-readable dump of everything

Consumers:
    scripts/generate_apps_doc.py   — docs/APPS.md
    scripts/generate_tiers_doc.py  — docs/TIERS.md
    scripts/generate_emptyos_site.py — eos.binbian.net app/capability/plugin pages
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS_ROOT = ROOT / "apps"
PLUGINS_ROOT = ROOT / "plugins"
CAPS_FILE = ROOT / "emptyos" / "capabilities" / "types.py"
RELEASE_TOML = ROOT / "release.toml"
EXAMPLE_TOML = ROOT / "emptyos.example.toml"


# ── Capabilities ──────────────────────────────────────────────────────────────

def _first_sentence(doc: str | None) -> str:
    if not doc:
        return ""
    line = doc.strip().split("\n", 1)[0].strip()
    # Trim to first sentence end if the opening line runs long.
    for end in (". ", ".\t"):
        if end in line:
            line = line.split(end, 1)[0] + "."
            break
    return line


def scan_capabilities() -> list[dict]:
    """Every capability declared in `emptyos/capabilities/types.py`.

    Parsed with `ast` (no import) — returns name, one-line summary (class
    docstring), and the declared kwargs of `execute` (the call signature).
    """
    src = CAPS_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    caps: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(isinstance(b, ast.Name) and b.id == "Capability" for b in node.bases):
            continue
        name = ""
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == "name"
                and isinstance(stmt.value, ast.Constant)
            ):
                name = str(stmt.value.value)
        args: list[str] = []
        for stmt in node.body:
            if isinstance(stmt, ast.AsyncFunctionDef) and stmt.name == "execute":
                for a in stmt.args.kwonlyargs:
                    if a.arg != "kwargs":
                        args.append(a.arg)
        caps.append(
            {
                "name": name or node.name,
                "summary": _first_sentence(ast.get_docstring(node)),
                "args": args,
                "class": node.name,
            }
        )
    return caps


# ── Apps ──────────────────────────────────────────────────────────────────────

def _count_routes(app_dir: Path) -> int:
    n = 0
    for py in app_dir.rglob("*.py"):
        try:
            n += py.read_text(encoding="utf-8", errors="ignore").count("@web_route")
        except OSError:
            continue
    return n


def _gitignored(dirs: list[Path]) -> set[Path]:
    """Which of ``dirs`` git ignores — batched into one ``check-ignore`` call.

    A generated catalog is a **tracked** file, so it must not advertise app
    directories that only exist on the machine running the generator. Local
    fixtures are gitignored on purpose (``apps/test-app/``,
    ``apps/extension/others/test-app/`` — .gitignore:33-34), and without this
    filter every regeneration on such a box re-adds them to docs/APPS.md and
    bumps the headline count.

    Deliberately NOT pushed down into ``app_layout.iter_app_dirs``: the runtime
    loader *should* keep loading a local fixture app. Only the published catalog
    excludes it — through the SDK's ``gitignored_app_dirs`` (``personal/`` exempt,
    bytes-safe batched call), which degrades open: no git, no repo, or any
    failure -> nothing filtered, i.e. exactly the previous behaviour.
    """
    sys.path.insert(0, str(ROOT))
    from emptyos.sdk.app_layout import gitignored_app_dirs

    return gitignored_app_dirs(dirs, APPS_ROOT)


def scan_apps(*, include_personal: bool = False, include_catalog: bool = False) -> list[dict]:
    """Every app across the track tree (depth-agnostic via app_layout)."""
    sys.path.insert(0, str(ROOT))
    from emptyos.sdk.app_layout import iter_app_dirs, track_of, group_of

    found = list(
        iter_app_dirs(
            APPS_ROOT, include_personal=include_personal, include_catalog=include_catalog
        )
    )
    ignored = _gitignored([adir for _, adir in found])

    apps: list[dict] = []
    for aid, adir in found:
        if adir in ignored:
            continue
        try:
            with open(adir / "manifest.toml", "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        app = data.get("app", {}) or {}
        requires = data.get("requires", {}) or {}
        provides = data.get("provides", {}) or {}
        apps.append(
            {
                "id": app.get("id") or aid,
                "name": app.get("name") or aid,
                "description": (app.get("description") or "").strip(),
                "version": app.get("version") or "",
                "private": bool(app.get("private", False)),
                "store_category": app.get("store_category") or "",
                "track": track_of(adir, APPS_ROOT),
                "group": group_of(adir, APPS_ROOT),
                "capabilities": requires.get("capabilities", []) or [],
                "apps": requires.get("apps", []) or [],
                "optional_apps": requires.get("optional_apps", []) or [],
                "web_prefix": (provides.get("web", {}) or {}).get("prefix", ""),
                "cli_commands": (provides.get("cli", {}) or {}).get("commands", []) or [],
                "emits": (provides.get("events", {}) or {}).get("emits", []) or [],
                "has_ui": (adir / "pages" / "index.html").exists(),
                "routes": _count_routes(adir),
                "rel_path": str(adir.relative_to(ROOT)).replace("\\", "/"),
            }
        )
    apps.sort(key=lambda a: (a["track"], a["group"], a["id"]))
    return apps


# ── Plugins ───────────────────────────────────────────────────────────────────

def scan_plugins() -> list[dict]:
    """Every plugin with a manifest under plugins/ (skips _retired / _archive)."""
    out: list[dict] = []
    if not PLUGINS_ROOT.is_dir():
        return out
    for mf in sorted(PLUGINS_ROOT.glob("*/manifest.toml")):
        if mf.parent.name.startswith("_"):
            continue
        try:
            with open(mf, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        plug = data.get("plugin", {}) or {}
        out.append(
            {
                "id": plug.get("id") or mf.parent.name,
                "name": plug.get("name") or mf.parent.name,
                "description": (plug.get("description") or "").strip(),
                "type": plug.get("type") or "",
                "capability": plug.get("capability") or plug.get("enhances") or "",
            }
        )
    return out


# ── Release tiers / platforms / targets ───────────────────────────────────────

def _resolve_tier(name: str, raw: dict, seen: set | None = None) -> dict:
    """Resolve a tier's effective app/plugin/skill sets, following `extends`."""
    seen = seen or set()
    tier = dict(raw.get(name, {}))
    apps = list(tier.get("apps", []) or [])
    plugins = list(tier.get("plugins", []) or [])
    skills = list(tier.get("skills", []) or [])
    parent = tier.get("extends")
    if parent and parent in raw and parent not in seen:
        seen.add(parent)
        p = _resolve_tier(parent, raw, seen)
        apps = list(dict.fromkeys(p["apps"] + apps))
        plugins = list(dict.fromkeys(p["plugins"] + plugins))
        skills = list(dict.fromkeys(p["skills"] + skills))
    return {
        "name": name,
        "kind": tier.get("kind", "distribution" if name not in {"core", "standard"} else "platform"),
        "display_name": tier.get("display_name", name),
        "audience": tier.get("audience", ""),
        "private": bool(tier.get("private", False)),
        "product_line": tier.get("product_line", ""),
        "description": tier.get("description", ""),
        # How this tier actually reaches a user. Not inherited through
        # `extends` — delivery is a property of the tier itself, and a child
        # silently claiming its parent's target would be the opposite of what
        # this field exists to expose.
        "delivery": tier.get("delivery", ""),
        "extends": parent or "",
        "target_platform": tier.get("target_platform", ""),
        "apps": apps,
        "plugins": plugins,
        "skills": skills,
        "own_apps": list(tier.get("apps", []) or []),
    }


def scan_release() -> dict:
    """Version, platforms, product lines, resolved tiers, and release targets from release.toml."""
    with open(RELEASE_TOML, "rb") as f:
        data = tomllib.load(f)
    version = (data.get("release", {}) or {}).get("version", "")
    platforms = _sub_tables(data, "platforms")
    tiers_raw = _sub_tables(data, "tiers")
    tiers = {name: _resolve_tier(name, tiers_raw) for name in tiers_raw}
    targets = _sub_tables(data, "targets")
    product_lines = _sub_tables(data, "product_lines")
    return {"version": version, "platforms": platforms, "product_lines": product_lines,
            "tiers": tiers, "targets": targets}


def _sub_tables(data: dict, prefix: str) -> dict:
    """Return the sub-tables under `[prefix.*]` keyed by bare name.

    tomllib already nests `[tiers.core]` as data["tiers"]["core"], so this is
    just that nested dict, filtered to table values (`{"core": {...}, ...}`).
    """
    section = data.get(prefix, {})
    if not isinstance(section, dict):
        return {}
    return {k: v for k, v in section.items() if isinstance(v, dict)}


# ── Aggregate stats ───────────────────────────────────────────────────────────

def system_stats(apps: list[dict] | None = None, plugins: list[dict] | None = None) -> dict:
    apps = apps if apps is not None else scan_apps()
    plugins = plugins if plugins is not None else scan_plugins()
    by_track: dict[str, int] = {}
    for a in apps:
        key = f"{a['track']}/{a['group']}" if a["group"] else (a["track"] or "flat")
        by_track[key] = by_track.get(key, 0) + 1
    return {
        "apps_public": sum(1 for a in apps if a["track"] == "public"),
        "apps_extension": sum(1 for a in apps if a["track"] == "extension"),
        "apps_total": len(apps),
        "apps_by_track": by_track,
        "routes_total": sum(a["routes"] for a in apps),
        "plugins": len(plugins),
        "capabilities": len(scan_capabilities()),
    }


def collect_all(*, include_personal: bool = False) -> dict:
    apps = scan_apps(include_personal=include_personal)
    plugins = scan_plugins()
    return {
        "capabilities": scan_capabilities(),
        "apps": apps,
        "plugins": plugins,
        "release": scan_release(),
        "stats": system_stats(apps, plugins),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="EmptyOS doc-system data extractor")
    ap.add_argument("--json", action="store_true", help="dump everything as JSON")
    ap.add_argument("--dump", action="store_true", help="human summary (default)")
    ap.add_argument("--include-personal", action="store_true")
    args = ap.parse_args()

    if args.json:
        print(json.dumps(collect_all(include_personal=args.include_personal), indent=2, default=str))
        return 0

    caps = scan_capabilities()
    plugins = scan_plugins()
    apps = scan_apps(include_personal=args.include_personal)
    rel = scan_release()
    stats = system_stats(apps, plugins)

    print(f"EmptyOS doc-data  (release v{rel['version']})")
    print(f"  capabilities : {len(caps)}  ({', '.join(c['name'] for c in caps)})")
    print(f"  plugins      : {len(plugins)}")
    print(f"  apps total   : {stats['apps_total']}  (public {stats['apps_public']}, extension {stats['apps_extension']})")
    print("  apps by track/group:")
    for k, v in sorted(stats["apps_by_track"].items()):
        print(f"      {k:<24} {v}")
    print(f"  routes total : {stats['routes_total']}")
    print(f"  tiers        : {len(rel['tiers'])}  ({', '.join(rel['tiers'])})")
    print(f"  platforms    : {len(rel['platforms'])}  ({', '.join(rel['platforms'])})")
    print(f"  targets      : {len(rel['targets'])}  ({', '.join(rel['targets'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
