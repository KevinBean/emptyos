#!/usr/bin/env python3
"""Build the life tech tree from vault ``life-node`` notes.

Reads ``{vault}/30_Resources/EmptyOS/life-tree/`` — ``_tree.md`` for the axes
(fenced ``life-tree-config`` block) and ``nodes/*.md`` for the graph — validates
the DAG, computes node state, and emits two renderings:

- ``--out <path.html>``   a self-contained era x palace tech tree (default)
- ``--canvas``            a board note in the ``canvas`` app codec, for
                          hand-editing layout in an existing surface

Pure stdlib + ``emptyos.frontmatter`` (top-level, kernel-free) so this never
boots the daemon. Exit code is the finding count under ``--validate``.

The astrology guard rail is structural, not decorative: nothing in this file
reads a chart, and no node state is derived from anything but recorded dates and
declared prerequisites. See ``_tree.md`` § "What this is NOT".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from emptyos.frontmatter import parse_frontmatter, strip_frontmatter  # noqa: E402

TREE_REL = "30_Resources/EmptyOS/life-tree"
_CONFIG_FENCE = re.compile(r"```json life-tree-config\s*\n(.*?)\n```", re.DOTALL)

# Precedence: a fired foreclosure > an authored state > a derived one.
AUTHORED = {"done", "active", "missed", "foreclosed"}
DERIVED = {"available", "locked", "fading"}
VALID_STATES = AUTHORED | DERIVED


# ── loading ──────────────────────────────────────────────────────────────────


def vault_root() -> Path:
    cfg = tomllib.loads((REPO / "emptyos.toml").read_text(encoding="utf-8"))
    return Path(cfg["notes"]["path"])


def load_config(tree_dir: Path) -> dict:
    src = (tree_dir / "_tree.md").read_text(encoding="utf-8")
    m = _CONFIG_FENCE.search(src)
    if not m:
        raise SystemExit(f"no ```json life-tree-config block in {tree_dir/'_tree.md'}")
    return json.loads(m.group(1))


def _as_list(v) -> list[str]:
    """Frontmatter lists arrive as list, str, or '' — normalise at the boundary."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip() and v.strip() != "[]":
        return [p.strip() for p in v.split(",") if p.strip()]
    return []


def _prose(body: str) -> str:
    """Body minus the parts that already have their own slot in the panel.

    The note repeats its title as an H1 and its ref as a ``Source:`` line so the
    file reads well on its own in the vault. Rendering those again inside the
    panel is duplication, not content.
    """
    keep = [
        ln for ln in body.strip().split("\n")
        if not ln.startswith("# ") and not ln.startswith("Source: [[")
    ]
    return "\n".join(keep).strip()


def node_from_fields(fm: dict, body: str, *, fallback_id: str, file: Path) -> dict:
    """Frontmatter + body -> a node dict.

    The single field-extraction, shared by both loaders: this script globs the
    folder (kernel-free, so it runs with no daemon), while the app feeds it rows
    from ``vault_query``. Two copies of this mapping would be two parsers, and
    they would disagree about what "available" means before anyone noticed.
    """
    return {
        "id": str(fm.get("id") or fallback_id).strip(),
        "title": str(fm.get("title") or fallback_id).strip(),
        "palace": str(fm.get("palace") or "").strip(),
        "era": str(fm.get("era") or "").strip(),
        "status": str(fm.get("status") or "").strip().lower(),
        "date": str(fm.get("date") or "").strip(),
        "requires": _as_list(fm.get("requires")),
        "forecloses": _as_list(fm.get("forecloses")),
        "ref": str(fm.get("ref") or "").strip(),
        "evidence": str(fm.get("evidence") or "").strip(),
        "inner": str(fm.get("inner") or "").strip(),
        "touched": str(fm.get("touched") or "").strip(),
        "body": _prose(body),
        "_file": file,
    }


