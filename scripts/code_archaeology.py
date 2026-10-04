#!/usr/bin/env python3
"""code_archaeology — static reachability + change-impact audit for EmptyOS.

Why this exists
---------------
EmptyOS is dynamic-dispatch heavy: apps talk via string-keyed
``self.call_app("id", "method")``, the event bus (``emit`` / ``@on_event``),
and manifest contribution slots (``[[contributes.<target>.<slot>]] method=``).
A naive AST dead-code pass over-reports every handler as unused, and the live
``/topology`` graph is *runtime-only* (it walks loaded manifests + kernel
state, it never parses source). So a whole class of bug ships silently:

  * a ``call_app("kb", "get_note")`` whose target app or method doesn't exist
    (this is exactly how the 2026-05-30 ``eos_apps.<id>`` reorg killed apps —
    load failed to ``state:error`` with the traceback dropped),
  * a manifest ``[[contributes.hub.panel]] method = "panel_foo"`` where the
    method was renamed/removed,
  * a ``[requires] apps = ["ghost"]`` pointing at an app that no longer exists.

This tool builds the reachability graph the topology endpoint can't: it parses
manifests (TOML) + every app ``*.py`` (Python ``ast``), injects synthetic edges
for the dynamic-dispatch shapes, and reports findings in **confidence tiers**
(borrowed from repowise's design — degrade gracefully on dynamic dispatch
rather than asserting binary dead/alive).

It is a static scan: **no daemon, no kernel import** (per
``.claude/rules/daemon-handling.md`` it's safe to run while :9000 is up). Pure
stdlib — ``ast``, ``tomllib``, ``pathlib``, ``re``.

Graduation (per .claude/rules/audits.md)
----------------------------------------
First home is ``scripts/`` as a static check. Exit code = count of HIGH-tier
findings, so it can gate a release (wire into ``scripts/release-public.py``)
or a pre-commit hook. Run against known-healthy apps (hub, task, journal)
before trusting a new heuristic — anything that fires on those is noise.

Usage
-----
    python scripts/code_archaeology.py                 # full audit, human report
    python scripts/code_archaeology.py --json          # machine-readable
    python scripts/code_archaeology.py --tier high      # only high-confidence
    python scripts/code_archaeology.py --app task       # scope to one app
    python scripts/code_archaeology.py --impact task.list_tasks
                                                        # who calls this app-method?
    python scripts/code_archaeology.py --impact emit:journal:entry_added
                                                        # who listens for this event?
    python scripts/code_archaeology.py --impact task.add --json
                                                        # agent-cli envelope (depth-grouped
                                                        # reverse blast radius; exit 0 = ok)
    python scripts/code_archaeology.py --impact task.add --depth 5
                                                        # widen transitive traversal
    python scripts/code_archaeology.py --graph out.json # dump the full graph

The --impact path follows .claude/rules/agent-cli.md: bare --impact prints a
human report; --impact --json emits exactly one envelope object
({ok, code, message, data}) to stdout with the exit code mirroring `ok`
(0 = ok, non-zero on not_found / invalid_args). This is the agent-facing seam
(pre-edit blast-radius preview); the discipline is borrowed from GitNexus's
`impact` tool — see memory project_gitnexus_borrow.

Naming note: committed scripts/ helpers must NOT start with an underscore
(``.gitignore`` has ``scripts/_*.py``) — hence ``code_archaeology.py``.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APPS_ROOT = REPO / "apps"

# Methods the platform calls reflectively even when no `call_app` names them —
# these are reachability ROOTS, never "dead". Decorated entrypoints are added
# dynamically; this is the lifecycle + convention set the loader/SDK invoke.
LIFECYCLE_METHODS = {
    "setup", "teardown", "__init__", "start", "stop", "connect", "disconnect",
}
# Prefix conventions the platform discovers by naming (hub/voice/tour/etc. also
# come through manifest method= fields, but these prefixes are belt-and-braces
# so a contribution method missing from the manifest still isn't flagged dead).
ROOT_PREFIXES = ("panel_", "voice_", "narrate_", "assistant_", "api_", "cli_")


# ─────────────────────────── data shapes ───────────────────────────


@dataclass
class AppInfo:
    app_id: str
    dir: Path
    manifest: dict
    public: bool = False   # under apps/public/ — i.e. ships in the public release
    # names defined anywhere in the app's .py (def + class-attr bindings)
    defined: set[str] = field(default_factory=set)
    # subset of `defined` that carry an @web_route/@cli_command/etc. decorator
    decorated: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    # @on_event("e") handlers: event -> [method names]
    listens: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    # every identifier referenced in the app's .py (self.X attrs + bare names)
    referenced: set[str] = field(default_factory=set)
    # emit("e") literal call sites: event -> count
    emits: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # call_app("id","method") literal call sites: (id, method) -> [source files]
    call_app_sites: list[tuple[str, str, str]] = field(default_factory=list)
    # require("svc") literal call sites
    requires_svc: set[str] = field(default_factory=set)


@dataclass
class Finding:
    tier: str           # high | medium | low
    kind: str           # broken-call-app | missing-contribution-method | ...
    app: str
    detail: str
    where: str = ""     # file:line or manifest

    def as_dict(self) -> dict:
        return {"tier": self.tier, "kind": self.kind, "app": self.app,
                "detail": self.detail, "where": self.where}


# ─────────────────────────── discovery ───────────────────────────


def discover_apps(apps_root: Path = APPS_ROOT) -> dict[str, AppInfo]:
    """Glob every apps/**/manifest.toml; build AppInfo keyed by manifest id.

    Reimplements the app_layout walk (rather than importing it) to stay free of
    any kernel side-effects. _catalog/ is parked (not loaded) so we skip it.
    ``apps_root`` defaults to this repo's apps/ but can point elsewhere — e.g.
    a release snapshot's apps/ to gate only what ships publicly.
    """
    apps: dict[str, AppInfo] = {}
    for mf in apps_root.rglob("manifest.toml"):
        rel = mf.relative_to(apps_root)
        # Skip parked/retired/template trees — any path part starting with "_"
        # (_catalog parked, _retired stale-by-definition, _example template).
        # These carry intentionally-dead dispatch and are pure false positives.
        if any(part.startswith("_") for part in rel.parts):
            continue
        try:
            manifest = tomllib.loads(mf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ! unparseable manifest {mf}: {e}", file=sys.stderr)
            continue
        app_id = (manifest.get("app", {}) or {}).get("id") or mf.parent.name
        if app_id in apps:
            # duplicate id across the tree — a real problem; keep first, warn
            print(f"  ! duplicate app id '{app_id}': {mf} vs {apps[app_id].dir}",
                  file=sys.stderr)
            continue
        apps[app_id] = AppInfo(app_id=app_id, dir=mf.parent, manifest=manifest,
                               public=bool(rel.parts) and rel.parts[0] == "public")
    return apps


# ─────────────────────────── AST extraction ───────────────────────────


def _str_arg(node: ast.AST) -> str | None:
    """Return the literal str value of an arg node, else None (non-literal)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_binding_ref(v: ast.AST) -> bool:
    """True if an assignment RHS is a multi-module binding reference:
    ``_mod.func`` / ``name``, a ``staticmethod(_mod.func)`` /
    ``classmethod(...)`` wrap, or a tuple of any of those
    (``a, b = _mod.a, _mod.b``). These are the re-bind shapes documented in
    .claude/rules/multi-module-apps.md and must count as a method definition."""
    if isinstance(v, (ast.Attribute, ast.Name)):
        return True
    if (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
            and v.func.id in ("staticmethod", "classmethod") and v.args):
        return isinstance(v.args[0], (ast.Attribute, ast.Name))
    if isinstance(v, (ast.Tuple, ast.List)):
        return bool(v.elts) and all(_is_binding_ref(e) for e in v.elts)
    return False


