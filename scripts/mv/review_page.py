"""Living-master review page — the one page every living-master review is sent as.

A video player beside the slot list, synced to playback: the playing slot is
highlighted and shown under the player, and clicking a slot seeks to it. Each
slot shows its timecode and length, a thumbnail, stable id, section, place tag
(the ``【…】`` prefix of its title), SING tag, source kind (storyboard card /
placeholder still / still / video, from the timeline), cut-quality label, title,
note, and the lyric lines whose midpoint falls inside it.

    python scripts/mv/review_page.py <living-master dir>              # local page
    python scripts/mv/review_page.py <living-master dir> --standalone # + self-contained copy

The local page links the video and thumbnails by relative path, so it only works
where it was built. **The copy you send is the ``--standalone`` one**: the video
and every thumbnail are embedded as data URIs (thumbnails re-encoded as JPEG,
at most 360 px wide), so it opens on another device. Its size is printed; over
16 MB (decimal) gets a warning — make a smaller review render first, e.g.
``ffmpeg -i living-master-rNNN.mp4 -vf scale=1280:720 -crf 28 living-master-rNNN-720p-review.mp4``
(a ``*review*`` copy at the newest revision is picked automatically).

Inputs, all optional except the living-master dir:

* ``living-master-timeline.json`` — slot windows (the review video was rendered
  from it, so seeking follows it) and source kind; a still also shows whether it
  is approved (videos show "影片" either way).
* ``plan.json`` — title / note / ``sing`` per slot (by ``id``, or by position
  S001, S002… when entries carry no id, as ``living_master.py init`` does); wins
  over the timeline's copy of title and note, because the plan is where they get
  edited.
* ``cut-check.json`` — ``{"rows": [{"id", "cut", "cut_db"}]}``.
* ``--lyrics`` — ``{"lines": [{"id", "text", "start", "end"}]}``; ``--phrase-bounds``
  (``{"boundaries": [{"after", "before", "t"}]}``) replaces each line's span with
  measured boundaries. Defaults: the highest ``vN`` of
  ``analysis/lyric-timeline-v*.json`` / ``analysis/phrase-bounds-v*.json`` next to
  the living-master dir.
* ``review.json`` in the living-master dir (or ``--meta``): the per-round text —

      {"title": "...", "subtitle": "...",
       "sections": [[0, "Intro"], [18.4, "V1"]], "acts": [[0, "I ..."]],
       "thumbs": ["../stills/v2/{id}-v2.jpg", "../stills/v1/{id}.jpeg"],
       "cards": [{"title": "請你決定", "ordered": true, "items": ["**bold** lead ..."],
                  "text": "closing paragraph"}]}

  ``thumbs`` patterns (relative to the file) are tried in order and the first
  that exists wins; ``--thumb`` on the command line (relative to the working
  directory) replaces them. With no match, a still slot shows its own source.
  In card text ``**x**`` renders bold; everything else is escaped.

Stdlib only for the local page; ``--standalone`` uses Pillow to shrink
thumbnails (without it they are embedded as-is). Never imports the kernel.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import math
import mimetypes
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

TIMELINE_NAME = "living-master-timeline.json"
STANDALONE_WARN_BYTES = 16_000_000  # decimal MB, as printed; the size cap for a page sent as an artifact
THUMB_WIDTH = 360     # max width: a slot row shows it at <=150 css px, 2.4x is enough for a sharp retina tile
THUMB_QUALITY = 78    # JPEG quality that keeps 45 thumbnails near 1 MB total
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

CUT_LABEL = {"silence": "剪點：靜音", "dip": "剪點：低谷", "shallow": "剪點：淺，待耳聽",
             "VOICED": "剪點：有聲！"}
KIND_LABEL = {"card": "分鏡卡", "placeholder-still": "靜圖（未核准）", "still": "靜圖",
              "video": "影片"}


# ── Data (pure) ─────────────────────────────────────────────────────────────

def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def latest_version(folder: Path, stem: str) -> Path | None:
    """Highest ``<stem>-vN.json`` in ``folder`` (numeric, so v10 beats v9)."""
    best, best_n = None, -1
    for p in folder.glob(f"{stem}-v*.json"):
        m = re.fullmatch(rf"{re.escape(stem)}-v(\d+)\.json", p.name)
        if m and int(m.group(1)) > best_n:
            best, best_n = p, int(m.group(1))
    return best


def lyric_midpoints(lines: list[dict], bounds: list[dict] | None) -> list[dict]:
    """``[{"text", "mid"}]`` — the midpoint of each line's span, measured when possible.

    A phrase boundary ``{"after": A, "before": B, "t": t}`` is where line A ends
    and line B starts; it overrides the line's own (unmeasured) start/end.
    """
    starts = {b["before"]: b["t"] for b in bounds or [] if "before" in b}
    ends = {b["after"]: b["t"] for b in bounds or [] if "after" in b}
    out = []
    for ln in lines:
        st = starts.get(ln.get("id"), ln.get("start"))
        en = ends.get(ln.get("id"), ln.get("end"))
        if bounds is not None and st is not None and en is not None:
            mid = (st + en) / 2
        else:  # unmeasured, or one side missing: anchor on the edge we have, never drop the line
            mid = st if st is not None else en
        out.append({"text": str(ln.get("text", "")), "mid": mid})
    return out


def label_at(t: float, table: list) -> str:
    """Name of the entry with the greatest start <= t (the table need not be sorted)."""
    hits = [(float(s), n) for s, n in table if float(s) <= t + 1e-6]
    return max(hits, key=lambda h: h[0])[1] if hits else ""


def source_kind(slot: dict) -> str:
    if slot.get("asset_state") == "placeholder" or not slot.get("source"):
        return "card"
    if slot.get("source_kind") == "video":
        return "video"
    return "still" if slot.get("production_approved") else "placeholder-still"


def build_rows(timeline: dict | None, plan: list[dict] | None, *, lyrics: list[dict],
               cuts: dict, sections: list, acts: list, end: float | None = None) -> list[dict]:
    """One row per slot. Windows come from the timeline (what the video shows);
    without one, from the plan's starts with the last slot ending at ``end``."""
    # Same id rule as living_master.init: a plan entry without "id" is S001, S002… by position.
    by_id = {str(p.get("id") or f"S{i + 1:03d}"): p for i, p in enumerate(plan or [])}
    if timeline:
        fps = timeline["fps"]
        slots = [{"id": s["stable_id"], "a": s["destination_start_frame"] / fps,
                  "b": s["destination_end_frame"] / fps, "title": s.get("title", ""),
                  "note": s.get("note", ""), "kind": source_kind(s),
                  "source": s.get("source", "")} for s in timeline["slots"]]
    else:
        if not plan:
            raise ValueError("need a timeline or a plan")
        if end is None:
            raise ValueError("without a timeline, --end (song length in seconds) is required")
        slots = []
        for k, p in enumerate(plan):
            a = float(p["start"])
            b = float(plan[k + 1]["start"]) if k + 1 < len(plan) else float(end)
            if not (math.isfinite(a) and math.isfinite(b)):
                raise ValueError(f"plan entry {k + 1}: start must be a finite number")
            slots.append({"id": str(p.get("id") or f"S{k + 1:03d}"), "a": a,
                          "b": b, "title": "", "note": "", "kind": None, "source": ""})
    rows, prev_act = [], None
    for s in slots:
        p = by_id.get(s["id"], {})
        title = str(p.get("title", s["title"]) or "")
        act = label_at(s["a"], acts)
        c = cuts.get(s["id"], {})
        rows.append({
            **s, "title": title, "note": str(p.get("note", s["note"]) or ""),
            "sing": bool(p.get("sing", False)),
            "place": title[1:title.index("】")] if title.startswith("【") and "】" in title else "",
            "sec": label_at(s["a"], sections),
            "act": act if act and act != prev_act else "",
            "lyrics": [ln["text"] for ln in lyrics
                       if ln["mid"] is not None and s["a"] <= ln["mid"] < s["b"]],
            "cut": c.get("cut"), "cut_db": c.get("cut_db"),
        })
        prev_act = act
    return rows


