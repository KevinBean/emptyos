#!/usr/bin/env python3
"""Static dataflow audit — build the app dataflow graph from manifests + code
and report every edge anomaly the live /topology graph would surface, but
offline (no daemon).

Dataflow edge classes audited:
  A. calls_app   — self.call_app("X", ...): dangling (X missing) / undeclared
  B. emits_event — self.emit("x:y"): emitted-in-code-not-declared / declared-not-emitted
  C. listens     — @on_event("x"): dangling listener (nobody emits x)
  D. unheard     — emitted (non-internal) with zero listeners
  E. capability  — self.<cap>(...) used in code but not in requires.capabilities
  F. orphans     — app with no inbound and no outbound dataflow edge

Pure ast + tomllib. Usage: python scripts/dataflow_audit.py [--json]
"""
from __future__ import annotations
import ast, json, sys, tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APPS = REPO / "apps"

# BaseApp methods that ARE a capability call (method name -> capability)
CAP_METHODS = {
    "think": "think", "think_stream": "think", "think_compare": "think",
    "search": "search", "speak": "speak", "listen": "listen",
    "pronounce": "pronounce", "draw": "draw", "animate": "animate",
    "model": "model", "artifact": "artifact", "see": "see",
    "browse": "browse", "send": "send", "footage": "footage",
    "translate": "translate",
}


def load_manifests() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for mf in APPS.rglob("manifest.toml"):
        if any(p.startswith("_") for p in mf.parts):
            continue
        try:
            m = tomllib.load(open(mf, "rb"))
        except Exception:
            continue
        aid = (m.get("app") or {}).get("id")
        if not aid:
            continue
        req = m.get("requires") or {}
        prov = (m.get("provides") or {}).get("events") or {}
        out[aid] = {
            "dir": mf.parent,
            "apps": set(req.get("apps") or []) | set(req.get("optional_apps") or []),
            "caps": set(req.get("capabilities") or []),
            "emits": set(prov.get("emits") or []),
            "listens": set(prov.get("listens") or []) | set(req.get("events") or []),
            "internal": set(prov.get("internal") or []),
            "contributes": set((m.get("contributes") or {}).keys()),
        }
    return out


def scan_code(d: Path):
    """Yield (kind, value, loc) for call_app/emit/on_event/cap-method in a dir."""
    for py in d.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            continue
        loc_base = "/".join(py.parts[-2:])
        for n in ast.walk(tree):
            # decorators @on_event("x")
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in n.decorator_list:
                    if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                            and dec.func.id == "on_event" and dec.args
                            and isinstance(dec.args[0], ast.Constant)):
                        yield "listen", dec.args[0].value, f"{loc_base}:{dec.lineno}"
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                attr = n.func.attr
                # Receiver must be the BaseApp — `self`, a helper's `app` param,
                # or `self.app`. Excludes `notif.send()`, `re.search()`,
                # `self.kernel.events.emit()` (receiver attr is events/kernel).
                recv = n.func.value
                rn = recv.id if isinstance(recv, ast.Name) else (
                    recv.attr if isinstance(recv, ast.Attribute) else None)
                if rn not in ("self", "app"):
                    continue
                if attr == "call_app" and n.args and isinstance(n.args[0], ast.Constant):
                    yield "call", n.args[0].value, f"{loc_base}:{n.lineno}"
                elif attr == "emit" and n.args and isinstance(n.args[0], ast.Constant):
                    yield "emit", n.args[0].value, f"{loc_base}:{n.lineno}"
                elif attr in CAP_METHODS:
                    yield "cap", CAP_METHODS[attr], f"{loc_base}:{n.lineno}"