def _flatten_name_targets(targets: list[ast.AST]) -> list[str]:
    """Flatten assignment targets to bound Names, descending tuple/list unpack."""
    out: list[str] = []
    stack = list(targets)
    while stack:
        t = stack.pop()
        if isinstance(t, ast.Name):
            out.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            stack.extend(t.elts)
    return out


def _decorator_name(dec: ast.AST) -> str | None:
    """web_route(...) / on_event(...) -> 'web_route' / 'on_event'."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def analyze_app(app: AppInfo) -> None:
    """Walk every .py under the app dir; populate the AST-derived fields."""
    for py in app.dir.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as e:
            print(f"  ! syntax error {py}:{e.lineno}", file=sys.stderr)
            continue
        try:
            rel = py.relative_to(REPO).as_posix()
        except ValueError:
            # --root points outside this repo (e.g. a release snapshot temp dir)
            rel = py.as_posix()
        _walk_module(app, tree, rel)


def _walk_module(app: AppInfo, tree: ast.Module, relpath: str) -> None:
    for node in ast.walk(tree):
        # function / method defs (incl. helper-module module-level funcs)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            app.defined.add(node.name)
            for dec in node.decorator_list:
                dname = _decorator_name(dec)
                if dname in ("web_route", "cli_command", "ws_route", "scheduled"):
                    app.decorated[dname].append(node.name)
                elif dname == "on_event" and isinstance(dec, ast.Call) and dec.args:
                    ev = _str_arg(dec.args[0])
                    if ev:
                        app.listens[ev].append(node.name)
                    app.decorated["on_event"].append(node.name)
        # class-attr / module-level bindings:  NAME = _mod.func   (multi-module)
        elif isinstance(node, ast.Assign):
            if _is_binding_ref(node.value):
                for name in _flatten_name_targets(node.targets):
                    app.defined.add(name)
        # references: self.X attribute access, and bare names
        elif isinstance(node, ast.Attribute):
            app.referenced.add(node.attr)
        elif isinstance(node, ast.Name):
            app.referenced.add(node.id)
        # dynamic dispatch call sites
        if isinstance(node, ast.Call):
            _extract_call(app, node, relpath)


def _extract_call(app: AppInfo, node: ast.Call, relpath: str) -> None:
    fn = node.func
    if not isinstance(fn, ast.Attribute):
        return
    name = fn.attr
    line = getattr(node, "lineno", 0)
    if name == "call_app" and len(node.args) >= 2:
        aid, meth = _str_arg(node.args[0]), _str_arg(node.args[1])
        if aid and meth:
            app.call_app_sites.append((aid, meth, f"{relpath}:{line}"))
        # non-literal args are intentionally skipped (can't resolve statically)
    elif name == "emit" and node.args:
        ev = _str_arg(node.args[0])
        if ev:
            app.emits[ev] += 1
    elif name in ("require", "service") and node.args:
        svc = _str_arg(node.args[0])
        if svc:
            app.requires_svc.add(svc)


# ─────────────────────────── manifest method refs ───────────────────────────


def manifest_method_refs(app: AppInfo) -> list[tuple[str, str]]:
    """Every (method_name, where) the manifest claims this app implements.

    Covers contribution slots, [provides.assistant] commands, and
    [provides.timeline] entity_source/custom_handler. These are reachability
    roots AND must resolve to a real method — a miss is a high-tier bug.
    """
    refs: list[tuple[str, str]] = []
    m = app.manifest

    # [[contributes.<target>.<slot>]]  with a method= field
    contributes = m.get("contributes", {}) or {}
    for target, slots in contributes.items():
        if not isinstance(slots, dict):
            continue
        for slot, entries in slots.items():
            if isinstance(entries, dict):
                entries = [entries]
            if not isinstance(entries, list):
                continue
            for e in entries:
                if isinstance(e, dict) and isinstance(e.get("method"), str):
                    refs.append((e["method"], f"manifest contributes.{target}.{slot}"))

    # [provides.assistant] commands = [{slash, method, ...}]
    assistant = (m.get("provides", {}) or {}).get("assistant", {}) or {}
    for cmd in assistant.get("commands", []) or []:
        if isinstance(cmd, dict) and isinstance(cmd.get("method"), str):
            refs.append((cmd["method"], "manifest provides.assistant.commands"))

    # [provides.timeline] entity_source / custom_handler
    timeline = (m.get("provides", {}) or {}).get("timeline", {}) or {}
    for key in ("entity_source", "custom_handler"):
        v = timeline.get(key)
        if isinstance(v, str) and v:
            refs.append((v, f"manifest provides.timeline.{key}"))

    return refs


# ─────────────────────────── audit ───────────────────────────


def run_audit(apps: dict[str, AppInfo], scope: str | None,
              public_only: bool = False) -> list[Finding]:
    """Audit dispatch reachability.

    Resolution always uses ALL discovered apps (so cross-tier ``call_app``
    targets resolve — a public app calling a personal app is not "missing").
    ``public_only`` filters only which SOURCE apps are audited — the release
    gate wants "broken dispatch in code that actually ships," not personal
    apps' bugs. ``scope`` narrows to a single app id.
    """
    findings: list[Finding] = []
    ids = set(apps)
    if scope:
        targets = [apps[scope]]
    elif public_only:
        targets = [a for a in apps.values() if a.public]
    else:
        targets = list(apps.values())

    # build the event listener index across ALL apps (events cross app bounds)
    listener_index: dict[str, list[str]] = defaultdict(list)
    declared_listens: dict[str, list[str]] = defaultdict(list)
    for a in apps.values():
        for ev in a.listens:
            listener_index[ev].append(a.app_id)
        reqs = (a.manifest.get("requires", {}) or {}).get("events", []) or []
        for ev in reqs:
            declared_listens[ev].append(a.app_id)
        # an app reacting to its own internal events also counts as a listener
    # reactor subscribes broadly; treat any event it could chain as consumed
    reactor_listens = set((apps.get("reactor") or AppInfo("", REPO, {})).listens)

    for app in targets:
        # ── HIGH: broken call_app targets ──────────────────────────────
        # A call_app to an app declared in this app's optional_apps is a
        # tolerated soft integration (CLAUDE.md: "soft integrations; absence is
        # tolerated") — the app degrades gracefully when the target isn't in the
        # tier. Report LOW (not HIGH) so --public-only --tier high doesn't treat
        # an intentional cross-tier soft dep as a broken-dispatch bug. Mirrors the
        # MEDIUM optional_apps-declaration handling below.
        opt_apps = set((app.manifest.get("requires", {}) or {}).get("optional_apps", []) or [])
        for aid, meth, where in app.call_app_sites:
            if aid not in ids:
                if aid in opt_apps:
                    findings.append(Finding(
                        "low", "optional-call-app-absent", app.app_id,
                        f'call_app("{aid}", "{meth}") — optional app "{aid}" not in tier (graceful)',
                        where))
                    continue
                findings.append(Finding(
                    "high", "broken-call-app", app.app_id,
                    f'call_app("{aid}", "{meth}") — app "{aid}" does not exist',
                    where))
            elif meth not in apps[aid].defined:
                findings.append(Finding(
                    "high", "broken-call-app", app.app_id,
                    f'call_app("{aid}", "{meth}") — "{aid}" has no method "{meth}"',
                    where))

        # ── HIGH: manifest method= refs that don't resolve ─────────────
        for meth, where in manifest_method_refs(app):
            if meth not in app.defined:
                findings.append(Finding(
                    "high", "missing-contribution-method", app.app_id,
                    f'manifest names method "{meth}" but it is not defined/bound',
                    where))

        # ── HIGH: requires.apps pointing at a ghost ────────────────────
        reqs = app.manifest.get("requires", {}) or {}
        for dep in reqs.get("apps", []) or []:
            if dep not in ids:
                findings.append(Finding(
                    "high", "broken-requires-app", app.app_id,
                    f'[requires] apps lists "{dep}" — no such app', "manifest"))
        # ── MEDIUM: optional_apps ghost (soft dep, lower severity) ─────
        for dep in reqs.get("optional_apps", []) or []:
            if dep not in ids:
                findings.append(Finding(
                    "medium", "broken-optional-app", app.app_id,
                    f'[requires] optional_apps lists "{dep}" — no such app',
                    "manifest"))

        # ── LOW: emitted events with no discoverable listener ──────────
        internal = set(((app.manifest.get("provides", {}) or {})
                        .get("events", {}) or {}).get("internal", []) or [])
        for ev in app.emits:
            if ev in internal:
                continue
            has_listener = (ev in listener_index or ev in declared_listens
                            or ev in reactor_listens)
            # ws/UI consumers can't be seen statically -> LOW confidence only
            if not has_listener:
                findings.append(Finding(
                    "low", "orphan-emit", app.app_id,
                    f'emits "{ev}" — no @on_event handler or manifest listener '
                    f"found (may be consumed by a UI WebSocket)", "ast"))

    return findings


# ─────────────────────────── change-impact ───────────────────────────


def _reverse_app_index(apps: dict[str, AppInfo]) -> dict[str, set[str]]:
    """target_app -> {source apps that call_app it on ANY method}.

    Self-calls are dropped — an app calling its own methods isn't blast radius.
    Used to expand the depth-1 (method-precise) caller set into transitive
    app-level layers.
    """
    rev: dict[str, set[str]] = defaultdict(set)
    for a in apps.values():
        for aid, _meth, _where in a.call_app_sites:
            if aid != a.app_id:
                rev[aid].add(a.app_id)
    return rev


def impact(apps: dict[str, AppInfo], query: str, *, max_depth: int = 3) -> dict:
    """Reverse blast radius — who depends on X?  Two query forms:

      app.method        -> depth-1 method-precise call_app("app","method") sites,
                           then transitive app-level callers grouped by depth
                           (the GitNexus `impact` discipline: precompute the
                           reverse edges so "who breaks if I edit this" is one
                           answer, not ten queries).
      emit:event:name   -> @on_event listeners + manifest requires.events
                           (depth 1 only — static analysis can't trace which
                           handler re-emits, so we don't fake event chains).

    On error, sets ``error`` (human string) + ``error_code`` (agent-cli token:
    ``not_found`` / ``invalid_args``). The caller maps those onto the envelope.
    """
    if query.startswith("emit:"):
        return _impact_event(apps, query)
    return _impact_app_method(apps, query, max_depth=max_depth)


def _impact_event(apps: dict[str, AppInfo], query: str) -> dict:
    ev = query[len("emit:"):]
    out: dict = {"query": query, "kind": "event", "event": ev,
                 "listeners": [], "emitters": [], "manifest_refs": []}
    affected: set[str] = set()
    for a in apps.values():
        if ev in a.listens:
            out["listeners"].append({"app": a.app_id, "handlers": a.listens[ev]})
            affected.add(a.app_id)
        reqs = (a.manifest.get("requires", {}) or {}).get("events", []) or []
        if ev in reqs:
            out["manifest_refs"].append(
                {"app": a.app_id, "where": "manifest requires.events"})
            affected.add(a.app_id)
        if ev in a.emits:
            out["emitters"].append({"app": a.app_id})
    ordered = sorted(affected)
    out["blast_radius"] = [{"depth": 1, "apps": ordered}] if ordered else []
    out["affected_apps"] = ordered
    out["max_depth_reached"] = 1 if ordered else 0
    return out


def _impact_app_method(apps: dict[str, AppInfo], query: str,
                       *, max_depth: int = 3) -> dict:
    out: dict = {"query": query, "kind": "app_method",
                 "callers": [], "manifest_refs": []}
    if "." not in query:
        out["error"] = "expected app.method or emit:event:name"
        out["error_code"] = "invalid_args"
        return out
    tgt_app, tgt_meth = query.split(".", 1)

    # depth-1: method-precise direct callers, each with its file:line site
    direct: set[str] = set()
    for a in apps.values():
        for aid, meth, where in a.call_app_sites:
            if aid == tgt_app and meth == tgt_meth:
                out["callers"].append({"app": a.app_id, "where": where})
                if aid != a.app_id:
                    direct.add(a.app_id)

    if tgt_app in apps:
        out["target"] = {"app": tgt_app, "method": tgt_meth,
                         "exists": tgt_meth in apps[tgt_app].defined}
        # tgt_app's own manifest naming this method = a contribution root
        for meth, where in manifest_method_refs(apps[tgt_app]):
            if meth == tgt_meth:
                out["manifest_refs"].append({"app": tgt_app, "where": where})
    else:
        out["target"] = {"app": tgt_app, "method": tgt_meth, "exists": False}
        out["error"] = f'app "{tgt_app}" not found'
        out["error_code"] = "not_found"

    # transitive app-level expansion from the depth-1 frontier
    rev = _reverse_app_index(apps)
    seen: set[str] = {tgt_app}
    frontier = set(direct)
    depths: list[dict] = []
    d = 1
    while frontier and d <= max_depth:
        layer = sorted(frontier - seen)
        if not layer:
            break
        depths.append({"depth": d, "apps": layer})
        seen.update(layer)
        nxt: set[str] = set()
        for app_id in layer:
            nxt |= rev.get(app_id, set())
        frontier = nxt - seen
        d += 1
    out["blast_radius"] = depths
    out["affected_apps"] = sorted(seen - {tgt_app})
    out["max_depth_reached"] = depths[-1]["depth"] if depths else 0
    return out


# ─────────────────────────── graph dump ───────────────────────────


def build_graph(apps: dict[str, AppInfo]) -> dict:
    nodes = [{"id": a.app_id, "type": "app",
              "methods": len(a.defined),
              "routes": len(a.decorated.get("web_route", [])),
              "emits": list(a.emits), "listens": list(a.listens)}
             for a in apps.values()]
    edges = []
    ids = set(apps)
    for a in apps.values():
        seen = set()
        for aid, meth, _ in a.call_app_sites:
            key = (a.app_id, aid)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": a.app_id, "target": aid, "type": "call_app",
                          "resolved": aid in ids})
        for ev in a.emits:
            for listener in apps:
                if ev in apps[listener].listens:
                    edges.append({"source": a.app_id, "target": listener,
                                  "type": "event", "event": ev})
    return {"nodes": nodes, "edges": edges,
            "stats": {"apps": len(nodes), "edges": len(edges)}}


# ─────────────────────────── reporting ───────────────────────────


def emit_envelope(ok: bool, code: str, message: str, data: object) -> int:
    """Print the one agent-cli envelope object (.claude/rules/agent-cli.md) and
    return the matching exit code (0 on ok, 1 otherwise). stdout carries only
    this JSON in --json mode — diagnostics go to stderr elsewhere."""
    print(json.dumps({"ok": ok, "code": code, "message": message, "data": data},
                     indent=2))
    return 0 if ok else 1


def print_impact(res: dict) -> None:
    """Human report for --impact (the non-JSON default)."""
    print(f"\nimpact — {res['query']}\n" + "─" * 60)
    if res.get("kind") == "event":
        print(f"  event: {res.get('event')}")
        ems = ", ".join(e["app"] for e in res.get("emitters", [])) or "(none)"
        print(f"  emitters:  {ems}")
        listeners = res.get("listeners", [])
        if listeners:
            print("  listeners:")
            for l in listeners:
                print(f"    [{l['app']}] {', '.join(l['handlers'])}")
        else:
            print("  listeners: (none found — may be a UI WebSocket consumer)")
        for r in res.get("manifest_refs", []):
            print(f"    [{r['app']}] {r['where']}")
    else:  # app_method
        tgt = res.get("target", {})
        mark = "✓ exists" if tgt.get("exists") else "✗ NOT DEFINED"
        print(f"  target: {tgt.get('app')}.{tgt.get('method')}  [{mark}]")
        callers = res.get("callers", [])
        if callers:
            print(f"\n  direct callers (depth 1): {len(callers)}")
            for c in callers:
                print(f"    [{c['app']}] {c['where']}")
        else:
            print("\n  direct callers: (none)")
        for r in res.get("manifest_refs", []):
            print(f"    contribution root: {r['where']}")
        rest = [layer for layer in res.get("blast_radius", []) if layer["depth"] > 1]
        if rest:
            print("\n  transitive callers:")
            for layer in rest:
                print(f"    depth {layer['depth']}: {', '.join(layer['apps'])}")
    if res.get("error"):
        print(f"\n  ! {res['error']}")
    print(f"\n  affected apps: {len(res.get('affected_apps', []))}"
          f"  (max depth {res.get('max_depth_reached', 0)})")


TIER_ORDER = {"high": 0, "medium": 1, "low": 2}


def print_report(findings: list[Finding], apps: dict[str, AppInfo],
                 tier_filter: str | None) -> int:
    shown = [f for f in findings
             if not tier_filter or TIER_ORDER[f.tier] <= TIER_ORDER[tier_filter]]
    shown.sort(key=lambda f: (TIER_ORDER[f.tier], f.kind, f.app))
    by_tier: dict[str, int] = defaultdict(int)
    for f in findings:
        by_tier[f.tier] += 1

    print(f"\ncode_archaeology — {len(apps)} apps scanned\n" + "─" * 60)
    cur = None
    for f in shown:
        if f.tier != cur:
            cur = f.tier
            label = {"high": "HIGH — real bugs (broken dispatch / missing method)",
                     "medium": "MEDIUM — soft-dep / probable issue",
                     "low": "LOW — informational (dynamic, verify by hand)"}[f.tier]
            print(f"\n## {label}")
        print(f"  [{f.app}] {f.kind}: {f.detail}")
        if f.where:
            print(f"        ↳ {f.where}")
    print("\n" + "─" * 60)
    print(f"  HIGH: {by_tier['high']}   MEDIUM: {by_tier['medium']}   "
          f"LOW: {by_tier['low']}")
    if by_tier["high"]:
        print("  ⚠ HIGH findings are broken dynamic dispatch — these fail "
              "silently at runtime.")
    else:
        print("  ✓ no broken dispatch found.")
    return by_tier["high"]


# ─────────────────────────── main ───────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--tier", choices=["high", "medium", "low"],
                    help="only show this tier and above")
    ap.add_argument("--app", help="scope the audit to one app id")
    ap.add_argument("--impact", help="app.method or emit:event:name — who depends?")
    ap.add_argument("--depth", type=int, default=3, metavar="N",
                    help="max transitive depth for --impact blast radius (default 3)")
    ap.add_argument("--graph", metavar="OUT.json", help="dump the reachability graph")
    ap.add_argument("--root", metavar="APPS_DIR",
                    help="scan this apps/ dir instead of this repo's")
    ap.add_argument("--public-only", action="store_true",
                    help="only audit apps under apps/public/ (the release gate "
                         "scope) — targets still resolve against the full tree")
    args = ap.parse_args()

    apps = discover_apps(Path(args.root) if args.root else APPS_ROOT)
    for a in apps.values():
        analyze_app(a)

    if args.impact:
        res = impact(apps, args.impact, max_depth=args.depth)
        if args.json:
            err = res.get("error_code")
            if err:
                return emit_envelope(False, err, res["error"], res)
            n = len(res.get("affected_apps", []))
            md = res.get("max_depth_reached", 0)
            note = ""
            if res.get("kind") == "app_method" and not res.get("target", {}).get("exists"):
                note = " — target method not defined"
            return emit_envelope(
                True, "ok",
                f"{res['query']}: {n} affected app(s) to depth {md}{note}", res)
        print_impact(res)
        return 1 if res.get("error_code") else 0

    if args.graph:
        g = build_graph(apps)
        Path(args.graph).write_text(json.dumps(g, indent=2), encoding="utf-8")
        print(f"graph → {args.graph}  ({g['stats']['apps']} apps, "
              f"{g['stats']['edges']} edges)")
        return 0

    if args.app and args.app not in apps:
        print(f"no such app: {args.app}", file=sys.stderr)
        return 2

    findings = run_audit(apps, args.app, public_only=args.public_only)
    if args.json:
        out = [f.as_dict() for f in findings
               if not args.tier or TIER_ORDER[f.tier] <= TIER_ORDER[args.tier]]
        print(json.dumps({"apps_scanned": len(apps), "findings": out}, indent=2))
        return sum(1 for f in findings if f.tier == "high")

    return print_report(findings, apps, args.tier)


if __name__ == "__main__":
    sys.exit(main())