def find_thumb(sid: str, patterns: list[str], base: Path, source: str = "",
               folder: Path | None = None) -> Path | None:
    """First existing ``{id}`` pattern; else the slot's own still source."""
    for pat in patterns:
        p = Path(pat.replace("{id}", sid))
        p = p if p.is_absolute() else base / p
        if p.is_file():
            return p
    if source and folder is not None:
        src = Path(source)
        src = src if src.is_absolute() else folder / src
        if src.suffix.lower() in IMAGE_SUFFIXES and src.is_file():
            return src
    return None


# ── HTML ────────────────────────────────────────────────────────────────────

def tc(t: float) -> str:
    cs = round(t * 100)  # round once, on the whole value, so 59.999 reads 1:00.00 not 0:60.00
    return f"{cs // 6000}:{(cs % 6000) / 100:05.2f}"


def _num(x: float) -> str:
    """Seek/range value rounded UP to the millisecond, so seeking a slot's start never
    lands on the previous shot's last frame."""
    return f"{math.ceil(x * 1000 - 1e-9) / 1000:.3f}".rstrip("0").rstrip(".")


def _rich(text: str) -> str:
    """Escape, then let ``**x**`` through as bold — the only markup cards need."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(str(text)))


def _card(c: dict) -> str:
    tag = "ol" if c.get("ordered") else "ul"
    items = "".join(f"<li>{_rich(i)}</li>" for i in c.get("items") or [])
    body = f"<{tag}>{items}</{tag}>" if items else ""
    for sub in c.get("subsections") or []:
        sub_items = "".join(f"<li>{_rich(i)}</li>" for i in sub.get("items") or [])
        body += (f'<h3 style="font-size:15px;margin:10px 0 4px">{html.escape(sub.get("title", ""))}</h3>'
                 f"<ul>{sub_items}</ul>")
    if c.get("text"):
        body += f"<p>{_rich(c['text'])}</p>"
    return f'<section class="card"><h2>{html.escape(c.get("title", ""))}</h2>{body}</section>'


def render_page(rows: list[dict], *, video_src: str, thumb_src: dict[str, str],
                meta: dict, duration: float, fps: int | None) -> str:
    slot_html = []
    for r in rows:
        if r["act"]:
            slot_html.append(f'<li class="act">{html.escape(r["act"])}</li>')
        ly = ("".join(f"<span>{html.escape(x)}</span>" for x in r["lyrics"])
              or '<span class="none">（無人聲）</span>')
        tag = (f'<span class="place">{html.escape(r["place"])}</span>' if r["place"] else "")
        tag += '<span class="tag">SING 對嘴</span>' if r["sing"] else ""
        if r.get("kind") in KIND_LABEL:
            tag += f'<span class="place kind-{r["kind"]}">{KIND_LABEL[r["kind"]]}</span>'
        if r["cut"] in CUT_LABEL:
            tag += f'<span class="place cut-{r["cut"]}">{CUT_LABEL[r["cut"]]}</span>'
        img = (f'<img src="{html.escape(thumb_src[r["id"]])}" alt="" loading="lazy">'
               if r["id"] in thumb_src else "")
        sec = f'<em>{html.escape(r["sec"])}</em>' if r["sec"] else ""
        slot_html.append(
            f'<li class="slot" data-id="{html.escape(r["id"])}" data-a="{_num(r["a"])}" '
            f'data-b="{_num(r["b"])}"><button type="button" onclick="seek({_num(r["a"])})">'
            f'<span class="tc">{tc(r["a"])}<small>{r["b"] - r["a"]:.1f}s</small>{img}</span>'
            f'<span class="body"><span class="head"><b>{html.escape(r["id"])}</b>{sec}{tag}</span>'
            f'<span class="title">{html.escape(r["title"])}</span>'
            f'<span class="note">{html.escape(r["note"])}</span>'
            f'<span class="lyr">{ly}</span></span></button></li>')

    checks = "".join(
        f'<li><button type="button" class="chip" onclick="seek({_num(max(0.0, r["a"] - 2))})">'
        f'▶ {tc(r["a"])}</button> <span><b>{html.escape(r["id"])} 的剪點</b>'
        f'<small>人聲低谷只有 {html.escape(str(r["cut_db"]))} dB，可能切在句中；從 2 秒前開始播</small>'
        f'</span></li>'
        for r in rows if r["cut"] == "shallow")
    cards = [_card(c) for c in meta.get("cards") or []]
    if checks:  # after the first (decision) card, as the page has always been laid out
        cards.insert(min(1, len(cards)),
                     f'<section class="card"><h2>耳聽核對（淺剪點）</h2><ul class="checks">{checks}</ul></section>')
    cards = "".join(cards)

    kinds = [r.get("kind") for r in rows]
    kind_bits = "、".join(f"{KIND_LABEL[k]} {kinds.count(k)}" for k in KIND_LABEL if kinds.count(k))
    summary = (f'{len(rows)} 格' + (f"（{kind_bits}）" if kind_bits else "")
               + f'；{sum(r["sing"] for r in rows)} 格要對嘴（SING）。{duration:.3f} s'
               + (f" · {fps} fps" if fps else "") + "。點任一格跳到那裡；播放時右欄會跟著亮。")
    subtitle = f'{html.escape(meta["subtitle"])} ' if meta.get("subtitle") else ""
    title = html.escape(meta.get("title") or "活母帶審看")

    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{--bg:#f6f4ef;--panel:#fff;--ink:#1d1d1b;--mute:#6b6760;--line:#e2ded5;--accent:#8a2f2a;--accent-soft:#f3e3e1;--now:#fff3d6}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#141414;--panel:#1d1d1d;--ink:#ecebe7;--mute:#a19d95;--line:#2f2e2b;--accent:#e08a80;--accent-soft:#3a2321;--now:#3a3220}}}}
:root[data-theme=dark]{{--bg:#141414;--panel:#1d1d1d;--ink:#ecebe7;--mute:#a19d95;--line:#2f2e2b;--accent:#e08a80;--accent-soft:#3a2321;--now:#3a3220}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif}}
header{{padding:20px 16px 8px;max-width:1280px;margin:auto}}h1{{margin:0;font-size:22px}}header p{{margin:4px 0 0;color:var(--mute)}}
main{{max-width:1280px;margin:auto;padding:8px 16px 40px;display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:20px}}
@media (max-width:860px){{main{{grid-template-columns:1fr}}.player{{position:static!important}}}}
.player{{position:sticky;top:12px;align-self:start}}video{{width:100%;border-radius:8px;background:#000;display:block}}
.now{{margin:10px 0 0;padding:12px 14px;background:var(--panel);border:1px solid var(--line);border-radius:8px}}
.now .t{{font-size:18px;font-weight:600}}.now .n{{color:var(--mute)}}.now .l{{margin-top:6px}}
section.card{{margin-top:14px;padding:14px;background:var(--panel);border:1px solid var(--line);border-radius:8px}}
section.card h2{{font-size:16px;margin:0 0 8px}}section.card ol,section.card ul{{margin:0;padding-left:20px}}
.checks{{list-style:none;padding:0!important}}.checks li{{display:flex;gap:10px;align-items:center;margin:6px 0}}.checks small{{display:block;color:var(--mute)}}
.chip{{border:1px solid var(--accent);background:var(--accent-soft);color:var(--ink);border-radius:999px;padding:4px 10px;cursor:pointer;font:inherit;white-space:nowrap}}
.slots{{list-style:none;margin:0;padding:0}}.slots .act{{margin:16px 0 6px;font-weight:700;color:var(--accent)}}
.slot button{{all:unset;box-sizing:border-box;display:grid;grid-template-columns:150px 1fr;gap:10px;width:100%;padding:8px 10px;border-radius:6px;cursor:pointer;border:1px solid transparent}}
.slot button:hover,.slot button:focus-visible{{border-color:var(--line);background:var(--panel)}}
.slot.on button{{background:var(--now);border-color:var(--accent)}}
.tc{{font-variant-numeric:tabular-nums;color:var(--mute)}}.tc img{{display:block;width:100%;margin-top:6px;border-radius:4px;aspect-ratio:16/9;object-fit:cover}}
@media (max-width:520px){{.slot button{{grid-template-columns:110px 1fr}}}}.tc small{{display:block}}
.head{{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}}.head em{{font-style:normal;color:var(--mute);font-size:13px}}
.place{{font-size:12px;border:1px solid var(--line);border-radius:4px;padding:0 5px;color:var(--mute)}}
.cut-shallow{{border-color:var(--accent);color:var(--accent)}}.cut-VOICED{{background:var(--accent);color:#fff}}
.kind-card,.kind-placeholder-still{{border-style:dashed}}
.tag{{font-size:12px;border:1px solid var(--accent);color:var(--accent);border-radius:4px;padding:0 5px}}
.title{{display:block;font-weight:600}}.note{{display:block;color:var(--mute);font-size:14px}}
.lyr{{display:block;margin-top:2px;font-size:14px}}.lyr span{{display:inline-block;margin-right:10px}}.lyr .none{{color:var(--mute)}}
</style></head><body>
<header><h1>{title}</h1>
<p>{subtitle}{summary}</p></header>
<main>
<div class="player">
<video id="v" src="{html.escape(video_src)}" controls preload="metadata"></video>
<div class="now" aria-live="polite"><div class="t" id="nt">—</div><div class="n" id="nn"></div><div class="l" id="nl"></div></div>
{cards}
</div>
<ol class="slots" id="slots">{''.join(slot_html)}</ol>
</main>
<script>
const v=document.getElementById('v'),items=[...document.querySelectorAll('.slot')];
function seek(t){{v.currentTime=Math.max(0,t);v.play();}}
let cur=null;
v.addEventListener('timeupdate',()=>{{const t=v.currentTime;const el=items.find(e=>t>=+e.dataset.a&&t<+e.dataset.b);
 if(el===cur)return;cur&&cur.classList.remove('on');cur=el;if(!el)return;el.classList.add('on');
 document.getElementById('nt').textContent=el.querySelector('b').textContent+'  '+el.querySelector('.title').textContent;
 document.getElementById('nn').textContent=el.querySelector('.note').textContent;
 document.getElementById('nl').textContent=[...el.querySelectorAll('.lyr span')].map(s=>s.textContent).join(' ／ ');
 const r=el.getBoundingClientRect();if(r.top<0||r.bottom>innerHeight)el.scrollIntoView({{block:'center',behavior:'smooth'}});}});
</script></body></html>"""


