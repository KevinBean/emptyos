#!/usr/bin/env python3
"""Inventory + staleness audit for dark feature flags.

EmptyOS ships features dark behind per-app flags (``feature.<slug>.enabled``,
read via ``app_config``/``setting``) and kernel flags (``[autopilot] <key>``).
The convention makes flags greppable, but nothing answers "how many exist, and
which have been dark long enough that they're forgotten rather than soaking?"
This scanner is that answer — the graduated form of the one-off grep, per
.claude/rules/audits.md / self-audit-loops.md.

For every flag it reports:

  1. WHERE it is read — apps (manifest id), sdk/kernel, plugins, tests.
  2. STATE on this machine — flipped truthy in ``emptyos.toml`` (any
     ``[apps.<id>]`` table, walked the same dot-path way ``Config.get`` does)
     or in the live settings store (``data/settings.json`` keys carrying the
     slug). Note: a flag may also be flipped per-call or on another machine —
     "dark here" is per-machine truth, not a global claim.
  3. AGE — first commit that introduced the flag string (``git log -S``,
     path-limited to the read sites so the pickaxe stays fast).

Classification (per flag):

  ON           flipped truthy on this machine — live, not dark
  wanted-dark  registered in WANTED_DARK below: deliberately dark until a
               named external trigger; never counts as stale
  soaking      dark, younger than --stale-days (default 90) or not yet
               committed — the normal post-merge state
  STALE        dark, older than --stale-days, no wanted-dark registration —
               nobody decided: flip-and-collapse it, or register it wanted-dark
  test-only    read only under tests/ — likely a leftover fixture name

Exit code = number of STALE flags (exit-code-as-signal; registered gate=False
in preflight, so stale flags warn rather than block).

Pure file I/O + git subprocess. Does NOT import emptyos.kernel (no syslog
handle; safe while the daemon is up — .claude/rules/daemon-handling.md).

Usage::

    python scripts/check_dark_flags.py                 # human table
    python scripts/check_dark_flags.py --json          # agent-cli envelope
    python scripts/check_dark_flags.py --stale-days 60
    python scripts/check_dark_flags.py --no-age        # skip git pickaxe (fast)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path

from check_base import REPO_ROOT, iter_py_files

SCAN_ROOTS = ["apps", "emptyos", "plugins", "engines", "tests"]

# App-level dark flags: feature.<slug>.enabled (read via app_config/setting).
APP_FLAG_RE = re.compile(r"feature\.([a-z0-9_-]+)\.enabled")
# Kernel-level dark flags: any quoted dot-path into the [autopilot] section.
# (The autopilot section is today's kernel dark-flag namespace; extend the
# alternation when another kernel section grows dark flags.)
KERNEL_FLAG_RE = re.compile(r"[\"'](autopilot\.[a-z0-9_]+)[\"']")

# Flags that are deliberately dark-until-wanted: dark is their honest steady
# state, waiting on a named external trigger — they never go STALE. Adding an
# entry here IS the "write the trigger down" step; keep the reason concrete.
WANTED_DARK: dict[str, str] = {
    "feature.mcp-foundry.enabled": (
        "dark until an external MCP client actually needs verb access "
        "(.claude/rules/autopilot-grants.md)"
    ),
    "feature.memory-extraction.enabled": (
        "rooms-distill KB extraction: decision-gate verified clean, but kept "
        "dark by choice — flip per-use when distilling, not as a standing mode"
    ),
}


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO_ROOT)).replace("\\", "/")


_manifest_id_cache: dict[Path, str | None] = {}


def _owner_id(path: Path) -> str | None:
    """Manifest id of the app/plugin dir containing *path*, else None."""
    for parent in path.parents:
        if parent == REPO_ROOT:
            return None
        if parent in _manifest_id_cache:
            return _manifest_id_cache[parent]
        m = parent / "manifest.toml"
        if m.exists():
            ident: str | None = parent.name
            try:
                data = tomllib.loads(m.read_text(encoding="utf-8"))
                ident = (
                    data.get("app", {}).get("id")
                    or data.get("plugin", {}).get("id")
                    or parent.name
                )
            except Exception:
                pass
            _manifest_id_cache[parent] = ident
            return ident
    return None


def _site_kind(rel: str) -> str:
    if rel.startswith("tests/") or "/tests/" in rel:
        return "test"
    if rel.startswith("emptyos/"):
        return "sdk"
    if rel.startswith("plugins/"):
        return "plugin"
    return "app"


def discover() -> dict[str, dict]:
    """Scan SCAN_ROOTS for flag reads. Returns {flag: {sites, owners, kinds}}."""
    flags: dict[str, dict] = {}
    for f in iter_py_files(SCAN_ROOTS):
        if f.name == Path(__file__).name:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _rel(f)
        found: set[str] = set()
        for m in APP_FLAG_RE.finditer(text):
            found.add(f"feature.{m.group(1)}.enabled")
        for m in KERNEL_FLAG_RE.finditer(text):
            found.add(m.group(1))
        for flag in found:
            entry = flags.setdefault(flag, {"sites": [], "owners": set(), "kinds": set()})
            entry["sites"].append(rel)
            kind = _site_kind(rel)
            entry["kinds"].add(kind)
            if kind in ("app", "plugin"):
                owner = _owner_id(f)
                if owner:
                    entry["owners"].add(owner)
    return flags


def _load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _flatten_settings(node: object, prefix: str = "") -> dict[str, object]:
    """Flatten data/settings.json to dotted keys.

    The settings service stores an app's toggles nested under its id
    (``{"agent_fleet": {"feature": {"fleet": {"enabled": true}}}}``) while the
    settings *page* writes some keys verbatim as one dotted string. Flattening
    normalises both into the form ``BaseApp.setting_or_config`` actually asks
    for: ``<app_id>.feature.<slug>.enabled``.
    """
    out: dict[str, object] = {}
    if not isinstance(node, dict):
        return out
    for k, v in node.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten_settings(v, key + "."))
        else:
            out[key] = v
    return out


def machine_state(flags: dict[str, dict]) -> dict[str, list[str]]:
    """Where each flag is flipped TRUTHY on this machine. {flag: ["src=val"]}."""
    cfg = _load_toml(REPO_ROOT / "emptyos.toml")
    apps_cfg = cfg.get("apps", {}) if isinstance(cfg.get("apps"), dict) else {}
    plugins_cfg = cfg.get("plugins", {}) if isinstance(cfg.get("plugins"), dict) else {}
    settings: dict = {}
    sp = REPO_ROOT / "data" / "settings.json"
    if sp.exists():
        try:
            raw = json.loads(sp.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                settings = raw
        except Exception:
            pass

    out: dict[str, list[str]] = {}
    for flag in flags:
        hits: list[str] = []
        if flag.startswith("feature."):
            slug = flag.split(".")[1]
            # emptyos.toml: [apps.<id>] feature.<slug>.enabled — same nested
            # walk Config.get does for app_config("feature.<slug>.enabled").
            for app_id, tbl in apps_cfg.items():
                node = tbl
                for part in ("feature", slug, "enabled"):
                    node = node.get(part) if isinstance(node, dict) else None
                    if node is None:
                        break
                if node:
                    hits.append(f"emptyos.toml [apps.{app_id}]")
            # emptyos.toml: [plugins.<id>] feature.<slug>.enabled — same walk;
            # plugin-owned flags (e.g. telegram two-way) live here, read by
            # the plugin's own nested-walk over BasePlugin.config("feature").
            for plugin_id, tbl in plugins_cfg.items():
                node = tbl
                for part in ("feature", slug, "enabled"):
                    node = node.get(part) if isinstance(node, dict) else None
                    if node is None:
                        break
                if node:
                    hits.append(f"emptyos.toml [plugins.{plugin_id}]")
            # settings store: live toggles keyed by app convention (e.g.
            # "voice-assistant.two-speed"); slug-containment is a heuristic,
            # so the matched key is shown for the human to judge. Scalar
            # truthy only — a dict value is an unrelated config blob, not a
            # flipped toggle (e.g. key "feature-pipeline" vs slug "pipeline").
            # Precise: the exact key BaseApp.setting_or_config reads, i.e.
            # "<app_id>.feature.<slug>.enabled". The store is NESTED, so the
            # top-level value is a dict and the scalar heuristic below can never
            # see it — that blind spot reported 4 live flags as dark (2026-08-28).
            # Only truthy counts: a reset writes null and a cleared input writes
            # "", both of which mean "unset, fall through to TOML".
            suffix = f"feature.{slug}.enabled"
            flat = _flatten_settings(settings)
            for key, val in flat.items():
                if (key == suffix or key.endswith("." + suffix)) and val:
                    hits.append(f"settings.json {key}={val!r}")
            # Legacy heuristic, deliberately unchanged and top-level-only: a flat
            # scalar toggle whose key merely CONTAINS the slug (e.g.
            # "voice-assistant.two-speed"). Widening it over the flattened map
            # would trade this fix for a new false-positive class.
            for key, val in settings.items():
                if slug in str(key) and isinstance(val, (bool, int, str)) and val:
                    hits.append(f"setting {key}={val!r}")
        else:  # kernel dot-path, e.g. autopilot.meter_spend
            node: object = cfg
            for part in flag.split("."):
                node = node.get(part) if isinstance(node, dict) else None
                if node is None:
                    break
            if node:
                hits.append(f"emptyos.toml [{flag.rsplit('.', 1)[0]}]")
        if hits:
            out[flag] = hits
    return out


def first_seen(flag: str, sites: list[str]) -> str | None:
    """ISO date of the first commit introducing *flag* (path-limited pickaxe)."""
    try:
        r = subprocess.run(
            ["git", "log", "--format=%as", "-S", flag, "--", *sites],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return None
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else None  # oldest commit = last line


def classify(flag: str, entry: dict, state: dict, intro: str | None,
             stale_days: int) -> tuple[str, int | None]:
    age: int | None = None
    if intro:
        try:
            age = (date.today() - date.fromisoformat(intro)).days
        except ValueError:
            pass
    if flag in state:
        return "ON", age
    if flag in WANTED_DARK:
        return "wanted-dark", age
    if entry["kinds"] == {"test"}:
        return "test-only", age
    if age is not None and age > stale_days:
        return "STALE", age
    return "soaking", age


def main() -> int:
    ap = argparse.ArgumentParser(description="Dark feature-flag inventory + staleness audit.")
    ap.add_argument("--json", action="store_true", help="agent-cli JSON envelope")
    ap.add_argument("--stale-days", type=int, default=90,
                    help="dark flags older than this (and not wanted-dark) are STALE")
    ap.add_argument("--no-age", action="store_true", help="skip git pickaxe (faster; nothing goes STALE)")
    args = ap.parse_args()

    flags = discover()
    state = machine_state(flags)

    rows = []
    for flag in sorted(flags):
        entry = flags[flag]
        intro = None if args.no_age else first_seen(flag, entry["sites"])
        cls, age = classify(flag, entry, state, intro, args.stale_days)
        where = sorted(entry["owners"]) or sorted(entry["kinds"] - {"test"}) or ["tests"]
        rows.append({
            "flag": flag,
            "class": cls,
            "where": where,
            "sites": sorted(entry["sites"]),
            "introduced": intro,
            "age_days": age,
            "state": state.get(flag, []),
            "wanted_dark_reason": WANTED_DARK.get(flag),
        })

    order = {"STALE": 0, "test-only": 1, "soaking": 2, "wanted-dark": 3, "ON": 4}
    rows.sort(key=lambda r: (order.get(r["class"], 9), r["flag"]))
    stale = [r for r in rows if r["class"] == "STALE"]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    summary = f"{len(rows)} dark flags: " + ", ".join(
        f"{counts[k]} {k}" for k in ("STALE", "test-only", "soaking", "wanted-dark", "ON") if k in counts
    )

    if args.json:
        print(json.dumps({
            "ok": not stale,
            "code": "ok" if not stale else "stale",
            "message": summary,
            "data": {"flags": rows, "stale_days": args.stale_days},
        }))
        return len(stale)

    print(f"dark-flag audit - {len(rows)} flags (stale after {args.stale_days}d dark)\n")
    for r in rows:
        intro = r["introduced"] or ("-" if args.no_age else "uncommitted")
        age = f"{r['age_days']}d" if r["age_days"] is not None else "-"
        line = f"  {r['class']:<11} {r['flag']:<42} {', '.join(r['where']):<24} {intro} ({age})"
        print(line)
        for s in r["state"]:
            print(f"{'':14}ON via {s}")
        if r["wanted_dark_reason"]:
            print(f"{'':14}why dark: {r['wanted_dark_reason']}")
    if stale:
        print(f"\n{len(stale)} STALE flag(s) - for each: flip-and-collapse it, or register it")
        print("in WANTED_DARK (this script) with a concrete trigger.")
    print(summary)
    return len(stale)


if __name__ == "__main__":
    sys.exit(main())
