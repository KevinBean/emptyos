"""Use-case UI-walk report renderer — turns a hand-kept step-log into a
self-contained, screenshot-backed HTML report.

This is the artifact half of the `eos-ui-walk` skill's *act-as-the-user* mode:
the agent drives real EmptyOS workflows in a live browser (as Kevin), captures a
screenshot at each meaningful step, and appends one JSONL record per step to a
step-log. This script reads that step-log, base64-embeds every screenshot, and
renders one shareable HTML file grouped by use case.

It is intentionally NOT a Playwright walker (that's ui_walk_audit.py / the
check-*.py scanners). It does no browser work, imports no kernel, and only reads
files + writes HTML — so it's safe to run anytime, against any step-log.

Step-log format — one JSON object per line:
  {"usecase": "Capture a task and see it land in Today",   # groups steps
   "step": 1,                                              # order within a use case
   "action": "Open /hub/ and read the digest",            # what I (as Kevin) did
   "status": "pass",                                       # pass|slow|confusing|fail|missing|skipped|info
   "note": "Loads <1s; Today shows 3 tasks",              # human judgment / friction
   "shot": "D:/emptyos/data/ui-walk/usecases/uc1-s1.png", # screenshot path (any abs/rel path), optional
   "gif": "D:/emptyos/data/ui-walk/usecases/uc1-fail.gif",# optional replay GIF (fail/confusing steps)
   "ms": 4200,                                             # optional measured duration (evidence for `slow`)
   "console": ["TypeError: x is undefined (hub.js:120)"],  # optional console errors (fail-context bundle)
   "network": ["GET /task/api/list -> 500"],              # optional failed requests (fail-context bundle)
   "url": "http://127.0.0.1:9000/hub/"}                    # optional, shown as a chip

`shot` and `gif` are resolved relative to --shots-base when not absolute; a
missing file renders a placeholder rather than failing the whole report. The
`gif` field carries the GIF-on-failure replay clip (assembled by
ui_walk_gif.py, or captured natively by claude-in-chrome's gif_creator) and is
rendered below the still, autoplaying.

Usage:
  python scripts/ui_walk_report.py --steplog data/ui-walk/usecases/steplog.jsonl \
      --out data/ui-walk/usecases/report.html --title "UI walk — 2026-06-17" --persona Kevin

Output lives under data/ (gitignored) — it's an artifact to show the user, not
committed. Open the HTML directly in a browser.
"""
from __future__ import annotations

import argparse
import html
import json
import time
from collections import OrderedDict
from pathlib import Path

from _eos_browser import embed_image_data_uri

REPO = Path(__file__).resolve().parent.parent

# worst-first ordering for use-case roll-up + status badges
STATUS_RANK = {"fail": 0, "missing": 1, "confusing": 2, "slow": 3, "skipped": 4, "info": 5, "pass": 6}
STATUS_COLOR = {
    "fail": "#e5484d",
    "missing": "#a371f7",
    "confusing": "#f5a623",
    "slow": "#f5d90a",
    "skipped": "#8b8b8b",
    "info": "#5b8def",
    "pass": "#46a758",
}
STATUS_LABEL = {
    "fail": "BROKEN",
    "missing": "GAP",
    "confusing": "CONFUSING",
    "slow": "SLOW",
    "skipped": "SKIPPED",
    "info": "INFO",
    "pass": "OK",
}


def _norm_status(s: str) -> str:
    s = (s or "").strip().lower()
    return s if s in STATUS_RANK else "info"


def _load_steplog(path: Path) -> list[dict]:
    rows: list[dict] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:  # noqa: BLE001 — tolerate a half-written tail line
            continue
    return rows


def _embed_shot(shot: str | None, shots_base: Path) -> str | None:
    """Resolve a screenshot path (absolute / relative-to-base / basename fallback)
    and return a base64 data: URI, or None if missing. Read+encode is delegated to
    the shared ``embed_image_data_uri`` (also used by ui_walk_audit.py)."""
    if not shot:
        return None
    p = Path(shot)
    if not p.is_absolute():
        p = shots_base / shot
    if not p.exists():
        p = shots_base / Path(shot).name  # MCP often saves by bare filename
    return embed_image_data_uri(p)


def _badge(status: str) -> str:
    c = STATUS_COLOR.get(status, "#8b8b8b")
    lbl = STATUS_LABEL.get(status, status.upper())
    return f'<span class="badge" style="background:{c}">{html.escape(lbl)}</span>'