# ── Sources: relative link or data URI ─────────────────────────────────────

def rel_src(target: Path, out_dir: Path) -> str:
    """URL from the page to ``target``; a file URI when no relative path exists
    (another drive on Windows)."""
    try:
        rel = os.path.relpath(target.resolve(), out_dir.resolve())
    except ValueError:
        return target.resolve().as_uri()
    return quote(rel.replace(os.sep, "/"))


def data_uri(path: Path, *, thumb: bool = False) -> str:
    data, mime = path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() == ".mp4":
        mime = "video/mp4"
    if thumb:
        try:
            from PIL import Image
        except ImportError:
            Image = None
        if Image is not None:
            try:
                with Image.open(io.BytesIO(data)) as im:
                    im = im.convert("RGB")
                    if im.width > THUMB_WIDTH:
                        im = im.resize((THUMB_WIDTH, round(im.height * THUMB_WIDTH / im.width)),
                                       Image.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=THUMB_QUALITY)
                data, mime = buf.getvalue(), "image/jpeg"
            except OSError:  # undecodable: embed the original bytes rather than fail the page
                pass
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# ── Inputs ──────────────────────────────────────────────────────────────────

_REV = re.compile(r"^living-master-r(\d+)", re.IGNORECASE)


def default_video(folder: Path) -> Path | None:
    """Newest-revision render; at that revision a ``*review*`` copy (smaller) wins."""
    best = None
    for p in folder.glob("living-master-r*.mp4"):
        m = _REV.match(p.name)
        if m:
            # newest revision; at a tie a review copy, then the smallest file (ties by name)
            key = (int(m.group(1)), "review" in p.name.lower(), -p.stat().st_size, p.name)
            if best is None or key > best[0]:
                best = (key, p)
    return best[1] if best else None


