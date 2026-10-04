"""Living master — the full-length MV timeline that exists before any paid video.

The MV production guide (docs/MV-PRODUCTION-GUIDE.md, stage 3) requires a
whole-song cut before anything is spent: every slot starts as a storyboard card,
and reviewed stills and clips are swapped in one slot at a time. This tool is
the Flow/Veo route's only editorial assembler. It never generates footage and
never decides that footage is good — approval is a human flag on a slot.

    python scripts/mv/living_master.py init   <dir> --audio song.wav --plan plan.json
    python scripts/mv/living_master.py render <dir>              # badged review cut
    python scripts/mv/living_master.py swap   <dir> S004 clips/s04.mp4 [--in-frame 12] [--approve]
    python scripts/mv/living_master.py approve <dir> S004
    python scripts/mv/living_master.py verify <dir> [--json]
    python scripts/mv/living_master.py render <dir> --final      # exit 2 while any slot is unapproved

`plan.json` is a list of slots in song order, each ``{"start": seconds,
"title": "...", "note": "...", "id": "S001" (optional)}``; the first must start
at 0 and each slot ends where the next begins. The last ends at
``ceil(duration * fps)`` so the whole song is kept.

The timeline (``living-master-timeline.json``, schema 1) uses Spark's field
names: half-open ``destination_start_frame``/``destination_end_frame``, a
source window of exactly the same length (never retimed or looped),
``source_sha256``, ``production_approved``. Every render writes a sidecar
``<master>.living-master.json`` whose ``placeholder_slots`` is the number of
unapproved slots; ``youtube_push_song.py`` refuses any master whose sha256
matches a sidecar reporting more than zero, whatever the file is named.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mv_config  # noqa: E402

if str(mv_config.REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(mv_config.REPO_ROOT))

SCHEMA_VERSION = 1
TIMELINE_NAME = "living-master-timeline.json"
SIDECAR_SUFFIX = ".living-master.json"
REVIEW_NAME = re.compile(r"^living-master-r\d+", re.IGNORECASE)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
FRAMINGS = {"full", "punch-in-106"}
KINDS = {"still", "video"}


class TimelineError(Exception):
    """The timeline or a requested change is invalid; nothing was written."""


class FinalRefused(Exception):
    """A final render was asked for while slots are still unapproved."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def timecode(frame: int, fps: int) -> str:
    seconds = frame / fps
    return f"{int(seconds) // 60:02d}:{seconds % 60:05.2f}"


# ── Timeline shape (pure) ────────────────────────────────────────────────────

