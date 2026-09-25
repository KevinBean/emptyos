"""Every proactive kind an app can push must be registered in the mute catalog.

A kind missing from `apps/public/standard/proactive/app.py::KINDS` still
delivers — nothing validates it on the way out. What it loses is the OFF
switch: the catalog is what the policy page renders, and `POST /api/mute`
refuses a kind it does not list. So an unregistered kind is a notification the
user receives and cannot silence.

Measured 2026-09-16: 22 emitted kinds were missing, `expense-budget` among them
with 251 deliveries in the audit log. The catalog had drifted in BOTH
directions, which is why this reports each separately.

Detection is deliberately narrow — only two shapes count as an emitter:

1. `proactive_notify("<kind>", …)` / any call whose name ends in `notify`
   carrying a literal `kind="<kind>"`. The second half is what sees a
   FORWARDER: reactor routes five kinds through its own
   `_notify(msg, *, kind=…)` wrapper, and a scan keyed on the two SDK function
   names alone reported 0 emitted for the whole app — so a new reaction could
   reproduce the original 22-kind defect with this gate green. Measured
   2026-09-16: every `*notify*(…, kind=…)` call in the tree is a real nudge, so
   the widening costs no false positives.
   A computed kind (`kind=PROACTIVE_KIND`) cannot be resolved statically and is
   skipped rather than guessed at.
2. a dict carrying both `"kind"` and `"text"` inside a `proactive_source*`
   method — the pull-source contract.

Two paths remain invisible by construction, and the gate does NOT cover them:
`plugins/telegram` calls `proactive.decide(kind=PROACTIVE_KIND)` directly with a
module constant, and `POST /proactive/api/test` takes its kind from the request
body. Both are single known kinds registered in the catalog; a scan cannot
follow either.

A looser first cut counted any `"kind":` key in any file mentioning proactive
and reported 40+, most of them unrelated dict rows (board columns, analytics
events). Widening this scan re-introduces that noise.

Exit code = number of unregistered kinds (gate). Registered-but-unemitted is
reported as a note: a kind can legitimately be declared before its sender
ships, and several here are emitted through wrappers this scan cannot see.

    python scripts/check_proactive_kinds.py [--json]
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "apps" / "public" / "standard" / "proactive" / "app.py"
PUSH_FUNCS = {"proactive_notify", "proactive_notify_or_raw"}
SEARCH_ROOTS = ("apps", "plugins", "emptyos")
SKIP_PARTS = {"_retired", "__pycache__", "node_modules", "dist"}


def _iter_py(root: Path):
    for base in SEARCH_ROOTS:
        d = root / base
        if not d.is_dir():
            continue
        for p in d.rglob("*.py"):
            if SKIP_PARTS & set(p.parts):
                continue
            yield p


def registered_kinds(catalog: Path = CATALOG) -> dict[str, str]:
    """Parse KINDS out of the proactive app. It is an annotated assignment
    (`KINDS: dict[str, str] = {...}`), which is an AnnAssign — reading only
    ast.Assign returns an empty map and every kind then looks unregistered."""
    if not catalog.is_file():
        return {}
    tree = ast.parse(catalog.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "KINDS":
            target = node.value
        elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "KINDS" for t in node.targets):
            target = node.value
        if isinstance(target, ast.Dict):
            return {k.value: getattr(v, "value", "")
                    for k, v in zip(target.keys, target.values)
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return {}


def emitted_kinds(root: Path = REPO) -> dict[str, list[str]]:
    """kind -> ["file:line", …] for every statically resolvable emitter."""
    found: dict[str, list[str]] = {}

    def add(kind, path: Path, line: int):
        if isinstance(kind, str) and kind:
            rel = str(path.relative_to(root)).replace("\\", "/")
            found.setdefault(kind, []).append(f"{rel}:{line}")

    for path in _iter_py(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name in PUSH_FUNCS:
                    if node.args and isinstance(node.args[0], ast.Constant):
                        add(node.args[0].value, path, node.lineno)
                if name in PUSH_FUNCS or name.endswith("notify"):
                    for kw in node.keywords:
                        if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                            add(kw.value.value, path, node.lineno)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name.startswith("proactive_source"):
                for d in ast.walk(node):
                    if not isinstance(d, ast.Dict):
                        continue
                    keys = {k.value for k in d.keys
                            if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                    if {"kind", "text"} <= keys:
                        for k, v in zip(d.keys, d.values):
                            if isinstance(k, ast.Constant) and k.value == "kind" \
                                    and isinstance(v, ast.Constant):
                                add(v.value, path, d.lineno)
    return found


def scan(root: Path = REPO) -> dict:
    catalog = registered_kinds(root / "apps" / "public" / "standard" / "proactive" / "app.py")
    emitters = emitted_kinds(root)
    # The catalog file declares kinds; it is not an emitter of them.
    emitters = {k: v for k, v in emitters.items()
                if not all("proactive/app.py" in loc for loc in v)}
    unregistered = {k: sorted(v) for k, v in emitters.items() if k not in catalog}
    unemitted = sorted(k for k in catalog if k not in emitters)
    return {"registered": len(catalog), "emitted": len(emitters),
            "unregistered": unregistered, "unemitted": unemitted}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    # scan(REPO), not scan(): a default bound at def time cannot be redirected,
    # so main() — the half that decides whether the gate fires — would be
    # untestable against a fixture tree (.claude/rules/audits.md).
    result = scan(REPO)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result["unregistered"]:
            print(f"check-proactive-kinds: {len(result['unregistered'])} kind(s) pushed but "
                  "not in KINDS — they deliver and cannot be muted:")
            for kind, locs in sorted(result["unregistered"].items()):
                print(f"  {kind:28} {locs[0]}" + (f" (+{len(locs) - 1} more)" if len(locs) > 1 else ""))
            print("  fix: add each to KINDS in apps/public/standard/proactive/app.py")
        else:
            print(f"check-proactive-kinds: OK — {result['registered']} registered, "
                  f"{result['emitted']} emitted, every emitted kind is mutable")
        if result["unemitted"]:
            print(f"  note: {len(result['unemitted'])} registered with no statically "
                  f"visible emitter (wrapper-sent or not shipped yet): "
                  f"{', '.join(result['unemitted'])}")
    return len(result["unregistered"])


if __name__ == "__main__":
    sys.exit(main())
