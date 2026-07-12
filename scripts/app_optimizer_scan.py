#!/usr/bin/env python3
"""scripts/app_optimizer_scan.py — whole-system app-quality scorer (App Optimizer).

Scores every active app on 8 dimensions (0-15 each, 0-120 total) from grep-able
static signals, so an optimizer pass can pick the worst-balanced apps without
reading ~200 app.py files. Heuristic ('counted' grade), calibrated against known
apps (assistant/rooms -> A, kb/projects -> A-, search -> honest C). VERIFY a
finding against the real code before acting on it (.claude/rules/audits.md): the
D/F tail is mostly intentional chrome, and AI=0 is correct for deterministic
calculators. Graduated from a session scratchpad per .claude/rules/self-audit-loops.md.

8 dimensions: backend · frontend · ai · collab · vault · innov · data · ux.

Modes:
    python scripts/app_optimizer_scan.py          # score + write dated snapshot
                                                  #   + scorecard-latest.json to the vault
    python scripts/app_optimizer_scan.py --check  # score + print summary only, NO writes
                                                  #   (preflight / advisory mode)
    python scripts/app_optimizer_scan.py --json   # one {ok,code,message,data} envelope line
    python scripts/app_optimizer_scan.py --out DIR# override the output directory

Default output (vault, private — the table lists personal app names):
    {vault}/30_Resources/EmptyOS/app-optimizer/outputs/YYYY-MM-DD-scorecard.md
    {vault}/30_Resources/EmptyOS/app-optimizer/scorecard-latest.json

Exit code = number of F-grade apps (exit-code-as-signal; advisory). Pure file I/O,
no kernel import — safe to run while the daemon is up.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_base import REPO_ROOT  # noqa: E402
from kb_paths import vault_root  # noqa: E402

EXCLUDE = ("_retired", "_example", "_catalog")
DIMS = ["backend", "frontend", "ai", "collab", "vault", "innov", "data", "ux"]


def app_dirs() -> list[Path]:
    out = []
    for mf in REPO_ROOT.glob("apps/**/manifest.toml"):
        if any(x in mf.parts for x in EXCLUDE):
            continue
        out.append(mf.parent)
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _count(pat: str, text: str) -> int:
    return len(re.findall(pat, text))


def _bucket(v: int, table: list[tuple[int, int]]) -> int:
    """table = ascending (threshold, score); returns score for largest threshold <= v."""
    s = table[0][1]
    for t, sc in table:
        if v >= t:
            s = sc
    return s


def grade(t: int) -> str:
    return (
        "A" if t >= 96 else "A-" if t >= 84 else "B+" if t >= 72 else "B" if t >= 60
        else "C" if t >= 40 else "D" if t >= 20 else "F"
    )


def score_app(d: Path) -> dict:
    p = d.as_posix()
    track = (
        "public" if "/public/" in p else "extension" if "/extension/" in p
        else "personal" if "/personal/" in p else "other"
    )
    man = {}
    try:
        man = tomllib.loads(_read(d / "manifest.toml"))
    except Exception:
        pass
    app = man.get("app", {})
    aid = app.get("id", d.name)
    name = app.get("name", aid)
    cat = app.get("store_category", "other")
    req = man.get("requires", {})
    caps = set(req.get("capabilities", []) or [])
    app_deps = (req.get("apps", []) or []) + (req.get("optional_apps", []) or [])
    prov = man.get("provides", {})
    emits_man = (prov.get("events", {}) or {}).get("emits", []) or []
    has_timeline = "timeline" in prov
    has_field_suggest = "field_suggest" in prov
    contributes = man.get("contributes", {})
    hub_panel = bool(contributes.get("hub", {}).get("panel"))
    voice_intent = bool(contributes.get("voice-assistant", {}).get("intent"))

    code = "\n".join(_read(f) for f in d.glob("*.py"))
    loc = code.count("\n") + 1 if code else 0
    routes = _count(r"@web_route\(", code)
    ws = _count(r"@ws_route\(", code)
    think_any = (
        _count(r"self\.think\(", code)
        + _count(r"self\.think_stream\(", code)
        + _count(r"self\.think_compare\(", code)
    )
    think_stream = _count(r"self\.think_stream\(", code)
    call_app = _count(r"self\.call_app\(", code)
    emit = _count(r"self\.emit\(", code)
    listen = _count(r"@on_event\(", code)
    vault = _count(r"self\.vault_\w+\(", code)
    # save_calculation/list_calculations are SDK-abstracted vault persistence —
    # credit them as real vault+data (a raw self.vault_* count would miss them).
    calc_persist = _count(r"self\.save_calculation\(|self\.list_calculations\(", code)
    vault += calc_persist * 2
    vault_write = _count(
        r"self\.vault_write\(|self\.vault_create_note\(|self\.vault_append"
        r"|self\.vault_set_section\(|self\.vault_update\(",
        code,
    )
    if _count(r"self\.save_calculation\(", code):
        vault_write += 1
    multimodal = sum(
        1 for c in ("draw", "speak", "listen", "see", "browse", "animate", "model")
        if re.search(r"self\.%s\(" % c, code)
    )
    slash = "SLASH_COMMANDS" in code
    log_activity = "log_activity" in code or ".jsonl" in code
    data_dir = "self.data_dir" in code or "json.dump" in code or "_save_json" in code
    export_route = bool(re.search(r"/api/export|/export", code)) or "[provides.export]" in _read(d / "manifest.toml")
    field_suggest_code = "suggest_field" in code

    pages = d / "pages"
    has_custom = (pages / "index.html").exists()
    page_bytes = 0
    page_txt = ""
    if pages.exists():
        for f in pages.rglob("*"):
            if f.is_file() and f.suffix in (".html", ".js", ".css"):
                t = _read(f)
                page_bytes += len(t)
                page_txt += t
    eos_ui = _count(r"EOS_UI\.", page_txt)
    heatmap = "yearHeatmap" in page_txt or "monthGrid" in page_txt or "heatmap" in page_txt.lower()
    streak = any(k in page_txt.lower() for k in ("streak", "achievement", "badge"))
    media_q = "@media" in page_txt
    toast = "toast" in page_txt.lower()
    hashroute = "hashRoute" in page_txt

    s_backend = _bucket(routes, [(0, 0), (1, 4), (3, 7), (5, 10), (10, 13), (15, 15)])
    if not has_custom:
        s_frontend = 3 if routes > 0 else 0
    else:
        s_frontend = _bucket(page_bytes, [(0, 5), (3000, 8), (10000, 11), (30000, 13)])
        if eos_ui >= 5:
            s_frontend = min(15, s_frontend + 2)
    s_ai = _bucket(think_any, [(0, 0), (1, 6), (3, 9), (6, 12), (11, 14)])
    if think_stream and think_any >= 6:
        s_ai = 15
    c = (
        min(5, call_app) + min(3, emit) + min(4, int(listen * 1.5))
        + (3 if hub_panel else 0) + (2 if voice_intent else 0) + min(2, len(emits_man))
    )
    s_collab = min(15, c)
    s_vault = _bucket(vault, [(0, 0), (1, 4), (3, 8), (7, 11), (13, 14)])
    if vault_write and s_vault < 15:
        s_vault = min(15, s_vault + 1)
    i = (
        (4 if ws else 0) + (3 if slash else 0) + min(4, multimodal * 2)
        + (2 if has_field_suggest or field_suggest_code else 0) + (2 if has_timeline else 0)
    )
    if has_custom:
        i += 3
    s_innov = min(15, i)
    dd = 0
    if vault_write:
        dd += 5
    if data_dir:
        dd += 4
    if log_activity:
        dd += 3
    if export_route:
        dd += 2
    if dd == 0 and routes > 0:
        dd = 2
    s_data = min(15, dd)
    u = (
        min(6, eos_ui) + (2 if heatmap else 0) + (2 if streak else 0)
        + (2 if media_q else 0) + (2 if toast else 0) + (1 if hashroute else 0)
    )
    s_ux = min(15, u)

    dims = dict(
        backend=s_backend, frontend=s_frontend, ai=s_ai, collab=s_collab,
        vault=s_vault, innov=s_innov, data=s_data, ux=s_ux,
    )
    total = sum(dims.values())
    return dict(
        id=aid, name=name, track=track, cat=cat, dir=p, loc=loc,
        routes=routes, think=think_any, call_app=call_app, emit=emit,
        listen=listen, vault=vault, has_page=has_custom, page_kb=round(page_bytes / 1024, 1),
        eos_ui=eos_ui, caps=sorted(caps), deps=app_deps,
        **{f"d_{k}": v for k, v in dims.items()},
        total=total,
        zeros=sum(1 for v in dims.values() if v == 0),
        lows=sum(1 for v in dims.values() if v < 6),
    )


def scan() -> list[dict]:
    rows = [score_app(d) for d in app_dirs()]
    rows.sort(key=lambda r: r["total"])
    return rows


def _summary(rows: list[dict]) -> dict:
    gc = Counter(grade(r["total"]) for r in rows)
    avgs = {dn: round(sum(r[f"d_{dn}"] for r in rows) / len(rows), 1) for dn in DIMS}
    return {
        "apps": len(rows),
        "grades": {g: gc.get(g, 0) for g in ["A", "A-", "B+", "B", "C", "D", "F"]},
        "dim_avg": avgs,
        "f_count": gc.get("F", 0),
    }


def render_markdown(rows: list[dict], today: str) -> str:
    s = _summary(rows)
    out = [
        "---",
        f"title: App Optimizer scorecard {today}",
        "tags:",
        "  - app-optimizer",
        "author: ai",
        "lifecycle: snapshot",
        f"as_of: {today}",
        "---",
        "",
        f"# App Optimizer — scorecard ({today})",
        "",
        "Static 8-dimension scan (0-15 each, 0-120 total). Heuristic ('counted') —",
        "verify before acting. Re-run: `python scripts/app_optimizer_scan.py`.",
        "",
        f"**{s['apps']} active apps scored.**",
        "",
        "## System dimension averages (/15)",
        "",
        "| " + " | ".join(DIMS) + " |",
        "|" + "---|" * len(DIMS),
        "| " + " | ".join(str(s["dim_avg"][d]) for d in DIMS) + " |",
        "",
        "## Grade distribution",
        "",
        " · ".join(f"{g}:{n}" for g, n in s["grades"].items()),
        "",
        "## All apps (high to low)",
        "",
        "| Grade | Total | App | Track | B | F | AI | C | V | I | D | U | 0s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: -r["total"]):
        out.append(
            f"| {grade(r['total'])} | {r['total']} | {r['id']} | {r['track']} | "
            f"{r['d_backend']} | {r['d_frontend']} | {r['d_ai']} | {r['d_collab']} | "
            f"{r['d_vault']} | {r['d_innov']} | {r['d_data']} | {r['d_ux']} | {r['zeros']} |"
        )
    out += [
        "",
        "## Worst-balanced (most zero dims, then lowest total) — top 25",
        "",
        "> Triage before building: most of this tail is intentional chrome or "
        "by-design (AI=0 is correct for deterministic calculators). See the "
        "audit-log front page for the curated next-queue.",
        "",
        "| 0s | Total | App | Track | Cat |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: (-r["zeros"], r["total"]))[:25]:
        out.append(f"| {r['zeros']} | {r['total']} {grade(r['total'])} | {r['id']} | {r['track']} | {r['cat']} |")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="EmptyOS whole-system app-quality scorer")
    ap.add_argument("--check", action="store_true", help="score + print summary only; no writes (preflight mode)")
    ap.add_argument("--json", action="store_true", help="emit a single {ok,code,message,data} envelope")
    ap.add_argument("--out", default="", help="override output directory")
    args = ap.parse_args(argv)

    rows = scan()
    s = _summary(rows)
    today = date.today().isoformat()

    if args.json:
        print(json.dumps({
            "ok": True, "code": "ok",
            "message": f"{s['apps']} apps scored, {s['f_count']} F-grade",
            "data": {"summary": s, "worst_balanced": [
                {"id": r["id"], "total": r["total"], "grade": grade(r["total"]),
                 "zeros": r["zeros"], "track": r["track"]}
                for r in sorted(rows, key=lambda r: (-r["zeros"], r["total"]))[:25]],
            },
        }))
        return s["f_count"]

    # human summary (always printed)
    print(f"App Optimizer scan — {s['apps']} apps")
    print("dim avg /15:", " · ".join(f"{d} {s['dim_avg'][d]}" for d in DIMS))
    print("grades:", " · ".join(f"{g}:{n}" for g, n in s["grades"].items()))
    print("\nworst-balanced (top 15):")
    for r in sorted(rows, key=lambda r: (-r["zeros"], r["total"]))[:15]:
        print(f"  {r['zeros']}z {r['total']:3}{grade(r['total']):>2} {r['id']:22} {r['track']}")

    if not args.check:
        base = Path(args.out) if args.out else (vault_root() / "30_Resources" / "EmptyOS" / "app-optimizer")
        (base / "outputs").mkdir(parents=True, exist_ok=True)
        snap = base / "outputs" / f"{today}-scorecard.md"
        snap.write_text(render_markdown(rows, today), encoding="utf-8")
        (base / "scorecard-latest.json").write_text(
            json.dumps(rows, indent=1), encoding="utf-8"
        )
        print(f"\nwrote {snap}")
        print(f"wrote {base / 'scorecard-latest.json'}")

    return s["f_count"]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