def build_timeline(plan: list[dict], *, audio_path: str, audio_sha256: str,
                   duration_seconds: float, fps: int = 24,
                   width: int = 1920, height: int = 1080) -> dict:
    """Turn a slot plan into a schema-1 timeline with card sources left empty."""
    if not plan:
        raise TimelineError("the plan has no slots")
    if duration_seconds <= 0 or fps <= 0:
        raise TimelineError("audio duration and fps must be positive")
    total = math.ceil(duration_seconds * fps)
    starts = [round(float(p["start"]) * fps) for p in plan]
    if starts[0] != 0:
        raise TimelineError("the first slot must start at 0")
    ends = starts[1:] + [total]
    slots, seen = [], set()
    for i, (p, s, e) in enumerate(zip(plan, starts, ends, strict=True)):
        sid = str(p.get("id") or f"S{i + 1:03d}")
        if sid in seen:
            raise TimelineError(f"duplicate slot id {sid}")
        seen.add(sid)
        if e <= s:
            raise TimelineError(f"slot {sid} is empty or out of order ({s} → {e})")
        slots.append({
            "stable_id": sid,
            "title": str(p.get("title") or ""),
            "note": str(p.get("note") or ""),
            "destination_start_frame": s,
            "destination_end_frame": e,
            "source": "",
            "source_kind": "still",
            "start_frame": 0,
            "end_frame": e - s,
            "source_sha256": "",
            "asset_state": "placeholder",
            "production_approved": False,
            "framing": "full",
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "audio_path": audio_path,
        "audio_sha256": audio_sha256,
        "audio_duration_seconds": duration_seconds,
        "fps": fps,
        "width": width,
        "height": height,
        "total_frames": total,
        "frame_interval": "half-open [start,end)",
        "slots": slots,
    }


def resolve(base: Path, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else base / path


def stored(base: Path, p: Path) -> str:
    """Store a path relative to the timeline folder when it lives inside it."""
    try:
        return p.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(p.resolve())


def unapproved(timeline: dict) -> list[str]:
    return [s["stable_id"] for s in timeline["slots"] if not s.get("production_approved")]


def structure_errors(timeline: dict) -> list[str]:
    """Checks that need no files: contiguity, ids, kinds, no retiming."""
    errors: list[str] = []
    if timeline.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    slots = timeline.get("slots") or []
    if not slots:
        return errors + ["no slots"]
    expected = 0
    ids: set[str] = set()
    for s in slots:
        sid = s.get("stable_id")
        if sid in ids:
            errors.append(f"duplicate slot id {sid}")
        ids.add(sid)
        ds, de = s.get("destination_start_frame"), s.get("destination_end_frame")
        if ds != expected:
            errors.append(f"{sid}: starts at frame {ds}, expected {expected} (gap or overlap)")
        if not isinstance(de, int) or not isinstance(ds, int) or de <= ds:
            errors.append(f"{sid}: empty destination window")
            expected = de if isinstance(de, int) else expected
            continue
        expected = de
        if s.get("source_kind") not in KINDS:
            errors.append(f"{sid}: source_kind must be one of {sorted(KINDS)}")
        if s.get("framing", "full") not in FRAMINGS:
            errors.append(f"{sid}: framing must be one of {sorted(FRAMINGS)}")
        if s.get("end_frame", 0) - s.get("start_frame", 0) != de - ds:
            errors.append(f"{sid}: source window is not the slot length (no retiming)")
    if expected != timeline.get("total_frames"):
        errors.append(f"slots end at frame {expected}, song is {timeline.get('total_frames')} frames")
    return errors


def file_errors(timeline: dict, base: Path) -> list[str]:
    """Checks against disk: every source and the audio exist and match their hash."""
    errors: list[str] = []
    audio = resolve(base, timeline.get("audio_path", ""))
    if not audio.is_file():
        errors.append(f"audio missing: {audio}")
    elif sha256_file(audio) != timeline.get("audio_sha256"):
        errors.append("audio changed since the timeline was built (sha256 mismatch)")
    for s in timeline["slots"]:
        src = resolve(base, s.get("source", ""))
        if not s.get("source") or not src.is_file():
            errors.append(f"{s['stable_id']}: source missing: {src}")
        elif sha256_file(src) != s.get("source_sha256"):
            errors.append(f"{s['stable_id']}: source changed since it was placed (sha256 mismatch)")
    return errors


# ── Disk operations ─────────────────────────────────────────────────────────

def load(folder: Path) -> dict:
    path = folder / TIMELINE_NAME
    if not path.is_file():
        raise TimelineError(f"no {TIMELINE_NAME} in {folder}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(folder: Path, timeline: dict, *, bump: bool) -> None:
    """Write the timeline; on a revision bump, keep the previous one in history/."""
    path = folder / TIMELINE_NAME
    if bump and path.exists():
        hist = folder / "history"
        hist.mkdir(exist_ok=True)
        shutil.copy2(path, hist / f"living-master-timeline-r{timeline['revision']:03d}.json")
        timeline["revision"] += 1
    from emptyos.runtime.atomic_io import atomic_write_text

    atomic_write_text(path, json.dumps(timeline, ensure_ascii=False, indent=2))


def probe_fps(path: Path) -> float | None:
    """The first video stream's frame rate, or None when ffprobe cannot tell."""
    import subprocess

    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    try:
        num, _, den = out.partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None


async def _probe_frames(path: Path) -> int:
    from emptyos.sdk.media.edl import probe_video_stream

    return (await probe_video_stream(path))[1]


def init(folder: Path, audio: Path, plan: list[dict], *, fps: int = 24,
         width: int = 1920, height: int = 1080, font_path=None) -> dict:
    """Create the v0 timeline with one storyboard card per slot. Never overwrites."""
    from emptyos.sdk.media.video import probe_duration
    import text_render

    if (folder / TIMELINE_NAME).exists():
        raise TimelineError(f"{TIMELINE_NAME} already exists — never rebuild over editorial work")
    if not audio.is_file():
        raise TimelineError(f"audio missing: {audio}")
    duration = asyncio.run(probe_duration(audio))
    folder.mkdir(parents=True, exist_ok=True)
    timeline = build_timeline(plan, audio_path=stored(folder, audio),
                              audio_sha256=sha256_file(audio), duration_seconds=duration,
                              fps=fps, width=width, height=height)
    for s in timeline["slots"]:
        card = folder / "cards" / f"{s['stable_id']}.png"
        text_render.render_card(
            card, width=width, height=height, stable_id=s["stable_id"],
            title=s["title"], note=s["note"],
            timecode=f"{timecode(s['destination_start_frame'], fps)}–"
                     f"{timecode(s['destination_end_frame'], fps)}",
            font_path=font_path)
        s["source"] = stored(folder, card)
        s["source_sha256"] = sha256_file(card)
    save(folder, timeline, bump=False)
    return timeline


def swap(folder: Path, stable_id: str, source: Path, *, in_frame: int = 0,
         kind: str | None = None, framing: str | None = None,
         approve: bool = False) -> dict:
    """Place a new source in one slot, keeping its id and destination frames."""
    timeline = load(folder)
    slot = next((s for s in timeline["slots"] if s["stable_id"] == stable_id), None)
    if slot is None:
        raise TimelineError(f"no slot {stable_id}")
    if not source.is_file():
        raise TimelineError(f"source missing: {source}")
    is_image = source.suffix.lower() in IMAGE_SUFFIXES
    kind = kind or ("still" if is_image else "video")
    if kind not in KINDS:
        raise TimelineError(f"kind must be one of {sorted(KINDS)}")
    if (kind == "still") != is_image:
        raise TimelineError(f"{source.name} cannot be used as a {kind}")
    if framing is not None and framing not in FRAMINGS:
        raise TimelineError(f"framing must be one of {sorted(FRAMINGS)}")
    need = slot["destination_end_frame"] - slot["destination_start_frame"]
    in_frame = max(0, int(in_frame))
    if approve and sha256_file(source) in _card_shas(folder):
        raise TimelineError(f"{source.name} is a storyboard card; a card is never approved")
    if kind == "video":
        rate = probe_fps(source)
        if rate is None or abs(rate - timeline["fps"]) > 0.01:
            raise TimelineError(
                f"{source.name} runs at {rate} fps, the timeline at {timeline['fps']}. The "
                "assembler reads frames by index, so another rate would play at the wrong "
                "speed. Conform the clip first.")
        have = asyncio.run(_probe_frames(source))
        if in_frame + need > have:
            raise TimelineError(
                f"{stable_id} needs {need} frames from frame {in_frame}, but {source.name} "
                f"has {have}. Pick an earlier in-frame or a longer clip — slots are never "
                "retimed or looped.")
    else:
        in_frame = 0
    slot.update({
        "source": stored(folder, source),
        "source_kind": kind,
        "start_frame": in_frame,
        "end_frame": in_frame + need,
        "source_sha256": sha256_file(source),
        "asset_state": "approved" if approve else "candidate",
        "production_approved": bool(approve),
    })
    if framing:
        slot["framing"] = framing
    save(folder, timeline, bump=True)
    return timeline


def _card_shas(folder: Path) -> set[str]:
    cards = folder / "cards"
    return {sha256_file(p) for p in cards.glob("*.png")} if cards.is_dir() else set()


def approve(folder: Path, stable_id: str) -> dict:
    timeline = load(folder)
    slot = next((s for s in timeline["slots"] if s["stable_id"] == stable_id), None)
    if slot is None:
        raise TimelineError(f"no slot {stable_id}")
    if slot.get("asset_state") == "placeholder" or slot.get("source_sha256") in _card_shas(folder):
        raise TimelineError(f"{stable_id} still holds a storyboard card — swap in real footage first")
    slot["production_approved"] = True
    slot["asset_state"] = "approved"
    save(folder, timeline, bump=True)
    return timeline


async def video_length_errors(timeline: dict, base: Path) -> list[str]:
    """Video slots that read past the end of their source."""
    errors = []
    for s in timeline["slots"]:
        src = resolve(base, s.get("source", ""))
        if s.get("source_kind") == "video" and src.is_file():
            have = await _probe_frames(src)
            if s["end_frame"] > have:
                errors.append(f"{s['stable_id']}: source has {have} frames, slot reads to {s['end_frame']}")
    return errors


def sidecar_for(master: Path) -> Path:
    return master.with_name(master.name + SIDECAR_SUFFIX)


def _badge_overlay_args(timeline: dict, folder: Path, font_path) -> tuple[list[str], str]:
    """ffmpeg inputs + filtergraph burning a badge onto every unapproved slot."""
    import text_render

    inputs, chain, last = [], [], "0:v"
    targets = [s for s in timeline["slots"] if not s.get("production_approved")]
    for i, s in enumerate(targets, start=1):
        badge = text_render.render_badge(
            folder / "badges" / f"{s['stable_id']}.png",
            f"PLACEHOLDER {s['stable_id']}", frame_height=timeline["height"],
            font_path=font_path)
        inputs += ["-i", str(badge)]
        out = f"b{i}"
        a, b = s["destination_start_frame"], s["destination_end_frame"] - 1
        chain.append(f"[{last}][{i}:v]overlay=x=W-w-24:y=24:"
                     f"enable='between(n,{a},{b})'[{out}]")
        last = out
    return inputs, ";\n".join(chain) + (f";\n[{last}]format=yuv420p[vout]" if chain else "")


async def _render(folder: Path, *, final: bool, gpu: bool, font_path) -> Path:
    from emptyos.sdk.media import edl
    from emptyos.sdk.media.encode import video_args_resolved

    timeline = load(folder)
    errors = structure_errors(timeline) + file_errors(timeline, folder)
    if errors:
        raise TimelineError("; ".join(errors))
    pending = unapproved(timeline)
    if final and pending:
        raise FinalRefused(f"{len(pending)} slot(s) not approved: {', '.join(pending)}")
    short = await video_length_errors(timeline, folder)
    if short:
        raise TimelineError("; ".join(short))
    fps, total = timeline["fps"], timeline["total_frames"]
    edits = [{**s, "source": resolve(folder, s["source"])} for s in timeline["slots"]]
    work = folder / ".render"
    work.mkdir(exist_ok=True)
    silent = work / "silent.mp4"
    if not await edl.assemble_frame_native_edl(
            edits, silent, width=timeline["width"], height=timeline["height"], fps=fps, gpu=gpu):
        raise TimelineError("frame-native assembly failed")
    picture = silent
    if pending:
        inputs, graph = _badge_overlay_args(timeline, work, font_path)
        graph_file = work / "badges.filtergraph.txt"
        graph_file.write_text(graph, encoding="utf-8")
        picture = work / "badged.mp4"
        if not await edl.run_ffmpeg(
                "ffmpeg", "-y", "-i", str(silent), *inputs,
                "-filter_complex_script", str(graph_file), "-map", "[vout]",
                *await video_args_resolved(crf=18, preset="medium", gpu=gpu),
                "-fps_mode", "passthrough", "-an", str(picture)):
            raise TimelineError("placeholder badge overlay failed")
    name = f"living-master-{'final-' if final else ''}r{timeline['revision']:03d}.mp4"
    master = folder / name
    # Mux inside the work dir and move into place only once the frame count
    # and the sidecar exist: a half-finished master with a final-looking name
    # and no sidecar would pass the release guard.
    staged = work / name
    if not await edl.mux_exact(picture, resolve(folder, timeline["audio_path"]), staged,
                               frames=total, fps=fps):
        raise TimelineError("audio mux failed")
    got = await _probe_frames(staged)
    if got != total:
        raise TimelineError(f"rendered {got} frames, timeline is {total}")
    from emptyos.runtime.atomic_io import atomic_write_text

    atomic_write_text(sidecar_for(master), json.dumps({
        "schema_version": SCHEMA_VERSION,
        "master": master.name,
        "master_sha256": sha256_file(staged),
        "timeline_revision": timeline["revision"],
        "timeline_sha256": sha256_file(folder / TIMELINE_NAME),
        "total_frames": total,
        "slots": len(timeline["slots"]),
        "placeholder_slots": len(pending),
        "final": final,
        "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2))
    os.replace(staged, master)
    shutil.rmtree(work, ignore_errors=True)
    return master


def render(folder: Path, *, final: bool = False, gpu: bool = False, font_path=None) -> Path:
    return asyncio.run(_render(folder, final=final, gpu=gpu, font_path=font_path))


# ── Release guard (used by scripts/youtube_push_song.py) ────────────────────

def matching_sidecar(video: Path, search_roots: list[Path] | None = None) -> dict | None:
    """The render sidecar whose ``master_sha256`` equals this file's, if any."""
    roots = search_roots or [video.resolve().parent.parent]
    found = {sidecar_for(video)} if sidecar_for(video).is_file() else set()
    for root in roots:
        if root.is_dir():
            found.update(root.rglob(f"*{SIDECAR_SUFFIX}"))
    if not found:
        return None
    digest = sha256_file(video)
    for car in sorted(found):
        try:
            data = json.loads(car.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("master_sha256") == digest:
            return data
    return None


def living_master_projects(video: Path, search_roots: list[Path] | None = None) -> list[Path]:
    """Living-master folders that sit beside this file's folder (the standard MV layout)."""
    here = video.resolve().parent.parent
    candidates = [here] + list(search_roots or [])
    return [c / "living-master" for c in candidates if (c / "living-master" / TIMELINE_NAME).is_file()]


def release_block_reason(video: Path, search_roots: list[Path] | None = None) -> str | None:
    """Why this file must not be uploaded, or None.

    Blocked:
    - a file named like a review render;
    - a file whose sha256 matches a render sidecar that still has placeholder
      slots (so a hardlink or copy under a release name is caught);
    - in an MV that uses a living master (a ``living-master/`` folder with a
      timeline beside the release folder), any file that is neither a render
      with no placeholders nor a post-tool output derived from one. That closes
      the path around the living master: re-encoding, or finishing clips that
      were never approved in it.

    Allowed: a video with no sidecar outside a living-master project (a static
    visualizer, an older MV). A review render with every slot approved is
    byte-identical to the final one, so it is allowed too.
    Not caught: a deleted sidecar in a project without a timeline folder.
    """
    if REVIEW_NAME.match(video.name):
        return f"{video.name} is a living-master review render, not a final master"
    data = matching_sidecar(video, search_roots)
    if data is not None:
        if data.get("placeholder_slots", 1) != 0:
            return (f"{video.name} is living-master render {data.get('master')} with "
                    f"{data.get('placeholder_slots')} placeholder slot(s); render --final first")
        return None
    projects = living_master_projects(video, search_roots)
    if projects:
        return (f"{video.name} does not trace back to a final render of {projects[0]}; finish it "
                "with the scripts/mv post tools from the final master or from approved clips")
    return None


def write_derived_sidecar(output: Path, source: Path, tool: str,
                          search_roots: list[Path] | None = None) -> bool:
    """Record that ``output`` was made from a placeholder-free render ``source``.

    Post tools (cards, burn_subtitles) call this, so a finished file keeps the
    lineage the release guard checks. Nothing is written when the source has
    no clean sidecar; the guard then treats the output like any unknown file.
    """
    data = matching_sidecar(source, search_roots)
    if data is None or data.get("placeholder_slots", 1) != 0:
        return False
    from emptyos.runtime.atomic_io import atomic_write_text

    atomic_write_text(sidecar_for(output), json.dumps({
        "schema_version": SCHEMA_VERSION,
        "master": output.name,
        "master_sha256": sha256_file(output),
        "placeholder_slots": 0,
        "final": True,
        "derived_from": data.get("master"),
        "derived_by": tool,
        "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2))
    return True


def approved_source_shas(folder: Path) -> set[str]:
    """sha256 of every source a human has approved in this living master."""
    timeline = load(folder)
    return {s["source_sha256"] for s in timeline["slots"]
            if s.get("production_approved") and s.get("source_sha256")}


# ── CLI ─────────────────────────────────────────────────────────────────────

def _font(args, *, required: bool) -> Path | None:
    """Cards carry Chinese titles, so init needs a real font; badges are ASCII."""
    cli = getattr(args, "font", None)
    if required:
        return mv_config.require("font.zh_sans", cli)
    return mv_config.resolve("font.zh_sans", cli)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("dir", type=Path)
    p.add_argument("--audio", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--font")
    p = sub.add_parser("render")
    p.add_argument("dir", type=Path)
    p.add_argument("--final", action="store_true")
    p.add_argument("--gpu", action="store_true")
    p.add_argument("--font")
    p = sub.add_parser("swap")
    p.add_argument("dir", type=Path)
    p.add_argument("stable_id")
    p.add_argument("source", type=Path)
    p.add_argument("--in-frame", type=int, default=0)
    p.add_argument("--kind", choices=sorted(KINDS))
    p.add_argument("--framing", choices=sorted(FRAMINGS))
    p.add_argument("--approve", action="store_true")
    p = sub.add_parser("approve")
    p.add_argument("dir", type=Path)
    p.add_argument("stable_id")
    p = sub.add_parser("verify")
    p.add_argument("dir", type=Path)
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "init":
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            t = init(args.dir, args.audio, plan, fps=args.fps, width=args.width,
                     height=args.height, font_path=_font(args, required=True))
            print(f"v0 timeline: {len(t['slots'])} slots, {t['total_frames']} frames. "
                  f"Next: render {args.dir}")
        elif args.cmd == "render":
            master = render(args.dir, final=args.final, gpu=args.gpu, font_path=_font(args, required=False))
            print(f"rendered {master}")
        elif args.cmd == "swap":
            t = swap(args.dir, args.stable_id, args.source, in_frame=args.in_frame,
                     kind=args.kind, framing=args.framing, approve=args.approve)
            print(f"{args.stable_id} swapped; revision {t['revision']}; "
                  f"{len(unapproved(t))} slot(s) unapproved")
        elif args.cmd == "approve":
            t = approve(args.dir, args.stable_id)
            print(f"{args.stable_id} approved; {len(unapproved(t))} slot(s) unapproved")
        else:
            t = load(args.dir)
            errors = structure_errors(t) + file_errors(t, args.dir)
            errors += asyncio.run(video_length_errors(t, args.dir))
            status = {"ok": not errors, "revision": t["revision"], "slots": len(t["slots"]),
                      "unapproved": unapproved(t), "errors": errors}
            print(json.dumps(status, ensure_ascii=False, indent=None if args.json else 2))
            return 0 if not errors else 1
    except FinalRefused as exc:
        print(f"REFUSING final render: {exc}", file=sys.stderr)
        return 2
    except TimelineError as exc:
        print(f"living master: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