def load_nodes(tree_dir: Path) -> list[dict]:
    nodes = []
    for f in sorted((tree_dir / "nodes").glob("*.md")):
        raw = f.read_text(encoding="utf-8")
        fm = parse_frontmatter(raw)
        if "life-node" not in _as_list(fm.get("tags")):
            continue
        nodes.append(
            node_from_fields(fm, strip_frontmatter(raw), fallback_id=f.stem, file=f)
        )
    return nodes


# ── validation ───────────────────────────────────────────────────────────────


def validate(nodes: list[dict], cfg: dict, vault: Path) -> list[str]:
    findings: list[str] = []
    eras = {e["id"] for e in cfg["eras"]}
    palaces = {p["id"] for p in cfg["palaces"]}
    by_id: dict[str, dict] = {}

    for n in nodes:
        if n["id"] in by_id:
            findings.append(f"duplicate id '{n['id']}' ({n['_file'].name})")
        by_id[n["id"]] = n
        if n["era"] not in eras:
            findings.append(f"{n['id']}: unknown era '{n['era']}'")
        if n["palace"] not in palaces:
            findings.append(f"{n['id']}: unknown palace '{n['palace']}'")
        if n["status"] and n["status"] not in VALID_STATES:
            findings.append(f"{n['id']}: unknown status '{n['status']}'")
        if n["status"] in DERIVED:
            findings.append(f"{n['id']}: '{n['status']}' is derived, do not author it")
        if n["date"] and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?(/\d{4}(-\d{2})?)?", n["date"]):
            findings.append(f"{n['id']}: unparseable date '{n['date']}'")
        if n["ref"] and not (vault / n["ref"]).exists():
            findings.append(f"{n['id']}: ref does not resolve — {n['ref']}")

    for n in nodes:
        for dep in n["requires"] + n["forecloses"]:
            if dep not in by_id:
                findings.append(f"{n['id']}: dangling edge -> '{dep}'")

    # cycle check (Kahn); a life DAG with a cycle is an authoring error
    indeg = {i: 0 for i in by_id}
    for n in nodes:
        for dep in n["requires"]:
            if dep in by_id:
                indeg[n["id"]] += 1
    queue = [i for i, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for n in nodes:
            if cur in n["requires"] and n["id"] in indeg:
                indeg[n["id"]] -= 1
                if indeg[n["id"]] == 0:
                    queue.append(n["id"])
    if seen != len(by_id):
        stuck = sorted(i for i, d in indeg.items() if d > 0)
        findings.append(f"cycle in requires among: {', '.join(stuck)}")

    return findings


# ── state ────────────────────────────────────────────────────────────────────


def _months_since(stamp: str, now: date) -> float | None:
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            d = datetime.strptime(stamp.split("/")[0], fmt).date()
        except ValueError:
            continue
        return (now - d).days / 30.44
    return None


def compute_states(nodes: list[dict], cfg: dict) -> dict[str, dict]:
    """Derive status, foreclosure and decay (see AUTHORED for precedence)."""
    by_id = {n["id"]: n for n in nodes}
    now = datetime.strptime(cfg.get("now") or date.today().isoformat(), "%Y-%m-%d").date()
    fade = float(cfg.get("fade_months") or 9)

    state = {n["id"]: dict(n) for n in nodes}

    # A choice that has fired closes what it forecloses.
    closed_by: dict[str, list[str]] = {}
    for n in nodes:
        if n["status"] in ("done", "active"):
            for tgt in n["forecloses"]:
                closed_by.setdefault(tgt, []).append(n["id"])

    for nid, s in state.items():
        s["closed_by"] = closed_by.get(nid, [])
        s["enables"] = sorted(m["id"] for m in nodes if nid in m["requires"])

        # Foreclosure outranks an authored state, including ``done``. Writing
        # `forecloses: [x]` is a claim that this choice *destroys* x, and some of
        # them genuinely do: spending the $50k landing fund un-acquires it. The
        # other order silently swallowed that — the asset stayed "done" while the
        # move it pays for went red, which reads as an unexplained contradiction.
        if s["closed_by"]:
            resolved = "foreclosed"
        elif s["status"] in AUTHORED:
            resolved = s["status"]
        else:
            deps = [by_id[d] for d in s["requires"] if d in by_id]
            resolved = "available" if all(d["status"] == "done" for d in deps) else "locked"

        # 熏習 — a done node left unreinforced decays. Never applies to the past-only
        # branches: a place I lived is not "unpractised", it is finished.
        if resolved == "done":
            stamp = s["touched"] or s["date"]
            age = _months_since(stamp, now) if stamp else None
            if s["touched"] and age is not None and age > fade:
                resolved = "fading"

        s["state"] = resolved

    return state


# ── layout ───────────────────────────────────────────────────────────────────


def layout(state: dict[str, dict], cfg: dict) -> dict[str, dict]:
    """Grid coords: x = era column, y = palace lane + slot within the cell."""
    era_x = {e["id"]: i for i, e in enumerate(cfg["eras"])}
    pal_y = {p["id"]: i for i, p in enumerate(cfg["palaces"])}
    slots: dict[tuple[int, int], int] = {}
    for nid in sorted(state, key=lambda k: (state[k]["date"] or "9999", k)):
        s = state[nid]
        x, lane = era_x.get(s["era"], 0), pal_y.get(s["palace"], 0)
        k = (x, lane)
        slot = slots.get(k, 0)
        slots[k] = slot + 1
        s["pos"] = {"x": x, "lane": lane, "slot": slot}
    for k, count in slots.items():
        for nid, s in state.items():
            if (s["pos"]["x"], s["pos"]["lane"]) == k:
                s["pos"]["of"] = count
    return state


# ── html ─────────────────────────────────────────────────────────────────────


def scenario_delta(nodes: list[dict], cfg: dict, take: list[str]) -> dict:
    """What changes if I do these things now?

    Marks each chosen node ``done`` on a *copy* and recomputes, then diffs. This
    is the forward question the tree exists to answer — and it is honest because
    every edge it walks was declared in a vault note, never inferred from a chart.
    """
    before = compute_states(nodes, cfg)
    clone = [dict(n) for n in nodes]
    chosen = set(take)
    for n in clone:
        if n["id"] in chosen:
            n["status"] = "done"
    after = compute_states(clone, cfg)

    moved = []
    for nid, a in after.items():
        b = before.get(nid)
        if b and b["state"] != a["state"] and nid not in chosen:
            moved.append(
                {
                    "id": nid,
                    "title": a["title"],
                    "palace": a["palace"],
                    "from": b["state"],
                    "to": a["state"],
                }
            )
    order = {"foreclosed": 0, "available": 1, "locked": 2}
    moved.sort(key=lambda m: (order.get(m["to"], 9), m["title"]))
    return {
        "take": sorted(chosen),
        "opened": [m for m in moved if m["to"] == "available"],
        "closed": [m for m in moved if m["to"] == "foreclosed"],
        "moved": moved,
    }


def js_bundle() -> str:
    """Renderer + its stylesheet, as one self-installing script."""
    return _JS.replace("__CSS__", _CSS)


def render_html(state: dict[str, dict], cfg: dict, findings: list[str]) -> str:
    data = json.dumps(graph_payload(state, cfg, findings), ensure_ascii=False)
    return _HTML.replace("__JS__", js_bundle()).replace("__DATA__", data)


def graph_payload(state: dict[str, dict], cfg: dict, findings: list[str]) -> dict:
    """The JSON the renderer consumes — same shape inlined or served."""
    return {
        "cfg": cfg,
        "nodes": [
            {k: v for k, v in s.items() if k not in ("_file", "body", "status")}
            | {"body": s["body"][:600]}
            for s in state.values()
        ],
        "findings": findings,
    }


def render_canvas(state: dict[str, dict], cfg: dict) -> str:
    """A board note in the canvas app codec (## _meta fence + ## n<id> sections)."""
    era_x = {e["id"]: i for i, e in enumerate(cfg["eras"])}
    CW, NW, NH, GAP, PAD = 330, 280, 76, 10, 40

    # Lanes size to their densest cell — a fixed lane height drops 官祿's eight-node
    # 國網 column straight through the 財帛 group below it.
    max_slot = {p["id"]: 1 for p in cfg["palaces"]}
    for s in state.values():
        max_slot[s["palace"]] = max(max_slot.get(s["palace"], 1), s["pos"]["slot"] + 1)
    lane_top, acc = {}, 0
    for p in cfg["palaces"]:
        lane_top[p["id"]] = acc
        acc += max_slot[p["id"]] * (NH + GAP) + PAD

    lay, edges, groups = {}, [], []
    for p in cfg["palaces"]:
        groups.append(
            {
                "id": f"g-{p['id']}",
                "x": -40,
                "y": lane_top[p["id"]] - 24,
                "w": len(cfg["eras"]) * CW + 80,
                "h": max_slot[p["id"]] * (NH + GAP) + PAD - 16,
                "label": f"{p['label']} · {p['en']}",
                "color": p["color"],
            }
        )
    for nid, s in state.items():
        lay[nid] = {
            "x": era_x.get(s["era"], 0) * CW,
            "y": lane_top.get(s["palace"], 0) + s["pos"]["slot"] * (NH + GAP),
            "w": NW,
            "h": NH,
            "color": next(
                (p["color"] for p in cfg["palaces"] if p["id"] == s["palace"]), "default"
            ),
        }
        for dep in s["requires"]:
            if dep in state:
                edges.append({"from": dep, "to": nid})

    meta = json.dumps({"layout": lay, "edges": edges, "groups": groups}, ensure_ascii=False)
    out = [
        "---",
        "type: canvas",
        "tags:",
        "  - canvas",
        "  - life-tree",
        "board_id: life-tree",
        "title: life-tree",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        f"node_count: {len(lay)}",
        f"edge_count: {len(edges)}",
        "---",
        "",
        "## _meta",
        "",
        "```json",
        meta,
        "```",
        "",
    ]
    for nid, s in state.items():
        body = f"**{s['title']}**"
        if s["date"]:
            body += f"  ·  {s['date']}"
        body += f"\n\n{s['state']}"
        if s["ref"]:
            body += f"\n\n[[{s['ref']}]]"
        out += [f"## n{nid}", "", body, ""]
    return "\n".join(out)


_HTML = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>人生科技樹</title>
<header>
  <h1>人生科技樹</h1>
  <span class="sub" id="stat"></span>
  <span class="legend">
    <span><i style="background:var(--lt-done)"></i>done</span>
    <span><i style="background:var(--lt-active)"></i>active</span>
    <span><i style="background:var(--lt-avail)"></i>available</span>
    <span><i style="background:var(--lt-locked);border:1px solid #444"></i>locked</span>
    <span><i style="background:var(--lt-closed)"></i>foreclosed</span>
    <span><i style="background:var(--lt-fading)"></i>fading</span>
  </span>
</header>
<div class="trait" id="trait"></div>
<div id="wrap"><svg id="svg"></svg></div>
<aside id="panel"><button id="close">&times;</button><div id="pbody"></div></aside>
<script>
window.LIFE_TREE_DATA = __DATA__;
</script>
<script>
__JS__
</script>
"""


_CSS = r"""
:root{
  --lt-bg:#12131a; --lt-panel:#1a1c25; --lt-line:#2b2e3b; --lt-text:#e6e8ef; --lt-muted:#8b90a3;
  --lt-done:#3f8f6b; --lt-active:#c9a227; --lt-avail:#4a7fb5; --lt-locked:#2b2e3b;
  --lt-closed:#b5564a; --lt-fading:#6b6f80;
}
*{box-sizing:border-box}
body{margin:0;background:var(--lt-bg);color:var(--lt-text);
/* The island paints its own dark ground, so every element whose colour would
   otherwise come from theme.css must be claimed here — on a light theme the
   shared --text is near-black and the h1 lands at ~1:1 on --lt-bg. Specific
   rules below still win; this only stops silent inheritance. */
h1,h2,h3,p,ul,ol,li,button,a,label,div{color:var(--lt-text)}
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC",sans-serif}
header{padding:14px 18px;border-bottom:1px solid var(--lt-line);display:flex;
  gap:16px;align-items:baseline;flex-wrap:wrap;position:sticky;top:0;background:var(--lt-bg);z-index:20}
h1{font-size:17px;margin:0;letter-spacing:.04em}
.sub{color:var(--lt-muted);font-size:12px}
.legend{display:flex;gap:12px;flex-wrap:wrap;margin-left:auto;font-size:11px;color:var(--lt-muted)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;border:1px solid #4a4f63}
#wrap{overflow:auto;height:calc(100vh - 56px);position:relative}
svg{display:block}
.lane{fill:#ffffff05}
.lane.alt{fill:#ffffff09}
.laneLabel{font-size:12px;fill:var(--lt-muted)}
.laneLabel tspan.en{font-size:9px;fill:#5f6479}
.eraLabel{font-size:12px;fill:var(--lt-muted);letter-spacing:.08em}
.eraYears{font-size:9px;fill:#5f6479}
.eraSep{stroke:var(--lt-line);stroke-width:1}
.now{stroke:var(--lt-active);stroke-width:2;stroke-dasharray:4 4;opacity:.7}
.edge{stroke:#454a5e;stroke-width:1.2;fill:none}
.edge.hot{stroke:var(--lt-avail);stroke-width:2.2}
.edge.cut{stroke:var(--lt-closed);stroke-width:2;stroke-dasharray:3 3}
.n{cursor:pointer}
.n rect{rx:5;stroke-width:1.4}
.n text{font-size:11px;fill:var(--lt-text);pointer-events:none}
.n .dt{font-size:9px;fill:#aeb3c6}
.n.dim{opacity:.28}
.n.hl rect{stroke:#fff;stroke-width:2.2}
#panel{position:fixed;right:0;top:56px;bottom:0;width:340px;background:var(--lt-panel);
  border-left:1px solid var(--lt-line);padding:18px;overflow:auto;transform:translateX(100%);
  transition:transform .18s ease;z-index:30}
#panel.open{transform:none}
#panel h2{font-size:15px;margin:0 0 4px}
#panel .meta{color:var(--lt-muted);font-size:11px;margin-bottom:14px}
#panel h3{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--lt-muted);
  margin:16px 0 6px;font-weight:600}
#panel ul{margin:0;padding-left:16px}#panel li{margin:3px 0}
#panel .body{white-space:pre-wrap;color:#c3c7d6;font-size:12.5px}
#panel .inner{border-left:2px solid var(--lt-fading);padding-left:10px;color:#a9aec2;
  font-style:italic;font-size:12.5px}
#panel .cut{color:var(--lt-closed)}
#panel .open-{color:var(--lt-done)}
#close{position:absolute;right:12px;top:12px;background:none;border:0;color:var(--lt-muted);
  font-size:20px;cursor:pointer;line-height:1}
.trait{padding:12px 18px;border-bottom:1px solid var(--lt-line);color:var(--lt-muted);font-size:12px}
.trait b{color:var(--lt-text)}
.warn{color:#c9a227}
"""

_JS = r"""
/* Life-tree renderer. ONE owner: this constant.
 *
 * Two consumers, same code — the standalone HTML inlines it with the data on
 * `window.LIFE_TREE_DATA`, and the personal app serves it via `--emit-js` and
 * lets it fetch live state. A second hand-maintained copy is exactly the drift
 * this indirection exists to prevent, so never edit the emitted file.
 */
(function () {
  var CSS = `__CSS__`;
  if (!document.getElementById('life-tree-css')) {
    var st = document.createElement('style');
    st.id = 'life-tree-css'; st.textContent = CSS;
    document.head.appendChild(st);
  }
  var boot = window.LIFE_TREE_DATA;
  if (boot) { init(boot); return; }
  fetch('/life-tree/api/graph')
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(init)
    .catch(function (e) {
      var s = document.getElementById('stat');
      if (s) s.textContent = 'could not load the tree — ' + e.message;
    });

function init(D) {
var CW=210, LH=118, NW=176, NH=34, PADL=112, PADT=46, SLOT=40;
var COL={done:'--lt-done',active:'--lt-active',available:'--lt-avail',locked:'--lt-locked',
         foreclosed:'--lt-closed',fading:'--lt-fading',missed:'--lt-fading'};
var cs=getComputedStyle(document.documentElement);
function c(v){return cs.getPropertyValue(v).trim();}
var byId={}; D.nodes.forEach(function(n){byId[n.id]=n;});
var eraX={}, palY={};
D.cfg.eras.forEach(function(e,i){eraX[e.id]=i;});
D.cfg.palaces.forEach(function(p,i){palY[p.id]=i;});

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(m){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m];});}

// Lanes size to their densest cell. A fixed lane height silently spills 官祿's
// eight-node 國網 column down through 財帛 and 遷移, which makes the swimlane —
// the whole point of the palace axis — mean nothing.
var maxSlot={}, laneTop={}, LANEPAD=24;
D.cfg.palaces.forEach(function(p){maxSlot[p.id]=1;});
D.nodes.forEach(function(n){
  maxSlot[n.palace]=Math.max(maxSlot[n.palace]||1, n.pos.slot+1);});
var acc=PADT;
D.cfg.palaces.forEach(function(p){
  laneTop[p.id]=acc; acc += maxSlot[p.id]*SLOT + LANEPAD;});

function X(n){return PADL + eraX[n.era]*CW;}
function Y(n){return laneTop[n.palace] + n.pos.slot*SLOT;}

var W = PADL + D.cfg.eras.length*CW + 40;
var H = acc + 40;
var svg=document.getElementById('svg');
svg.setAttribute('width',W); svg.setAttribute('height',H);
svg.setAttribute('viewBox','0 0 '+W+' '+H);

function el(t,a){var e=document.createElementNS('http://www.w3.org/2000/svg',t);
  for(var k in a) e.setAttribute(k,a[k]); return e;}

// lanes
D.cfg.palaces.forEach(function(p,i){
  svg.appendChild(el('rect',{class:'lane'+(i%2?' alt':''),x:0,y:laneTop[p.id]-14,
    width:W,height:maxSlot[p.id]*SLOT+LANEPAD-8}));
  var t=el('text',{class:'laneLabel',x:12,y:laneTop[p.id]+6});
  t.appendChild(document.createTextNode(p.label));
  var sp=el('tspan',{class:'en',x:12,dy:13}); sp.textContent=p.en; t.appendChild(sp);
  svg.appendChild(t);
});
// eras
D.cfg.eras.forEach(function(e,i){
  var x=PADL+i*CW-18;
  svg.appendChild(el('line',{class:'eraSep',x1:x,y1:22,x2:x,y2:H-20}));
  var t=el('text',{class:'eraLabel',x:x+10,y:18}); t.textContent=e.label;
  svg.appendChild(t);
  var y=el('text',{class:'eraYears',x:x+10,y:32}); y.textContent=e.years;
  svg.appendChild(y);
});
var nowX=PADL+eraX['choose']*CW-18;
svg.appendChild(el('line',{class:'now',x1:nowX,y1:22,x2:nowX,y2:H-20}));

// edges
var eg=el('g',{}); svg.appendChild(eg);
D.nodes.forEach(function(n){
  (n.requires||[]).forEach(function(d){
    var s=byId[d]; if(!s) return;
    var x1=X(s)+NW, y1=Y(s)+NH/2, x2=X(n), y2=Y(n)+NH/2, mx=(x1+x2)/2;
    eg.appendChild(el('path',{class:'edge','data-from':d,'data-to':n.id,
      d:'M'+x1+','+y1+'C'+mx+','+y1+' '+mx+','+y2+' '+x2+','+y2}));
  });
});

// nodes
var ng=el('g',{}); svg.appendChild(ng);
D.nodes.forEach(function(n){
  var g=el('g',{class:'n','data-id':n.id});
  var fill=c(COL[n.state]||'--lt-locked');
  g.appendChild(el('rect',{x:X(n),y:Y(n),width:NW,height:NH,fill:fill,
    stroke:(n.state==='locked'?'#3a3e4e':'#0006'),
    'fill-opacity':(n.state==='locked'?0.5:0.92)}));
  var t=el('text',{x:X(n)+9,y:Y(n)+14});
  var lbl=n.title.length>26?n.title.slice(0,25)+'…':n.title;
  t.textContent=lbl; g.appendChild(t);
  if(n.date){var d=el('text',{class:'dt',x:X(n)+9,y:Y(n)+27});
    d.textContent=n.date+(n.state==='foreclosed'?'  ✕':''); g.appendChild(d);}
  g.addEventListener('click',function(){open(n.id);});
  g.addEventListener('mouseenter',function(){hover(n.id);});
  g.addEventListener('mouseleave',clear);
  ng.appendChild(g);
});

function related(id){
  var n=byId[id], s={}; s[id]=1;
  (n.requires||[]).forEach(function(d){s[d]=1;});
  (n.enables||[]).forEach(function(d){s[d]=1;});
  (n.forecloses||[]).forEach(function(d){s[d]=1;});
  (n.closed_by||[]).forEach(function(d){s[d]=1;});
  return s;
}
function hover(id){
  var s=related(id), n=byId[id];
  [].forEach.call(document.querySelectorAll('.n'),function(g){
    var i=g.getAttribute('data-id');
    g.classList.toggle('dim',!s[i]); g.classList.toggle('hl',i===id);});
  [].forEach.call(document.querySelectorAll('.edge'),function(p){
    var f=p.getAttribute('data-from'), t=p.getAttribute('data-to');
    p.classList.toggle('hot',f===id||t===id);});
  var cut=(n.forecloses||[]).concat([]);
  [].forEach.call(document.querySelectorAll('.edge'),function(p){
    if(cut.indexOf(p.getAttribute('data-to'))>=0) p.classList.add('cut');});
}
function clear(){
  [].forEach.call(document.querySelectorAll('.n'),function(g){
    g.classList.remove('dim'); g.classList.remove('hl');});
  [].forEach.call(document.querySelectorAll('.edge'),function(p){
    p.classList.remove('hot'); p.classList.remove('cut');});
}
function names(ids){return (ids||[]).map(function(i){
  return byId[i]?byId[i].title:i;});}
function open(id){
  var n=byId[id], h=[];
  h.push('<h2>'+esc(n.title)+'</h2>');
  var pal=D.cfg.palaces.filter(function(p){return p.id===n.palace;})[0]||{};
  var era=D.cfg.eras.filter(function(e){return e.id===n.era;})[0]||{};
  h.push('<div class="meta">'+esc(pal.label||'')+' · '+esc(era.label||'')+' '+
    esc(era.years||'')+' · <b>'+esc(n.state)+'</b>'+(n.date?' · '+esc(n.date):'')+'</div>');
  if(n.body) h.push('<div class="body">'+esc(n.body)+'</div>');
  if(n.inner) h.push('<h3>內心</h3><div class="inner">'+esc(n.inner)+'</div>');
  if((n.requires||[]).length) h.push('<h3>requires</h3><ul><li>'+
    names(n.requires).map(esc).join('</li><li>')+'</li></ul>');
  if((n.enables||[]).length) h.push('<h3>enables</h3><ul class="open-"><li>'+
    names(n.enables).map(esc).join('</li><li>')+'</li></ul>');
  if((n.forecloses||[]).length) h.push('<h3>forecloses</h3><ul class="cut"><li>'+
    names(n.forecloses).map(esc).join('</li><li>')+'</li></ul>');
  if((n.closed_by||[]).length) h.push('<h3>closed by</h3><ul class="cut"><li>'+
    names(n.closed_by).map(esc).join('</li><li>')+'</li></ul>');
  if(n.evidence) h.push('<h3>evidence</h3><div class="body">'+esc(n.evidence)+'</div>');
  if(n.ref) h.push('<h3>source</h3><div class="body">'+esc(n.ref)+'</div>');
  document.getElementById('pbody').innerHTML=h.join('');
  document.getElementById('panel').classList.add('open');
}
document.getElementById('close').onclick=function(){
  document.getElementById('panel').classList.remove('open');};

var counts={};
D.nodes.forEach(function(n){counts[n.state]=(counts[n.state]||0)+1;});
document.getElementById('stat').textContent =
  D.nodes.length+' nodes · '+Object.keys(counts).sort().map(function(k){
    return counts[k]+' '+k;}).join(' · ')+
  (D.findings.length?'  ·  ⚠ '+D.findings.length+' findings':'');
document.getElementById('trait').innerHTML =
  '<b>外在 武曲七殺</b> — 官祿/財帛 research fast &nbsp;·&nbsp; ' +
  '<b>內在 福德宮天同落陷</b> — 福德 is structurally expensive, pay for it deliberately' +
  '&nbsp;·&nbsp; <span class="warn">taxonomy only — no chart is cast, ' +
  'no date or outcome is derived from astrology</span>';
}
})();
"""



# ── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="", help="HTML output path")
    ap.add_argument("--canvas", action="store_true", help="also write the canvas board note")
    ap.add_argument("--validate", action="store_true", help="validate only; exit = findings")
    ap.add_argument("--emit-js", default="", metavar="PATH",
                    help="write the renderer for the app to serve (generated — never edit)")
    ap.add_argument("--scenario", default="", metavar="ID,ID",
                    help="print what taking these nodes now would open and close")
    ap.add_argument("--check", action="store_true",
                    help="with --emit-js: verify the emitted copy is current; exit 1 if stale")
    args = ap.parse_args()

    if args.emit_js and args.check:
        # A generated file nobody re-generates is just a stale second copy — the
        # exact drift the emit indirection exists to prevent.
        p = Path(args.emit_js)
        current = p.read_text(encoding="utf-8") if p.exists() else ""
        if current.endswith(js_bundle()):
            print(f"{p}: current")
            return 0
        print(f"{p}: STALE — re-run with --emit-js")
        return 1

    if args.emit_js:
        p = Path(args.emit_js)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "/* GENERATED by scripts/build_life_tree.py --emit-js. Do not edit;\n"
            "   edit _JS in that script and re-emit, or the two copies drift. */\n"
            + js_bundle(),
            encoding="utf-8",
        )
        print(f"wrote {p}")
        return 0

    vault = vault_root()
    tree_dir = vault / TREE_REL
    cfg = load_config(tree_dir)
    nodes = load_nodes(tree_dir)

    findings = validate(nodes, cfg, vault)
    for f in findings:
        print(f"  ! {f}")
    print(f"{len(nodes)} nodes, {len(findings)} findings")

    if args.validate:
        return len(findings)
    if args.scenario:
        d = scenario_delta(nodes, cfg, [s.strip() for s in args.scenario.split(",") if s.strip()])
        print(f"\ntaking: {', '.join(d['take'])}")
        for label, rows in (("opens", d["opened"]), ("closes", d["closed"])):
            print(f"  {label}: {len(rows)}")
            for r in rows:
                print(f"    {r['from']:>10} -> {r['to']:<10} {r['title']}")
        return 0
    if not nodes:
        print("no nodes yet — author some under nodes/ first")
        return 1

    state = layout(compute_states(nodes, cfg), cfg)

    out = Path(args.out) if args.out else tree_dir / "life-tree.html"
    out.write_text(render_html(state, cfg, findings), encoding="utf-8")
    print(f"wrote {out}")

    if args.canvas:
        boards = vault / "30_Resources/EmptyOS/canvas"
        boards.mkdir(parents=True, exist_ok=True)
        b = boards / "life-tree.md"
        b.write_text(render_canvas(state, cfg), encoding="utf-8")
        print(f"wrote {b}")

    return len(findings)


if __name__ == "__main__":
    raise SystemExit(main())