def render(rows: list[dict], *, title: str, persona: str, shots_base: Path) -> str:
    # group by use case, preserve first-seen order, sort steps within
    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in rows:
        uc = str(r.get("usecase") or "Ungrouped")
        groups.setdefault(uc, []).append(r)
    for uc in groups:
        groups[uc].sort(key=lambda r: (r.get("step") or 0))

    # roll-up counts
    counts: dict[str, int] = {k: 0 for k in STATUS_RANK}
    for r in rows:
        counts[_norm_status(r.get("status", ""))] += 1
    n_uc = len(groups)
    n_steps = len(rows)

    # worst status per use case (drives sort: worst-first)
    def uc_worst(steps: list[dict]) -> str:
        return min((_norm_status(s.get("status", "")) for s in steps),
                   key=lambda s: STATUS_RANK[s], default="info")

    ordered_uc = sorted(groups.items(), key=lambda kv: STATUS_RANK[uc_worst(kv[1])])

    parts: list[str] = []
    parts.append(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
       background:#0e0f12; color:#e6e6e6; }}
.wrap {{ max-width:980px; margin:0 auto; padding:28px 20px 80px; }}
h1 {{ font-size:24px; margin:0 0 4px; }}
.sub {{ color:#9aa0a6; margin:0 0 20px; font-size:13px; }}
.summary {{ display:flex; gap:8px; flex-wrap:wrap; margin:0 0 28px; }}
.pill {{ padding:6px 12px; border-radius:999px; background:#1a1c20; font-size:13px;
         border:1px solid #2a2d33; }}
.pill b {{ font-weight:700; }}
.uc {{ background:#15171b; border:1px solid #24272d; border-radius:12px;
       margin:0 0 18px; overflow:hidden; }}
.uc-head {{ display:flex; align-items:center; gap:10px; padding:14px 16px;
           border-bottom:1px solid #24272d; background:#181a1f; }}
.uc-head h2 {{ font-size:16px; margin:0; flex:1; }}
.badge {{ color:#0b0c0e; font-weight:800; font-size:10px; letter-spacing:.04em;
          padding:3px 8px; border-radius:5px; white-space:nowrap; }}
.step {{ display:flex; gap:16px; padding:16px; border-bottom:1px solid #1f2227; }}
.step:last-child {{ border-bottom:0; }}
.step-body {{ flex:1; min-width:0; }}
.step-action {{ font-weight:600; margin:0 0 4px; }}
.step-meta {{ display:flex; gap:8px; align-items:center; margin:0 0 6px; flex-wrap:wrap; }}
.step-n {{ color:#6b7077; font-size:12px; font-variant-numeric:tabular-nums; }}
.step-note {{ color:#c4c8cd; font-size:14px; white-space:pre-wrap; margin:0; }}
.url {{ color:#7aa2f7; font-size:12px; text-decoration:none; word-break:break-all; }}
.shot {{ flex:0 0 320px; }}
.shot img {{ width:320px; max-width:42vw; border-radius:8px; border:1px solid #2a2d33;
            display:block; cursor:zoom-in; }}
.shot .missing {{ width:320px; max-width:42vw; height:120px; border-radius:8px;
                 border:1px dashed #3a3d44; display:flex; align-items:center;
                 justify-content:center; color:#6b7077; font-size:12px; }}
.shot .replay {{ margin-top:8px; }}
.shot .replay-label {{ color:#9aa0a6; font-size:11px; letter-spacing:.04em;
                      text-transform:uppercase; margin:0 0 3px; }}
.ms {{ color:#f5d90a; font-size:12px; font-variant-numeric:tabular-nums; }}
.ctx {{ margin:8px 0 0; }}
.ctx summary {{ cursor:pointer; color:#9aa0a6; font-size:12px; letter-spacing:.03em;
               text-transform:uppercase; user-select:none; }}
.ctx pre {{ margin:6px 0 0; padding:10px 12px; background:#101216; border:1px solid #2a2d33;
           border-radius:8px; font:12px/1.5 ui-monospace,Consolas,monospace;
           color:#e8a2a6; white-space:pre-wrap; word-break:break-all; }}
.ctx.net pre {{ color:#9fb6e8; }}
@media (max-width:680px) {{ .step {{ flex-direction:column; }} .shot img,.shot .missing {{ width:100%; max-width:100%; }} }}
</style></head><body><div class="wrap">""")

    parts.append(f"<h1>{html.escape(title)}</h1>")
    parts.append(f'<p class="sub">Walked as <b>{html.escape(persona)}</b> · '
                 f'{n_uc} use case(s), {n_steps} step(s) · '
                 f'{time.strftime("%Y-%m-%d %H:%M")}</p>')

    # summary pills (only non-zero, worst-first)
    parts.append('<div class="summary">')
    for st in sorted(counts, key=lambda s: STATUS_RANK[s]):
        if counts[st]:
            c = STATUS_COLOR[st]
            parts.append(f'<span class="pill"><b style="color:{c}">{counts[st]}</b> '
                         f'{html.escape(STATUS_LABEL[st].title())}</span>')
    parts.append("</div>")

    for uc, steps in ordered_uc:
        worst = uc_worst(steps)
        parts.append('<section class="uc">')
        parts.append('<div class="uc-head">'
                     f'<h2>{html.escape(uc)}</h2>{_badge(worst)}</div>')
        for s in steps:
            st = _norm_status(s.get("status", ""))
            n = s.get("step")
            action = html.escape(str(s.get("action") or ""))
            note = html.escape(str(s.get("note") or ""))
            url = s.get("url")
            uri = _embed_shot(s.get("shot"), shots_base)
            parts.append('<div class="step"><div class="step-body">')
            ms = s.get("ms")
            ms_chip = (f'<span class="ms">{int(ms):,} ms</span>'
                       if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > 0 else "")
            parts.append('<div class="step-meta">'
                         f'{_badge(st)}<span class="step-n">step {html.escape(str(n))}</span>{ms_chip}</div>')
            parts.append(f'<p class="step-action">{action}</p>')
            if note:
                parts.append(f'<p class="step-note">{note}</p>')
            for key, cls, label in (("console", "ctx", "console errors"), ("network", "ctx net", "failed requests")):
                lines = s.get(key)
                if isinstance(lines, list) and lines:
                    shown = [str(x) for x in lines[:20]]
                    if len(lines) > 20:  # surface the cap — a silent truncation reads as "everything"
                        shown.append(f"... +{len(lines) - 20} more (see steplog.jsonl)")
                    body = html.escape("\n".join(shown))
                    parts.append(f'<details class="{cls}"><summary>{label} ({len(lines)})</summary>'
                                 f'<pre>{body}</pre></details>')
            if url:
                u = html.escape(str(url))
                parts.append(f'<a class="url" href="{u}" target="_blank" rel="noopener">{u}</a>')
            parts.append("</div>")
            parts.append('<div class="shot">')
            if uri:
                parts.append(f'<img src="{uri}" loading="lazy" '
                             f'onclick="window.open(this.src)" alt="screenshot">')
            elif s.get("shot"):
                parts.append('<div class="missing">screenshot not found</div>')
            gif_uri = _embed_shot(s.get("gif"), shots_base)
            if gif_uri:
                parts.append('<div class="replay"><p class="replay-label">&#9654; replay</p>'
                             f'<img src="{gif_uri}" loading="lazy" '
                             f'onclick="window.open(this.src)" alt="replay gif"></div>')
            elif s.get("gif"):
                parts.append('<div class="missing">replay gif not found</div>')
            parts.append("</div></div>")
        parts.append("</section>")

    parts.append("</div></body></html>")
    return "".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a use-case UI-walk step-log into a self-contained HTML report.")
    ap.add_argument("--steplog", required=True, help="path to the JSONL step-log")
    ap.add_argument("--out", default="", help="output HTML path (default: <steplog dir>/report.html)")
    ap.add_argument("--title", default="", help="report title")
    ap.add_argument("--persona", default="Kevin", help="who the walk was performed as")
    ap.add_argument("--shots-base", default="", help="base dir for resolving relative screenshot paths (default: steplog dir)")
    args = ap.parse_args()

    steplog = Path(args.steplog)
    if not steplog.exists():
        print(f"ERROR: step-log not found: {steplog}")
        return 2
    rows = _load_steplog(steplog)
    if not rows:
        print(f"ERROR: step-log is empty: {steplog}")
        return 2

    out = Path(args.out) if args.out else steplog.parent / "report.html"
    shots_base = Path(args.shots_base) if args.shots_base else steplog.parent
    title = args.title or f"EmptyOS UI walk — {time.strftime('%Y-%m-%d')}"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows, title=title, persona=args.persona, shots_base=shots_base), encoding="utf-8")
    print(f"Report -> {out}  ({len(rows)} steps across {len({r.get('usecase') for r in rows})} use cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
