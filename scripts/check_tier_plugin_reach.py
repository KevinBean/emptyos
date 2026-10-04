#!/usr/bin/env python3
"""Flag a tier that ships an app naming a plugin the tier itself does not ship.

`release_filter.prune_snapshot` DELETES every plugin absent from the resolved
tier union, so a tier's plugin list is "what reaches the user". An app is not
filtered with it: `rooms` declares `agent-runtime` under `[requires].services`
and `viz` declares `manim` under `[requires].plugins`, and both shipped in
`standard` while neither plugin did. `_validate_dependencies` only WARNS to
syslog, so the app loads, the manifest still claims the dependency, and the
feature is simply dead — `self.require("agent-runtime")` raises the first time
a user clicks.

WHAT THIS ACTUALLY GUARDS, measured rather than hoped. Against the 2026-09-20
pre-fix tree it reports 18 triples over 8 tiers, but only **2 distinct plugins
across 3 apps**: `agent-runtime` (rooms, forge) and `manim` (viz). The hand
audit that motivated the fix found seven — `markitdown`/`ocr` (reader),
`meeting-capture` (braindump), `tailscale` (hub), `translate`
(voice-assistant), plus `legacy-doc` and `command-launcher`. **Those are NOT
guarded here**, because none is declared in a manifest: their apps reach them
through `self.service(...)` / `get_optional(...)` in source. Do not read a
green run as "every plugin dependency reaches its tier" — read it as "every
*declared* one does".

The two uncovered classes, both deliberate:

  - a SOURCE-ONLY dependency (`self.service("markitdown")`). `BaseApp.service`
    is the optional form and `require` is the raising one, so a call site alone
    cannot distinguish a hard dependency from a graceful probe — the
    >30%-false-positive band `.claude/rules/audits.md` says must never gate.
  - a CAPABILITY dependency (`[requires].capabilities = [..., "pronounce"]`).
    Seven capabilities register with an empty built-in chain, so a plugin is
    their only provider — but `model` and `artifact` are provided by *apps*
    (robot-modeller, viz) and app-side registration is declared nowhere, so the
    general check would fire on a healthy tree. `pronounce` is the one live
    case and is handled by hand in `english-learning` + `englishos-cloud`.

WHY THE EXISTING CHECKERS CANNOT SEE THIS. `check-tier-folder.py` validates
that the ids in a tier's plugin/skill/engine arrays EXIST — it never reads an
app manifest, so it cannot ask whether a shipped app's declared plugin is in
that app's own tier. `check_call_app_declared.py` walks app→app edges, not
app→plugin. This is the app→plugin edge, resolved per tier.

WHAT COUNTS AS "NAMING A PLUGIN". Only the two manifest fields that are a
DECLARATION of dependency:

    [requires] plugins = ["manim"]
    [requires] services = ["agent-runtime"]      # a plugin's provided service

`services` is included because that is how a service plugin is actually
depended on — `rooms` names `agent-runtime` there, not under `plugins`. A
service id that no plugin provides is ignored (it may be provided by an app or
by the kernel), which is what keeps this from firing on `chatbot`/`external_lab`.

Source-level `self.service("x")` / `require("x")` calls are deliberately NOT
scanned. They are reached through `get_optional` as often as not, so a hit
there cannot distinguish a hard dependency from a graceful probe — exactly the
>30%-false-positive band `.claude/rules/audits.md` says must never gate. The
manifest is the app's own statement of intent, and it is unambiguous.

Exit code = number of unreachable (tier, app, plugin) triples. Silent when clean.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: A waiver marker, which must be the whole comment — see `waivers`.
_MARKER = "# tier-plugin-reach: ignore "


def _load_app_layout():
    """Import `iter_app_dirs` by path — the same reason `release_filter` does:
    a release script must work in a bare checkout without importing the SDK."""
    import importlib.util

    path = REPO / "emptyos" / "sdk" / "app_layout.py"
    spec = importlib.util.spec_from_file_location("_app_layout", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.iter_app_dirs


def plugin_services(repo: Path) -> dict[str, str]:
    """Map every service id a plugin provides -> that plugin's dir name.

    Read from the manifest's `[plugin] provides` / `[provides] services`, and
    falling back to the plugin's own directory name, which the loader uses as
    the service id in the common case.
    """
    out: dict[str, str] = {}
    plugins_dir = repo / "plugins"
    if not plugins_dir.is_dir():
        return out
    for d in sorted(plugins_dir.iterdir()):
        if not d.is_dir() or not (d / "plugin.py").exists():
            continue
        out.setdefault(d.name, d.name)
        mf = d / "manifest.toml"
        if not mf.is_file():
            continue
        try:
            with open(mf, "rb") as f:
                man = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        declared = man.get("plugin", {}).get("provides")
        if not declared:
            declared = man.get("provides", {}).get("services")
        for sid in declared or []:
            if isinstance(sid, str) and sid:
                out.setdefault(sid, d.name)
    return out


def resolve_tier(tiers: dict, name: str, key: str, seen: frozenset = frozenset()) -> set[str]:
    """The union of `key` for `name` and everything it extends."""
    if name in seen or name not in tiers:
        return set()
    t = tiers[name]
    got = {x for x in t.get(key, []) if isinstance(x, str)}
    parent = t.get("extends")
    if isinstance(parent, str):
        got |= resolve_tier(tiers, parent, key, seen | {name})
    return got


def app_plugin_needs(repo: Path, services: dict[str, str]) -> dict[str, set[str]]:
    """app id -> the set of PLUGIN dir names its manifest declares it needs."""
    iter_app_dirs = _load_app_layout()
    needs: dict[str, set[str]] = {}
    for app_id, app_dir in iter_app_dirs(repo / "apps", include_personal=True):
        mf = app_dir / "manifest.toml"
        if not mf.is_file():
            continue
        try:
            with open(mf, "rb") as f:
                req = tomllib.load(f).get("requires", {})
        except (OSError, tomllib.TOMLDecodeError):
            continue
        want: set[str] = set()
        for pid in req.get("plugins", []) or []:
            if isinstance(pid, str) and pid:
                want.add(pid)
        for sid in req.get("services", []) or []:
            # Only when a PLUGIN provides it — an app- or kernel-provided
            # service is not this checker's business.
            if isinstance(sid, str) and sid in services:
                want.add(services[sid])
        if want:
            needs[app_id] = want
    return needs


def waivers(repo: Path) -> set[tuple[str, str, str]]:
    """`(tier, app, plugin)` triples marked deliberate at the call site.

    An inline marker inside the `[tiers.<name>]` block it applies to, never a
    central allowlist — per `.claude/rules/audits.md`, an allowlist turns every
    new legitimate case into a build break and drifts out of sight of the
    decision it records:

        # tier-plugin-reach: ignore rooms agent-runtime — <why>

    Scoped to the tier whose block it sits in, so a waiver in `demo` cannot
    silently excuse `standard`.
    """
    out: set[tuple[str, str, str]] = set()
    tier = ""
    for line in (repo / "release.toml").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("[tiers.") and s.endswith("]"):
            tier = s[len("[tiers."):-1]
        elif s.startswith("[") and s.endswith("]"):
            tier = ""
        # COMMENT-LEADING only. This function reads release.toml as text while
        # `scan` reads it as TOML, and a bare `marker in line` let the two
        # disagree in the dangerous direction: the same marker pasted inside a
        # `description = """..."""` or any quoted value registered a waiver and
        # SUPPRESSED a real finding. The syntax is published in
        # `.claude/rules/plugins.md`, so pasting it into prose is the likely
        # accident, not a contrived one. A marker that is not the whole comment
        # is not a waiver.
        if tier and s.startswith(_MARKER):
            parts = s[len(_MARKER):].split()
            if len(parts) >= 2:
                out.add((tier, parts[0], parts[1]))
    return out


def scan(repo: Path = REPO) -> list[dict]:
    with open(repo / "release.toml", "rb") as f:
        tiers = tomllib.load(f).get("tiers", {})
    waived = waivers(repo)
    services = plugin_services(repo)
    needs = app_plugin_needs(repo, services)

    findings: list[dict] = []
    for tier in sorted(tiers):
        apps = resolve_tier(tiers, tier, "apps")
        plugs = resolve_tier(tiers, tier, "plugins")
        for app_id in sorted(apps):
            for pid in sorted(needs.get(app_id, ())):
                if pid not in plugs and (tier, app_id, pid) not in waived:
                    findings.append({"tier": tier, "app": app_id, "plugin": pid})
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    findings = scan()
    msg = (f"{len(findings)} app(s) declare a plugin their tier does not ship"
           if findings else "every declared plugin reaches its tier")
    if args.json:
        return emit_json(not findings, "tier_plugin_unreachable", msg, {"findings": findings})

    if not findings:
        print(f"check-tier-plugin-reach: OK — {msg}.")
        return 0

    print(f"{msg}. The plugin is pruned from that tier's snapshot, so the "
          f"feature is dead while the manifest still claims it.\n")
    by_tier: dict[str, list[dict]] = {}
    for f in findings:
        by_tier.setdefault(f["tier"], []).append(f)
    for tier, rows in by_tier.items():
        print(f"  [{tier}]")
        for r in rows:
            print(f"    {r['app']:22s} needs {r['plugin']}")
    print("\nFix: add the plugin to that tier's `plugins` in release.toml, or "
          "drop the dependency from the app's manifest if it is not real.")
    return len(findings)


if __name__ == "__main__":
    raise SystemExit(main())