def audit() -> dict:
    mans = load_manifests()
    ids = set(mans)
    code: dict[str, dict] = {}
    for aid, info in mans.items():
        c = {"call": {}, "emit": {}, "listen": {}, "cap": {}}
        for kind, val, loc in scan_code(info["dir"]):
            c[kind].setdefault(val, []).append(loc)
        code[aid] = c

    findings = {k: [] for k in
                ("call_dangling", "call_undeclared", "emit_undeclared",
                 "emit_declared_unused", "listen_dangling", "unheard",
                 "cap_undeclared", "orphans")}

    # all emitted events (code ∪ manifest), all listened (code ∪ manifest)
    all_emit, all_listen = {}, {}
    for aid in ids:
        for e in set(code[aid]["emit"]) | mans[aid]["emits"]:
            all_emit.setdefault(e, set()).add(aid)
        for e in set(code[aid]["listen"]) | mans[aid]["listens"]:
            all_listen.setdefault(e, set()).add(aid)
    internal = set().union(*(m["internal"] for m in mans.values())) if mans else set()

    inbound, outbound = {a: 0 for a in ids}, {a: 0 for a in ids}

    for aid in sorted(ids):
        m, c = mans[aid], code[aid]
        declared = m["apps"] | {aid}
        # A. call_app
        for tgt, locs in c["call"].items():
            if tgt not in ids:
                findings["call_dangling"].append({"app": aid, "target": tgt, "loc": locs[0]})
            else:
                outbound[aid] += 1
                inbound[tgt] = inbound.get(tgt, 0) + 1
                if tgt not in declared:
                    findings["call_undeclared"].append({"app": aid, "target": tgt, "loc": locs[0]})
        # contributes edges count as outbound/inbound
        for tgt in m["contributes"]:
            if tgt in ids and tgt != aid:
                outbound[aid] += 1; inbound[tgt] = inbound.get(tgt, 0) + 1
        for tgt in m["apps"]:
            if tgt in ids:
                outbound[aid] += 1; inbound[tgt] = inbound.get(tgt, 0) + 1
        # B. emit declaration consistency
        for e in c["emit"]:
            if e not in m["emits"] and e not in m["internal"]:
                findings["emit_undeclared"].append({"app": aid, "event": e, "loc": c["emit"][e][0]})
        for e in m["emits"]:
            if e not in c["emit"]:
                findings["emit_declared_unused"].append({"app": aid, "event": e})
        # E. capability declaration
        for cap in c["cap"]:
            if cap not in m["caps"]:
                findings["cap_undeclared"].append({"app": aid, "cap": cap, "loc": c["cap"][cap][0]})

    # C. dangling listeners (listen to an event nobody emits)
    for evt, listeners in all_listen.items():
        if evt not in all_emit:
            for aid in sorted(listeners):
                findings["listen_dangling"].append({"app": aid, "event": evt})
            for aid in listeners:
                inbound[aid] += 1  # still a wired-in edge from the bus
    # event edges: emit→listen contributes to in/outbound
    for evt, emitters in all_emit.items():
        listeners = all_listen.get(evt, set())
        for a in emitters:
            if listeners - {a}:
                outbound[a] += 1
        for a in listeners:
            if emitters - {a}:
                inbound[a] += 1
        # D. unheard
        if evt not in internal and not (listeners - emitters):
            findings["unheard"].append({"event": evt, "emitters": sorted(emitters)})

    # F. orphans
    for aid in sorted(ids):
        if inbound.get(aid, 0) == 0 and outbound.get(aid, 0) == 0:
            findings["orphans"].append({"app": aid})

    return {"app_count": len(ids), "findings": findings}


def main() -> int:
    res = audit()
    f = res["findings"]
    if "--json" in sys.argv:
        print(json.dumps(res, indent=2)); return 0
    print(f"=== Dataflow audit — {res['app_count']} apps ===\n")
    order = [
        ("call_dangling", "A. DANGLING call_app (runtime AttributeError)"),
        ("call_undeclared", "A. UNDECLARED call_app edge (manifest debt)"),
        ("emit_undeclared", "B. emit() in code, not declared in manifest"),
        ("emit_declared_unused", "B. emit declared in manifest, never emitted in code"),
        ("listen_dangling", "C. DANGLING listener (@on_event for event nobody emits)"),
        ("unheard", "D. UNHEARD event (emitted, zero listeners)"),
        ("cap_undeclared", "E. capability used in code, not in requires.capabilities"),
        ("orphans", "F. ORPHAN app (no inbound/outbound dataflow edge)"),
    ]
    for key, title in order:
        items = f[key]
        print(f"\n## {title} — {len(items)}")
        for it in items[:80]:
            print("   " + json.dumps(it, ensure_ascii=False))
        if len(items) > 80:
            print(f"   ... +{len(items) - 80} more")
    # Final one-line summary (preflight tails the last line). Exit code = the
    # hard floor: dangling call_app (guaranteed runtime AttributeError). The
    # declaration-debt categories are reported but advisory.
    debt = (len(f["call_undeclared"]) + len(f["emit_undeclared"]) + len(f["cap_undeclared"]))
    hard = len(f["call_dangling"])
    print(f"\nSUMMARY: {hard} dangling call_app (hard) · {debt} declaration-debt "
          f"({len(f['call_undeclared'])} call / {len(f['emit_undeclared'])} emit / "
          f"{len(f['cap_undeclared'])} cap) · {len(f['listen_dangling'])} dangling listeners")
    return hard


if __name__ == "__main__":
    raise SystemExit(main())