def video_revision(video: Path) -> int | None:
    m = _REV.match(video.name)
    return int(m.group(1)) if m else None


def build(folder: Path, *, video: Path | None = None, lyrics: Path | None = None,
          phrase_bounds: Path | None = None, thumbs: list[str] | None = None,
          meta_path: Path | None = None, out: Path | None = None, standalone: bool = False,
          end: float | None = None) -> dict:
    """Write the page(s). Returns ``{"local": Path, "standalone": Path|None,
    "slots": int, "bytes": int|None, "warnings": [...]}``."""
    folder = Path(folder)
    warnings: list[str] = []
    tl_path = folder / TIMELINE_NAME
    timeline = _read_json(tl_path) if tl_path.is_file() else None
    plan = _read_json(folder / "plan.json") if (folder / "plan.json").is_file() else None
    meta_path = meta_path or folder / "review.json"
    meta = _read_json(meta_path) if meta_path.is_file() else {}

    analysis = folder.parent / "analysis"
    lyrics = lyrics or latest_version(analysis, "lyric-timeline")
    phrase_bounds = phrase_bounds or latest_version(analysis, "phrase-bounds")
    lines = _read_json(lyrics)["lines"] if lyrics and lyrics.is_file() else []
    bounds = (_read_json(phrase_bounds)["boundaries"]
              if phrase_bounds and phrase_bounds.is_file() else None)
    cc = folder / "cut-check.json"
    cuts = {r["id"]: r for r in _read_json(cc)["rows"]} if cc.is_file() else {}

    rows = build_rows(timeline, plan, lyrics=lyric_midpoints(lines, bounds), cuts=cuts,
                      sections=meta.get("sections") or [], acts=meta.get("acts") or [], end=end)
    duration = (timeline["total_frames"] / timeline["fps"]) if timeline else float(end)
    fps = timeline["fps"] if timeline else None

    video = video or default_video(folder)
    if video is None or not video.is_file():
        raise FileNotFoundError(f"no review video (looked for living-master-r*.mp4 in {folder}; pass --video)")
    rev = video_revision(video)
    if timeline and rev is not None and rev != timeline.get("revision"):
        warnings.append(f"video is revision {rev} but the timeline is revision "
                        f"{timeline.get('revision')}: slot times may not match what plays")

    if thumbs is not None:
        patterns, pat_base = thumbs, Path.cwd()
    else:
        patterns, pat_base = meta.get("thumbs") or [], meta_path.parent
    thumb_paths = {}
    for r in rows:
        p = find_thumb(r["id"], patterns, pat_base, r.get("source", ""), folder)
        if p:
            thumb_paths[r["id"]] = p

    out = out or folder.parent / "review" / f"{folder.name}-review.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    page_args = dict(rows=rows, meta=meta, duration=duration, fps=fps)
    out.write_text(render_page(video_src=rel_src(video, out.parent),
                               thumb_src={k: rel_src(p, out.parent) for k, p in thumb_paths.items()},
                               **page_args), encoding="utf-8")
    result = {"local": out, "standalone": None, "slots": len(rows), "bytes": None,
              "lyrics": lyrics, "phrase_bounds": phrase_bounds, "video": video,
              "thumbs": len(thumb_paths), "warnings": warnings}
    if standalone:
        so = out.with_name(out.stem + "-standalone.html")
        so.write_text(render_page(video_src=data_uri(video),
                                  thumb_src={k: data_uri(p, thumb=True) for k, p in thumb_paths.items()},
                                  **page_args), encoding="utf-8")
        size = so.stat().st_size
        result.update(standalone=so, bytes=size)
        if size > STANDALONE_WARN_BYTES:
            warnings.append(f"standalone page is {size / 1e6:.1f} MB (> 16 MB): use a smaller "
                            f"review render (e.g. 720p) before sending")
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir", type=Path, help="living-master folder (holds living-master-timeline.json)")
    ap.add_argument("--video", type=Path, help="review render (default: newest living-master-r*.mp4)")
    ap.add_argument("--lyrics", type=Path)
    ap.add_argument("--phrase-bounds", type=Path)
    ap.add_argument("--thumb", action="append", dest="thumbs", metavar="PATTERN",
                    help="thumbnail path with {id}; repeat, first match wins")
    ap.add_argument("--meta", type=Path, help="review text (default: <dir>/review.json)")
    ap.add_argument("--out", type=Path, help="default: <dir>/../review/<dir name>-review.html")
    ap.add_argument("--end", type=float, help="song length in seconds when there is no timeline")
    ap.add_argument("--standalone", action="store_true",
                    help="also write <out>-standalone.html with everything embedded (the copy to send)")
    args = ap.parse_args(argv)
    try:
        res = build(args.dir, video=args.video, lyrics=args.lyrics, phrase_bounds=args.phrase_bounds,
                    thumbs=args.thumbs, meta_path=args.meta, out=args.out,
                    standalone=args.standalone, end=args.end)
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"review page: {exc}", file=sys.stderr)
        return 1
    print(f"{res['slots']} slots · video {res['video'].name} · {res['thumbs']} thumbnails · "
          f"lyrics {res['lyrics'].name if res['lyrics'] else '(none)'} · "
          f"bounds {res['phrase_bounds'].name if res['phrase_bounds'] else '(none)'}")
    print(f"local:      {res['local']}")
    if res["standalone"]:
        print(f"standalone: {res['standalone']}  ({res['bytes'] / 1e6:.1f} MB)  <- send this one")
    for w in res["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
