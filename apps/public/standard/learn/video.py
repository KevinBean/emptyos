"""Learn — lesson video: a narrated, captioned teaching video made from one lesson's KB note.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the five-stage lesson-video Pipeline (script → narration →
visuals → assemble → review), its run registry at
``data/apps/learn/video-runs/``, the finished-video store at
``data/apps/learn/videos/<course>/<idx>/`` plus ``videos/index.json``, and
every ``/api/.../video*`` route. The pure helpers (``normalize_script``,
``plan_timeline``, ``frame_exact_durations``, ``caption_cues``,
``render_slide_html``, ``video_run_choices``, ``video_panel_rows``,
``final_review_problems``) sit at module level so they test without a daemon.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._find_course`` (app.py); ``kb.get_note`` and
``viz.generate`` via ``call_app`` — viz is optional, and each animated scene it
generates also leaves an artifact under viz's own ``outputs/`` in the vault. A
missing or failing viz degrades that scene to a slide.
Do not import from ``.app`` (it imports us, which would cycle).

Dark by default: ``[apps.learn] feature.lesson-video.enabled = true`` in
emptyos.toml. Off, every route refuses and the lesson player renders exactly as
before.

The run pauses after ``script`` on purpose: the script is the one artifact a
human should read before narration and rendering are paid for
(.claude/rules/staged-pipeline.md — stop_after preview); a retry that has not
yet produced a script pauses there again. A run that completes has its folder
removed once the video is published, so the registry normally holds only runs
a human still has to act on.

Videos and runs are keyed by lesson position AND source note: the diagnostic
reorders a course's lessons, and a video made for one note must never show up
on whichever lesson moved into its slot.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from emptyos.sdk import atomic_write_text, parse_llm_json, web_route
from emptyos.sdk.pipeline import Pipeline, Stage
from emptyos.sdk.utils import path_segment_error

from .shared import _parse_lessons

if TYPE_CHECKING:
    from .app import LearnApp  # noqa: F401 — for type hints only

log = logging.getLogger(__name__)


# ─── Bind to LearnApp class as ───────────────────────────────────────
#   _lesson_video_enabled   = _video._lesson_video_enabled
#   _video_pipeline         = _video._video_pipeline
#   _video_lesson           = _video._video_lesson
#   _video_inputs           = _video._video_inputs
#   _video_source           = _video._video_source
#   _open_video_run         = _video._open_video_run
#   _drive_video_run        = _video._drive_video_run
#   _queue_course_videos    = _video._queue_course_videos
#   _video_run_view         = _video._video_run_view
#   _publish_lesson_video   = _video._publish_lesson_video
#   _lesson_video_dir       = _video._lesson_video_dir
#   _load_video_index       = _video._load_video_index
#   api_video_start         = _video.api_video_start
#   api_video_run           = _video.api_video_run
#   api_video_resume        = _video.api_video_resume
#   api_video_discard       = _video.api_video_discard
#   api_lesson_video        = _video.api_lesson_video
#   api_video_file          = _video.api_video_file
#   api_video_queue_course  = _video.api_video_queue_course
#   panel_video_runs        = _video.panel_video_runs
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


# ─── Constants ───────────────────────────────────────────────────────

RESOLUTION = (1280, 720)
VIEWPORT = "1280x720"
# concat_clips and _scene_fit_filter (emptyos/sdk/media/video.py) both
# normalise every clip to 30 fps, so scene lengths are counted in 30ths.
FPS = 30
# Script shape limits — a short lesson video, not a lecture. Past three
# animations the run spends more time recording than narrating.
MAX_SCENES = 10
MAX_ANIM_SCENES = 3
MAX_BULLETS = 5
# Text caps sized to the 1280×720 slide template: a heading on one line, a
# bullet on one line, narration of ~120 spoken words.
HEADING_CHAR_CAP = 90
BULLET_CHAR_CAP = 120
NARRATION_CHAR_CAP = 900
BRIEF_CHAR_CAP = 600
# Same cap as the lesson quiz (srs.py) — the precedent for how much of a KB
# note Learn sends through `think` (CLAUDE.md rule 19).
SOURCE_CHAR_CAP = 6000
# stitch_audio's own defaults, passed explicitly on both sides so the
# narration track and plan_timeline cannot disagree about where scenes start.
NARRATION_GAP_MS = 400
NARRATION_TAIL_MS = 1500
# html_record's own ceiling; a longer scene holds the clip's final frame.
ANIM_MAX_SECONDS = 30.0
# Below this an animation has no room to show a sequence; record at least this
# much and let fit_clip_to_duration trim it to the scene.
ANIM_MIN_SECONDS = 2.0
# AAC priming and frame rounding move the muxed length by a few frames; a
# second either way is still "the picture ends with the voice".
REVIEW_SLACK_S = 1.0
DEFAULT_TTS_PROVIDERS = ["kokoro", "edge-tts"]
RUNS_KIND = "video-runs"
_DISABLED = {
    "error": "lesson video is disabled",
    "hint": "set [apps.learn] feature.lesson-video.enabled = true in emptyos.toml",
}
# Latin sentence ends need a following space ("3.5 m" is not a sentence end);
# CJK full-width stops are written without one.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])\s*")


VIDEO_SCRIPT_SYSTEM = """You are a patient teacher writing the script for a short narrated teaching video.
The video is built directly from your JSON. Each scene is either:
- a SLIDE: a heading and up to 5 short bullets, shown while the narration plays, or
- an ANIM: a short animated diagram generated from your anim_brief, shown while the narration plays.

Rules:
- Ground every claim in the source note. If the note does not state a number, value, or clause, do not state one.
- Write 6 to 10 scenes. Open with what the learner will understand by the end; close with a one-scene recap.
- Narration is spoken aloud: plain sentences, 25 to 70 words per scene, no markdown, no symbols, no list markers.
- Bullets are glanceable fragments of at most about 8 words — they support the narration, they do not repeat it.
- Use ANIM for at most 3 scenes, and only where motion explains something a still cannot: a process, a flow, a sequence, a quantity changing. Its anim_brief describes what appears and moves, in order, with the labels to show.
- Every other scene is a SLIDE with an empty anim_brief.

Do NOT:
- Do not use ANIM for a list, a definition, a title card, or a recap.
- Do not quote clause numbers, table values, or limits that are not in the source note.
- Do not write narration that reads the bullets out verbatim.
- Do not say "as you can see on this slide" or describe the video itself.

Return ONLY JSON in exactly this shape:
{"title": "Why temperature rise is limited", "scenes": [{"kind": "slide", "heading": "What you will learn", "bullets": ["Where heat comes from", "Why limits exist"], "narration": "By the end of this lesson you will know where heat builds up inside an assembly and why the standard caps how hot it may run.", "anim_brief": ""}, {"kind": "anim", "heading": "Heat leaving a conductor", "bullets": [], "narration": "Current flowing through a conductor produces heat, and that heat has to travel outward through the insulation before it can escape.", "anim_brief": "A horizontal conductor labelled Conductor sits in the centre. It turns from grey to orange as a label Current flows appears. Red arrows then move outward through a surrounding ring labelled Insulation, and finally fade into the space around it labelled Air."}]}"""

VIDEO_SCRIPT_PROMPT = """Course: {course_title}
Lesson: {lesson_title}

Source note — {source_title}:
{body}

Write the video script for this lesson."""

ANIM_BRIEF_TEMPLATE = """{brief}

Scene heading to show at the top: {heading}

Format constraints: a 1280x720 landscape frame, dark background, large high-contrast labels readable at video size, no interactive controls. The whole animation lasts about {seconds} seconds and ends holding its finished state — set window.SCENE_DURATION_MS = {ms}."""


# ─── Pure helpers ────────────────────────────────────────────────────


def _clean_text(value, cap: int) -> str:
    """Whitespace-collapsed text capped at ``cap`` characters, cut back to a
    word boundary so narration is never spoken ending mid-word."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if len(text) <= cap:
        return text
    cut = text[:cap]
    space = cut.rfind(" ")
    return (cut[:space] if space > cap // 2 else cut).strip()


def normalize_script(raw) -> dict:
    """Coerce a model reply (or a user-edited script) into the shape every
    later stage trusts: ``{title, scenes: [{kind, heading, bullets, narration,
    anim_brief}]}``.

    Never raises. Drops scenes with no narration (nothing to time the scene
    by), caps scene/bullet counts and text lengths, and turns an ``anim`` with
    no brief — or any anim past ``MAX_ANIM_SCENES`` — into a ``slide``. The
    brief is kept on a demoted scene so switching it back in the editor does
    not lose the text.
    """
    if isinstance(raw, str):
        raw = parse_llm_json(raw, fallback={})
    if not isinstance(raw, dict):
        raw = {}
    scenes_in = raw.get("scenes")
    if not isinstance(scenes_in, list):
        scenes_in = []
    scenes: list[dict] = []
    anims = 0
    for item in scenes_in:
        if not isinstance(item, dict):
            continue
        narration = _clean_text(item.get("narration"), NARRATION_CHAR_CAP)
        if not narration:
            continue
        brief = _clean_text(item.get("anim_brief"), BRIEF_CHAR_CAP)
        wants_anim = str(item.get("kind") or "").strip().lower() == "anim"
        if wants_anim and brief and anims < MAX_ANIM_SCENES:
            kind = "anim"
            anims += 1
        else:
            kind = "slide"
        bullets: list[str] = []
        if isinstance(item.get("bullets"), list):
            for b in item["bullets"]:
                text = _clean_text(b, BULLET_CHAR_CAP)
                if text:
                    bullets.append(text)
                if len(bullets) >= MAX_BULLETS:
                    break
        scenes.append({
            "kind": kind,
            "heading": _clean_text(item.get("heading"), HEADING_CHAR_CAP),
            "bullets": bullets,
            "narration": narration,
            "anim_brief": brief,
        })
        if len(scenes) >= MAX_SCENES:
            break
    return {"title": _clean_text(raw.get("title"), HEADING_CHAR_CAP), "scenes": scenes}


def plan_timeline(durations_ms: list[int], gap_ms: int = NARRATION_GAP_MS,
                  tail_ms: int = NARRATION_TAIL_MS) -> list[dict]:
    """Scene windows over narration stitched by ``stitch_audio``.

    ``stitch_audio`` lays segment, gap, segment, ... , tail. Scene *i* speaks
    over ``[start_ms, end_ms)`` and its picture stays up for its speech plus
    the silence that follows it (the gap, or the tail on the last scene), so
    the pictures cover the whole stitched track.
    """
    out: list[dict] = []
    offset = 0
    n = len(durations_ms)
    for i, d in enumerate(durations_ms):
        d = max(0, int(d))
        trailing = tail_ms if i == n - 1 else gap_ms
        out.append({"start_ms": offset, "end_ms": offset + d, "clip_s": (d + trailing) / 1000.0})
        offset += d + gap_ms
    return out


def frame_exact_durations(durations_s: list[float], fps: int = FPS) -> list[float]:
    """Round scene lengths to whole frames against the running total.

    Rounding each clip on its own drifts by up to one frame per scene; rounding
    the cumulative boundaries keeps every picture change within half a frame of
    where the narration timeline puts it, however many scenes there are.
    """
    out: list[float] = []
    elapsed = 0.0
    prev_frame = 0
    for d in durations_s:
        elapsed += max(0.0, float(d))
        frame = round(elapsed * fps)
        out.append(max(1, frame - prev_frame) / fps)
        prev_frame = max(frame, prev_frame + 1)
    return out


def caption_cues(timeline: list[dict], scenes: list[dict]) -> list[dict]:
    """Sentence-sized caption cues: each scene's narration is split into
    sentences, spread across that scene's speech window by text length.

    One cue per scene would put 70 words on screen at once."""
    from emptyos.sdk.media import proportional_timings

    cues: list[dict] = []
    for slot, scene in zip(timeline, scenes, strict=False):
        sentences = [s for s in _SENTENCE_SPLIT.split(scene.get("narration") or "") if s.strip()]
        if not sentences:
            continue
        span = max(0, int(slot["end_ms"]) - int(slot["start_ms"]))
        for cue in proportional_timings(span, [{"text": s} for s in sentences]):
            cues.append({
                "start_ms": int(slot["start_ms"]) + cue["start_ms"],
                "end_ms": int(slot["start_ms"]) + cue["end_ms"],
                "text": cue["text"],
            })
    return cues


def render_slide_html(scene: dict, *, lesson_title: str, index: int, total: int) -> str:
    """A self-contained 1280×720 slide for one scene. Every value is escaped —
    headings and bullets come from a model (or a user edit), not from us.

    The palette is a fixed dark video frame, not a themed app page: a
    rendered video cannot follow the viewer's theme."""
    esc = html.escape
    heading = esc(scene.get("heading") or lesson_title or "")
    bullets = "".join(f"<li>{esc(b)}</li>" for b in scene.get("bullets") or [])
    body = f"<ul>{bullets}</ul>" if bullets else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        "html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0f172a;"
        "color:#e2e8f0;font-family:'Segoe UI',Inter,Arial,sans-serif}"
        # Bottom padding keeps the footer above the burned-in caption band — two
        # caption lines sit in roughly the lowest 120px of a 720p frame.
        ".slide{box-sizing:border-box;width:1280px;height:720px;padding:64px 96px 150px;"
        "display:flex;flex-direction:column}"
        ".kicker{font-size:22px;letter-spacing:.08em;text-transform:uppercase;color:#7dd3fc}"
        "h1{font-size:54px;line-height:1.15;margin:14px 0 36px;color:#f8fafc}"
        "ul{margin:0;padding:0;list-style:none}"
        "li{font-size:34px;line-height:1.35;margin:0 0 20px;padding-left:40px;position:relative}"
        "li::before{content:'';position:absolute;left:0;top:17px;width:14px;height:14px;"
        "border-radius:3px;background:#38bdf8}"
        ".foot{margin-top:auto;display:flex;justify-content:space-between;font-size:20px;"
        "color:#94a3b8;border-top:1px solid #1e293b;padding-top:16px}"
        "</style></head><body><div class=\"slide\">"
        f"<div class=\"kicker\">{esc(lesson_title or '')}</div>"
        f"<h1>{heading}</h1>{body}"
        f"<div class=\"foot\"><span>{esc(lesson_title or '')}</span><span>{index} / {total}</span></div>"
        "</div></body></html>"
    )


def source_sha(text: str) -> str:
    """Identity of the lesson note a video was made from — hashed over the
    note's own body, so an edit to it (and only to it) marks the video stale."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def video_run_choices(status: str, completed: list[str]) -> list[str]:
    """The actions that are legal for a run in this state — and only those
    (.claude/rules/staged-pipeline.md § the UI contract)."""
    if status in ("running", "queued"):
        return []
    if status == "paused" and list(completed) == ["script"]:
        return ["render", "regenerate", "discard"]
    if status in ("error", "interrupted"):
        return ["retry", "discard"]
    return ["discard"]


def final_review_problems(verdict_hard: list[str], *, has_audio: bool,
                          duration_s: float, audio_s: float) -> list[str]:
    """Hard reasons a finished lesson video must not be published.

    ``review_video`` treats a missing audio track as a soft note, and a lesson
    video without its narration is not a lesson video — so that is hard here,
    as is narration with no measurable length and a video whose length differs
    from the narration. (``mux_audio`` ends on the shorter stream, so a picture
    running long is trimmed before this sees it; a short one is caught.)"""
    problems = list(verdict_hard)
    if not has_audio:
        problems.append("the video has no narration track")
    if audio_s <= 0:
        problems.append("the narration has no measurable length")
    elif abs(duration_s - audio_s) > REVIEW_SLACK_S:
        problems.append(f"video length {duration_s:.2f}s does not match narration {audio_s:.2f}s")
    return problems


def video_panel_rows(states, live_ids, *, limit: int = 5) -> list[dict] | None:
    """Hub ``plain-list`` rows for lesson videos waiting on a human, each
    opening its own lesson. ``None`` on a healthy day, so no panel renders.

    A persisted ``running`` run this daemon is not driving was cut off by a
    restart; it is listed as interrupted rather than left invisible."""
    rows: list[dict] = []
    for handle, state in states:
        if handle.run_id in live_ids or not isinstance(state, dict):
            continue
        status = state.get("status")
        if status == "running":
            status = "interrupted"
        inputs = state.get("inputs") or {}
        course_id, idx = inputs.get("course_id"), _as_int(inputs.get("idx"))
        if status not in ("paused", "error", "interrupted") or not course_id or idx is None:
            continue
        if status == "paused":
            icon, subtitle = "📝", "script ready for your review"
        elif status == "error":
            icon, subtitle = "⚠", f"failed: {state.get('error') or 'unknown error'}"[:140]
        else:
            icon, subtitle = "⏸", "interrupted by a restart — retry it from the lesson"
        rows.append({
            "title": inputs.get("lesson_title") or f"Lesson {int(idx) + 1}",
            "subtitle": subtitle,
            "href": f"/learn/#course/{quote(str(course_id), safe='')}/lesson/{int(idx)}",
            "icon": icon,
        })
        if len(rows) >= limit:
            break
    return rows or None


def _as_int(value, default=None):
    """An integer from persisted or request data, or ``default`` — a hand-edited
    run.json must not break the hub panel or turn a route into a 500."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def video_entry_matches(entry: dict | None, slug: str) -> bool:
    """A saved video belongs to a lesson only if it was made from that lesson's
    note — its index slot alone is not enough once lessons can be reordered."""
    return bool(entry) and entry.get("slug") == slug


def _anim_key(scene: dict, record_s: float) -> str:
    return hashlib.sha256(
        json.dumps([scene.get("anim_brief", ""), scene.get("heading", ""), round(record_s, 1)]).encode("utf-8")
    ).hexdigest()[:16]


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _video_key(course_id: str, idx: int) -> str:
    return f"{course_id}:{int(idx)}"


def _video_live(self) -> dict:
    """In-memory map of runs this daemon is driving, queuing, or discarding.

    Persisted state alone cannot say whether a ``running`` run is still alive:
    after a restart it is an orphan. Anything not in this map is treated as
    ``interrupted``."""
    return self.__dict__.setdefault("_lesson_video_live", {})


def _mark_video_live(self, run_id: str, course_id: str, idx: int, stage: str, detail: str = "") -> None:
    _video_live(self)[run_id] = {"course_id": course_id, "idx": int(idx), "stage": stage, "pct": 0, "detail": detail}


def _store_speech(raw, audio_dir: Path, i: int) -> Path:
    """Copy (or write) one scene's speech result into the run folder."""
    if isinstance(raw, (bytes, bytearray)):
        dest = audio_dir / f"scene_{i:02d}.mp3"
        dest.write_bytes(bytes(raw))
        return dest
    src = Path(str(raw or ""))
    if not str(raw or "") or not src.is_file():
        raise RuntimeError(f"scene {i + 1}: the speech provider returned no audio file")
    dest = audio_dir / f"scene_{i:02d}{src.suffix or '.mp3'}"
    shutil.copy2(src, dest)
    return dest


def _copy_atomic(src: str | Path, dest: Path) -> None:
    """Land ``dest`` in one step: a crash mid-copy leaves only a ``.part`` file,
    never a truncated ``dest`` — and the index is written after this returns,
    so it can only ever point at a complete video."""
    part = dest.with_name(dest.name + ".part")
    shutil.copy2(src, part)
    os.replace(part, dest)


def _prune_old_videos(video_dir: Path, keep: str) -> None:
    """Remove superseded video files. One still open for streaming raises on
    Windows; it is left in place and removed on a later publish."""
    for old in list(video_dir.glob("video*.mp4")) + list(video_dir.glob("video*.mp4.part")):
        if old.name == keep:
            continue
        try:
            old.unlink()
        except OSError:
            pass


def _video_file(video_dir: Path | None, entry: dict | None) -> Path | None:
    """The published file an index entry points at, if it exists. Entries made
    before files were versioned name no file and used ``video.mp4``."""
    if video_dir is None or not entry:
        return None
    path = video_dir / (entry.get("file") or "video.mp4")
    return path if path.is_file() else None


def _approved_script(ctx) -> dict:
    """The script later stages must use: the user's edit when one was supplied
    on resume, else what the script stage produced. Source identity always
    comes from the stage result — an edit can change words, not provenance."""
    base = ctx.result("script") or {}
    edited = ctx.inputs.get("script")
    if isinstance(edited, dict):
        norm = normalize_script(edited)
        if norm["scenes"]:
            return {**base, "title": norm["title"] or base.get("title", ""), "scenes": norm["scenes"]}
    return base


# ─── Stages ──────────────────────────────────────────────────────────


async def _vs_script(ctx) -> dict:
    app = ctx.app
    source = await app._video_source(ctx.inputs.get("slug", ""))
    body = source.get("body") or ""
    if not body:
        raise RuntimeError(f"lesson source unavailable: {source.get('error') or 'the note is empty'}")
    await ctx.progress(0.1, "Writing the script")
    prompt = VIDEO_SCRIPT_PROMPT.format(
        course_title=ctx.inputs.get("course_title", ""),
        lesson_title=ctx.inputs.get("lesson_title", ""),
        source_title=source.get("title", ""),
        body=body[:SOURCE_CHAR_CAP],
    )
    raw = await app.think(prompt, system=VIDEO_SCRIPT_SYSTEM, domain="text", temperature=0.4)
    script = normalize_script(raw)
    if not script["scenes"]:
        raise RuntimeError("the model returned no usable scenes — retry, or pick another model with the model pill")
    await ctx.progress(1.0, f"{len(script['scenes'])} scenes")
    return {
        **script,
        "source_sha": source_sha(body),
        "source_path": source.get("path", ""),
        "source_title": source.get("title", ""),
    }


async def _vs_narration(ctx) -> dict:
    from emptyos.sdk.media import audio_duration_ms, stitch_audio

    app = ctx.app
    scenes = _approved_script(ctx).get("scenes") or []
    if not scenes:
        raise RuntimeError("the script has no scenes to narrate")
    audio_dir = ctx.artifact("audio")
    audio_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {}
    if ctx.inputs.get("tts_providers"):
        kwargs["prefer_provider"] = list(ctx.inputs["tts_providers"])
    if ctx.inputs.get("voice"):
        kwargs["voice"] = ctx.inputs["voice"]

    files: list[Path] = []
    durations: list[int] = []
    for i, scene in enumerate(scenes):
        await ctx.progress(i / len(scenes), f"Narrating scene {i + 1} of {len(scenes)}")
        raw = await app.speak(scene["narration"], **kwargs)
        dest = await asyncio.to_thread(_store_speech, raw, audio_dir, i)
        dur = await asyncio.to_thread(audio_duration_ms, dest)
        if dur <= 0:
            raise RuntimeError(f"scene {i + 1}: the narration audio is empty or unreadable")
        files.append(dest)
        durations.append(dur)

    name = await stitch_audio(files, audio_dir, gap_ms=NARRATION_GAP_MS,
                              tail_ms=NARRATION_TAIL_MS, filename_prefix="narration")
    if not name:
        raise RuntimeError("could not stitch the narration")
    await ctx.progress(1.0, "Narration ready")
    return {"files": [str(f) for f in files], "durations_ms": durations, "audio": str(audio_dir / name)}


async def _record_anim(app, scene: dict, record_s: float, out_mp4: Path, *,
                       source: str, context_id: str) -> str | None:
    """Generate and record one animated scene. Returns None on success, else
    the reason it failed — the caller falls back to a slide, never a hole."""
    from emptyos.sdk.media import record_html_to_mp4, review_video

    prompt = ANIM_BRIEF_TEMPLATE.format(
        brief=scene.get("anim_brief", ""),
        heading=scene.get("heading", ""),
        seconds=round(record_s),
        ms=int(record_s * 1000),
    )
    try:
        meta = await app.call_app("viz", "generate", prompt=prompt, shape="anim-explainer", source=source)
    except Exception as e:  # noqa: BLE001 — viz is optional; any failure degrades to a slide
        return f"animation generation failed: {e}"
    if not isinstance(meta, dict):
        return "animation generation returned nothing"
    if not meta.get("ok") or not meta.get("html_path"):
        return meta.get("error") or "animation generation returned no page"
    res = await record_html_to_mp4(
        app.service("playwright"), app.vault_root / meta["html_path"], out_mp4,
        duration_s=record_s, viewport=VIEWPORT, context_id=context_id,
    )
    if not res.get("ok"):
        return res.get("error") or "recording failed"
    # A clip the recorder could not seek comes out frozen — catch it here.
    verdict = await review_video(out_mp4, require_audio=False)
    if not verdict.ok:
        return "; ".join(verdict.hard) or "the recorded clip failed review"
    return None


async def _vs_visuals(ctx) -> dict:
    app = ctx.app
    script = _approved_script(ctx)
    scenes = script.get("scenes") or []
    narration = ctx.result("narration") or {}
    timeline = plan_timeline(narration.get("durations_ms") or [])
    if len(timeline) != len(scenes):
        raise RuntimeError("the narration and the script disagree on the scene count — regenerate the video")
    plugin = app.service("playwright")
    if plugin is None or not await plugin.available():
        raise RuntimeError("the playwright plugin (headless Chromium) is required to render slides")

    out_dir = ctx.artifact("visuals")
    out_dir.mkdir(parents=True, exist_ok=True)
    lesson_title = script.get("title") or ctx.inputs.get("lesson_title", "")
    items: list[dict] = []
    warnings: list[str] = []
    cid = f"learn-video-{ctx.handle.run_id}"
    try:
        for i, (scene, slot) in enumerate(zip(scenes, timeline, strict=True)):
            await ctx.progress(i / len(scenes), f"Scene {i + 1} of {len(scenes)} ({scene['kind']})")
            item = {"kind": "slide", "clip_s": slot["clip_s"]}
            if scene["kind"] == "anim":
                mp4 = out_dir / f"scene_{i:02d}.mp4"
                marker = out_dir / f"scene_{i:02d}.anim"
                record_s = max(ANIM_MIN_SECONDS, min(float(slot["clip_s"]), ANIM_MAX_SECONDS))
                key = _anim_key(scene, record_s)
                # A retry of this stage must not re-pay an animation it already
                # recorded for the same brief (staged-pipeline.md: never re-pay).
                if mp4.is_file() and marker.is_file() and marker.read_text(encoding="utf-8") == key:
                    reason = None
                else:
                    reason = await _record_anim(app, scene, record_s, mp4,
                                                source=script.get("source_path", ""),
                                                context_id=f"{cid}-anim-{i}")
                    if reason is None:
                        marker.write_text(key, encoding="utf-8")
                if reason is None:
                    item.update(kind="anim", path=str(mp4))
                else:
                    warnings.append(f"scene {i + 1}: animation replaced by a slide — {reason}")
            if item["kind"] == "slide":
                page = out_dir / f"scene_{i:02d}.html"
                png = out_dir / f"scene_{i:02d}.png"
                page.write_text(render_slide_html(scene, lesson_title=lesson_title,
                                                  index=i + 1, total=len(scenes)), encoding="utf-8")
                await plugin.navigate(page.resolve().as_uri(), wait="load", context_id=cid, viewport=VIEWPORT)
                await plugin.screenshot(path=str(png), context_id=cid)
                if not png.is_file():
                    raise RuntimeError(f"scene {i + 1}: the slide screenshot was not written")
                item["path"] = str(png)
            items.append(item)
    finally:
        try:
            await plugin.close_context(cid)
        except Exception:  # noqa: BLE001 — closing a context must not mask the real failure
            pass
    await ctx.progress(1.0, f"{len(items)} scenes rendered")
    return {"scenes": items, "timeline": timeline, "warnings": warnings}


async def _vs_assemble(ctx) -> dict:
    from emptyos.sdk.media import fit_clip_to_duration, generate_srt, mux_audio, still_to_clip
    from emptyos.sdk.media.video import concat_clips, probe_duration

    visuals = ctx.result("visuals") or {}
    narration = ctx.result("narration") or {}
    items = visuals.get("scenes") or []
    timeline = visuals.get("timeline") or []
    audio = narration.get("audio") or ""
    if not items or not audio:
        raise RuntimeError("nothing to assemble — the visuals or narration stage produced no output")

    durations = [float(it["clip_s"]) for it in items]
    audio_s = await probe_duration(audio)
    shortfall = audio_s - sum(durations)
    if shortfall > 0:  # decoder rounding: never let the picture end before the voice
        durations[-1] += shortfall + 1.0 / FPS
    durations = frame_exact_durations(durations)

    clip_dir = ctx.artifact("clips")
    clip_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    for i, (item, dur) in enumerate(zip(items, durations, strict=True)):
        await ctx.progress(i / (len(items) + 2), f"Encoding scene {i + 1} of {len(items)}")
        clip = clip_dir / f"clip_{i:02d}.mp4"
        if item["kind"] == "anim":
            await fit_clip_to_duration(item["path"], clip, dur, resolution=RESOLUTION)
        else:
            await still_to_clip(item["path"], clip, dur, resolution=RESOLUTION)
        clips.append(clip)

    silent = clip_dir / "picture.mp4"
    await concat_clips(clips, silent, resolution=RESOLUTION)
    srt = ctx.artifact("captions.srt")
    generate_srt(caption_cues(timeline, _approved_script(ctx).get("scenes") or []),
                 str(srt), speaker_labels=False)
    await ctx.progress((len(items) + 1) / (len(items) + 2), "Mixing narration and captions")
    final = ctx.artifact("video.mp4")
    await mux_audio(silent, audio, final, srt_path=str(srt))
    return {"video": str(final), "audio_s": audio_s, "duration_s": await probe_duration(final)}


async def _vs_review(ctx) -> dict:
    from emptyos.sdk.media import review_video
    from emptyos.sdk.media.video import has_audio_stream

    assembled = ctx.result("assemble") or {}
    video = assembled.get("video") or ""
    audio_s = float(assembled.get("audio_s") or 0)
    # sample_frames=False: a slide video holds still frames by design, and the
    # frozen-frame check would read every one of them as a stuck recorder.
    # Animated clips were frame-reviewed one by one in `visuals`.
    verdict = await review_video(video, min_duration_s=max(1.0, audio_s - REVIEW_SLACK_S),
                                 require_audio=True, sample_frames=False)
    problems = final_review_problems(
        list(verdict.hard), has_audio=await has_audio_stream(video),
        duration_s=float(assembled.get("duration_s") or 0), audio_s=audio_s,
    )
    if problems:
        raise RuntimeError("video review failed: " + "; ".join(problems))
    return await ctx.app._publish_lesson_video(
        ctx.inputs, _approved_script(ctx), video,
        run_id=ctx.handle.run_id,
        duration_s=assembled.get("duration_s"),
        warnings=(ctx.result("visuals") or {}).get("warnings") or [],
        soft=list(verdict.soft),
    )


VIDEO_STAGES = [
    Stage("script", _vs_script, weight=2),
    Stage("narration", _vs_narration, weight=4),
    Stage("visuals", _vs_visuals, weight=5),
    Stage("assemble", _vs_assemble, weight=3),
    Stage("review", _vs_review, weight=1),
]


# ─── App-bound helpers ───────────────────────────────────────────────


def _lesson_video_enabled(self) -> bool:
    return bool(self.setting_or_config(
        "learn.feature.lesson-video.enabled", False, config_key="feature.lesson-video.enabled",
    ))


def _video_pipeline(self) -> Pipeline:
    return Pipeline(app=self, name="lesson-video", stages=VIDEO_STAGES, registry_kind=RUNS_KIND)


def _video_lesson(self, course_id: str, idx_raw) -> tuple[dict | None, dict | None, str]:
    """(lesson, course, error). The lesson carries its ``index``. Videos are
    made for reading lessons only — the same rule the course queue applies."""
    try:
        idx = int(idx_raw)
    except (TypeError, ValueError):
        return None, None, "lesson index must be an integer"
    course = self._find_course(course_id)
    if not course:
        return None, None, "course not found"
    lessons = _parse_lessons(course["fm"])
    if idx < 0 or idx >= len(lessons):
        return None, None, "lesson index out of range"
    lesson = lessons[idx]
    if (lesson.get("kind") or "read") != "read":
        return None, None, "videos are made for reading lessons only"
    if not lesson.get("slug"):
        return None, None, "this lesson has no source note to teach from"
    return {**lesson, "index": idx}, course, ""


def _video_inputs(self, course: dict, lesson: dict, course_id: str) -> dict:
    """Everything a run needs, persisted once — the run's Settings row shows
    these, never whatever the config says now."""
    providers = self.app_config("video_tts_providers", DEFAULT_TTS_PROVIDERS)
    if isinstance(providers, str):
        providers = [p.strip() for p in providers.split(",") if p.strip()]
    if not isinstance(providers, (list, tuple)):
        providers = list(DEFAULT_TTS_PROVIDERS)
    return {
        "course_id": course_id,
        "idx": lesson["index"],
        "slug": lesson.get("slug", ""),
        "lesson_title": lesson.get("title") or f"Lesson {lesson['index'] + 1}",
        "course_title": course["fm"].get("title") or course_id,
        "tts_providers": [str(p) for p in providers],
        "voice": str(self.app_config("video_voice", "") or ""),
        "resolution": VIEWPORT,
    }


async def _video_source(self, slug: str) -> dict:
    """The lesson note exactly as written: ``{body, title, path}`` or
    ``{error}``. Deliberately NOT the lesson player's annotated body — citation
    buttons and rewritten links are page markup, and they change when *other*
    notes change, which would mark a video stale for an edit it never saw."""
    if not slug:
        return {"error": "no source note"}
    try:
        res = await self.call_app("kb", "get_note", slug=slug)
    except Exception as e:  # noqa: BLE001 — surfaced in-band, never a 500
        return {"error": f"kb lookup failed: {e}"}
    if not isinstance(res, dict) or res.get("error"):
        return {"error": (res or {}).get("error") if isinstance(res, dict) else "not found"}
    props = res.get("properties") or {}
    return {
        "body": res.get("body") or "",
        "title": props.get("title") or res.get("name") or slug,
        "path": res.get("path") or "",
    }


def _open_video_run(self, course_id: str, idx: int, slug: str | None = None) -> str | None:
    """The run already working on this lesson — live, queued, paused, failed,
    or interrupted — so a second click resumes it instead of starting another.

    With ``slug``, a persisted run for the same slot but a different note (the
    lessons were reordered since) is not this lesson's run. Scans every stored
    run: a completed run's folder is removed on publish, so the registry stays
    small."""
    for run_id, live in _video_live(self).items():
        if live.get("course_id") == course_id and live.get("idx") == int(idx):
            return run_id
    for handle, state in self.runs(RUNS_KIND).recent_states(None):
        inputs = state.get("inputs") or {}
        if (inputs.get("course_id") == course_id and _as_int(inputs.get("idx")) == int(idx)
                and (slug is None or inputs.get("slug") == slug)
                and state.get("status") in ("running", "paused", "error")):
            return handle.run_id
    return None


async def _drive_video_run(self, run_id: str, *, start_inputs: dict | None = None,
                           resume_inputs: dict | None = None, stop_after: str | None = None) -> None:
    """Drive one run to its next pause/end, keeping the live map current.

    Callers mark the run live *before* scheduling this (with no await between
    their duplicate check and the mark), so two clicks cannot start two runs.
    A run that completes has already published its video, so its folder is
    removed."""
    live = _video_live(self)

    async def progress(stage, pct, detail=""):
        entry = live.get(run_id)
        if entry is not None:
            entry.update(stage=stage, pct=int(pct), detail=detail or "")

    pipe = self._video_pipeline()
    try:
        if start_inputs is not None:
            summary = await pipe.start(start_inputs, run_id=run_id, stop_after=stop_after, progress=progress)
        else:
            summary = await pipe.resume(run_id, inputs=resume_inputs, stop_after=stop_after, progress=progress)
        if summary.get("status") == "complete":
            handle = self.runs(RUNS_KIND).get(run_id)
            if handle is not None:
                await asyncio.to_thread(shutil.rmtree, handle.dir, True)
    finally:
        live.pop(run_id, None)


async def _queue_course_videos(self, course: dict, jobs: list[tuple[dict, str]], course_id: str) -> None:
    """Script every queued lesson, one after another — each still stops for
    review. One lesson failing must not strand the rest as phantom runs."""
    live = _video_live(self)
    try:
        for lesson, run_id in jobs:
            try:
                await self._drive_video_run(run_id, start_inputs=self._video_inputs(course, lesson, course_id),
                                            stop_after="script")
            except Exception:  # noqa: BLE001 — log and move on to the next lesson
                log.exception("lesson video queue: %s lesson %s failed to start", course_id, lesson.get("index"))
    finally:
        for _lesson, run_id in jobs:
            if live.get(run_id, {}).get("stage") == "queued":
                live.pop(run_id, None)


def _video_run_view(self, run_id: str) -> dict | None:
    """Progress + settings + choices for one run (the staged-pipeline UI contract)."""
    live = _video_live(self).get(run_id)
    handle = self.runs(RUNS_KIND).get(run_id)
    state = handle.read_state() if handle else None
    stages = [s.name for s in VIDEO_STAGES]
    if state is None:
        if not live:
            return None
        status = "queued" if live["stage"] == "queued" else "running"
        # Nothing is persisted yet, so there are no settings to show — the UI
        # must not invent defaults for a run that has not started.
        return {"run_id": run_id, "status": status, "stage": live["stage"], "progress": live["pct"],
                "detail": live["detail"], "completed": 0, "total": len(stages), "stages": stages,
                "settings": {}, "choices": [], "error": "", "warnings": []}

    status = state.get("status") or ""
    if live:
        # This daemon is driving it right now. The persisted status still says
        # "paused"/"error" between a resume click and the first stage write,
        # and offering Render or Retry in that window would start it twice.
        status = "running"
    elif status == "running":
        status = "interrupted"
    completed = list(state.get("completed") or [])
    results = state.get("results") or {}
    inputs = state.get("inputs") or {}
    view = {
        "run_id": run_id,
        "status": status,
        "stage": (live or {}).get("stage") or state.get("failed_stage") or state.get("stage") or "",
        "progress": (live or {}).get("pct", state.get("progress", 0)),
        "detail": (live or {}).get("detail", ""),
        "completed": len(completed),
        "total": len(stages),
        "stages": stages,
        "settings": {k: inputs[k] for k in
                     ("course_id", "idx", "lesson_title", "course_title", "tts_providers", "voice", "resolution")
                     if k in inputs},
        "choices": video_run_choices(status, completed),
        "error": state.get("error") or "",
        "warnings": (results.get("visuals") or {}).get("warnings") or [],
        "limits": {"max_anim_scenes": MAX_ANIM_SCENES},
    }
    if "script" in completed:
        script = results.get("script") or {}
        edited = inputs.get("script")
        if isinstance(edited, dict):
            norm = normalize_script(edited)
            if norm["scenes"]:
                script = {**script, "title": norm["title"] or script.get("title", ""), "scenes": norm["scenes"]}
        view["script"] = {"title": script.get("title", ""), "scenes": script.get("scenes") or []}
    return view


def _lesson_video_dir(self, course_id: str, idx: int) -> Path | None:
    if path_segment_error(course_id, "course id"):
        return None
    return self.data_dir / "videos" / course_id / str(int(idx))


def _load_video_index(self) -> dict:
    path = self.data_dir / "videos" / "index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _publish_lesson_video(self, inputs: dict, script: dict, video: str, *, run_id: str,
                                duration_s, warnings: list[str], soft: list[str]) -> dict:
    """Copy the reviewed video into the lesson's store and index it."""
    course_id = inputs.get("course_id", "")
    idx = int(inputs.get("idx", 0))
    dest = self._lesson_video_dir(course_id, idx)
    if dest is None:
        raise RuntimeError("invalid course id")
    dest.mkdir(parents=True, exist_ok=True)
    # A new file per publish, never a replace: on Windows the previous video is
    # held open while the lesson page streams it, and replacing an open file
    # fails there. The index switches to the new file; old ones are pruned
    # best-effort and a still-open one is simply left for next time.
    filename = f"video-{run_id}.mp4"
    await asyncio.to_thread(_copy_atomic, video, dest / filename)
    await asyncio.to_thread(atomic_write_text, dest / "script.json",
                            json.dumps(script, ensure_ascii=False, indent=2))
    entry = {
        "course_id": course_id,
        "idx": idx,
        "file": filename,
        "slug": inputs.get("slug", ""),
        "title": script.get("title") or inputs.get("lesson_title", ""),
        "source_sha": script.get("source_sha", ""),
        "run_id": run_id,
        "duration_s": duration_s,
        "scenes": len(script.get("scenes") or []),
        "warnings": list(warnings),
        "review_notes": list(soft),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    async with self.write_lock("learn-video-index"):
        index = self._load_video_index()
        index[_video_key(course_id, idx)] = entry
        await asyncio.to_thread(atomic_write_text, self.data_dir / "videos" / "index.json",
                                json.dumps(index, ensure_ascii=False, indent=2))
    await asyncio.to_thread(_prune_old_videos, dest, filename)
    self.spawn_background(
        self.emit("learn:video_generated", {"course_id": course_id, "idx": idx, "run_id": run_id}),
        label="learn-video-generated",
    )
    return entry


# ─── Routes ──────────────────────────────────────────────────────────


@web_route("POST", "/api/courses/{course_id}/lessons/{idx}/video")
async def api_video_start(self, request):
    """Start a lesson video. The run writes its script and pauses for review."""
    if not self._lesson_video_enabled():
        return dict(_DISABLED)
    course_id = request.path_params["course_id"]
    bad = path_segment_error(course_id, "course id")
    if bad:
        return {"error": bad}
    lesson, course, err = self._video_lesson(course_id, request.path_params["idx"])
    if err:
        return {"error": err}
    inputs = self._video_inputs(course, lesson, course_id)
    existing = self._open_video_run(course_id, lesson["index"], lesson["slug"])
    if existing:
        return {"ok": True, "run_id": existing, "existing": True}
    run_id = _new_run_id()
    _mark_video_live(self, run_id, course_id, lesson["index"], "starting")
    self.spawn_background(
        self._drive_video_run(run_id, start_inputs=inputs, stop_after="script"),
        label=f"learn-video-{run_id}",
    )
    return {"ok": True, "run_id": run_id}


@web_route("GET", "/api/video-runs/{run_id}")
async def api_video_run(self, request):
    if not self._lesson_video_enabled():
        return dict(_DISABLED)
    run_id = request.path_params["run_id"]
    bad = path_segment_error(run_id, "run id")
    if bad:
        return {"error": bad}
    return self._video_run_view(run_id) or {"error": "run not found"}


@web_route("POST", "/api/video-runs/{run_id}/resume")
async def api_video_resume(self, request):
    """Render (after script review), or retry a failed/interrupted run.

    Body ``{script?}``: an edited script, accepted only while the run is still
    at its script pause — once narration exists, editing words would desync
    the picture from the voice."""
    if not self._lesson_video_enabled():
        return dict(_DISABLED)
    run_id = request.path_params["run_id"]
    bad = path_segment_error(run_id, "run id")
    if bad:
        return {"error": bad}
    body = await self.safe_json(request)
    if not isinstance(body, dict):
        body = {}
    handle = self.runs(RUNS_KIND).get(run_id)
    state = handle.read_state() if handle else None
    if state is None:
        return {"error": "run not found"}
    if run_id in _video_live(self):
        return {"error": "this run is already running"}
    if state.get("status") == "complete":
        return {"error": "this run is already complete"}
    completed = list(state.get("completed") or [])
    resume_inputs = None
    if body.get("script") is not None:
        if completed != ["script"]:
            return {"error": "the script can only be edited before narration starts — regenerate instead"}
        norm = normalize_script(body["script"])
        if not norm["scenes"]:
            return {"error": "the edited script has no scene with narration"}
        resume_inputs = {"script": norm}
    # A retry that never produced a script (the script stage failed, or a
    # restart cut it off) must pause at the script again: narration and
    # rendering are only ever paid for after a human has read one.
    stop_after = None if "script" in completed else "script"
    inputs = state.get("inputs") or {}
    _mark_video_live(self, run_id, inputs.get("course_id", ""), _as_int(inputs.get("idx"), 0), "resuming")
    self.spawn_background(self._drive_video_run(run_id, resume_inputs=resume_inputs, stop_after=stop_after),
                          label=f"learn-video-{run_id}")
    return {"ok": True, "run_id": run_id}


@web_route("POST", "/api/video-runs/{run_id}/discard")
async def api_video_discard(self, request):
    """Delete a run folder that is not running. A published video is kept."""
    if not self._lesson_video_enabled():
        return dict(_DISABLED)
    run_id = request.path_params["run_id"]
    bad = path_segment_error(run_id, "run id")
    if bad:
        return {"error": bad}
    live = _video_live(self)
    if run_id in live:
        return {"error": "a running run cannot be discarded — wait for it to pause or finish"}
    handle = self.runs(RUNS_KIND).get(run_id)
    if handle is None:
        return {"error": "run not found"}
    inputs = (handle.read_state() or {}).get("inputs") or {}
    # Hold the run in the live map while it is deleted: a resume arriving
    # mid-delete is refused, and a start gets this run back as "existing"
    # rather than a second one racing it.
    _mark_video_live(self, run_id, inputs.get("course_id", ""), _as_int(inputs.get("idx"), 0), "discarding")
    try:
        await asyncio.to_thread(shutil.rmtree, handle.dir, True)
    finally:
        live.pop(run_id, None)
    if handle.dir.exists():
        return {"error": "the run could not be fully deleted — a file in it is in use; try again"}
    return {"ok": True, "run_id": run_id}


@web_route("GET", "/api/courses/{course_id}/lessons/{idx}/video")
async def api_lesson_video(self, request):
    """The lesson's saved video (with an out-of-date flag) and any open run."""
    if not self._lesson_video_enabled():
        return {"enabled": False}
    course_id = request.path_params["course_id"]
    bad = path_segment_error(course_id, "course id")
    if bad:
        return {"enabled": True, "error": bad}
    lesson, _course, err = self._video_lesson(course_id, request.path_params["idx"])
    if err:
        return {"enabled": True, "error": err}
    idx = lesson["index"]
    out: dict = {"enabled": True, "exists": False, "run": None}
    entry = self._load_video_index().get(_video_key(course_id, idx))
    if not video_entry_matches(entry, lesson["slug"]):
        entry = None
    if _video_file(self._lesson_video_dir(course_id, idx), entry) is not None:
        source = await self._video_source(lesson["slug"])
        # A deleted or unreadable note cannot vouch for the video either.
        current = source_sha(source["body"]) if source.get("body") else ""
        out.update(
            exists=True,
            video_url=f"/learn/api/videos/{course_id}/{idx}",
            title=entry.get("title", ""),
            duration_s=entry.get("duration_s"),
            created=entry.get("created", ""),
            warnings=entry.get("warnings") or [],
            stale=current != entry.get("source_sha"),
        )
    run_id = self._open_video_run(course_id, idx, lesson["slug"])
    if run_id:
        out["run"] = self._video_run_view(run_id)
    return out


@web_route("GET", "/api/videos/{course_id}/{idx}")
async def api_video_file(self, request):
    from starlette.responses import FileResponse, JSONResponse

    if not self._lesson_video_enabled():
        return JSONResponse(dict(_DISABLED), status_code=404)
    course_id = request.path_params["course_id"]
    bad = path_segment_error(course_id, "course id")
    if bad:
        return JSONResponse({"error": bad}, status_code=400)
    try:
        idx = int(request.path_params["idx"])
    except ValueError:
        return JSONResponse({"error": "lesson index must be an integer"}, status_code=400)
    entry = self._load_video_index().get(_video_key(course_id, idx))
    path = _video_file(self._lesson_video_dir(course_id, idx), entry)
    if path is None:
        return JSONResponse({"error": "no video for this lesson"}, status_code=404)
    return FileResponse(str(path), media_type="video/mp4")


@web_route("POST", "/api/courses/{course_id}/videos")
async def api_video_queue_course(self, request):
    """Script a video for every reading lesson that has none yet, one at a time."""
    if not self._lesson_video_enabled():
        return dict(_DISABLED)
    course_id = request.path_params["course_id"]
    bad = path_segment_error(course_id, "course id")
    if bad:
        return {"error": bad}
    course = self._find_course(course_id)
    if not course:
        return {"error": "course not found"}
    index = self._load_video_index()
    jobs: list[tuple[dict, str]] = []
    for i, lesson in enumerate(_parse_lessons(course["fm"])):
        if (lesson.get("kind") or "read") != "read" or not lesson.get("slug"):
            continue
        slug = lesson["slug"]
        if video_entry_matches(index.get(_video_key(course_id, i)), slug) or self._open_video_run(course_id, i, slug):
            continue
        run_id = _new_run_id()
        _mark_video_live(self, run_id, course_id, i, "queued", "waiting for earlier lessons")
        jobs.append(({**lesson, "index": i}, run_id))
    if jobs:
        self.spawn_background(self._queue_course_videos(course, jobs, course_id),
                              label=f"learn-video-queue-{course_id}")
    return {"ok": True, "queued": len(jobs), "run_ids": [rid for _, rid in jobs]}


async def panel_video_runs(self) -> list[dict] | None:
    """Hub rows for lesson videos waiting on a human — each opens its lesson."""
    if not self._lesson_video_enabled():
        return None
    try:
        return video_panel_rows(self.runs(RUNS_KIND).recent_states(None), set(_video_live(self)))
    except Exception:  # noqa: BLE001 — a home screen must not break on an unreadable run folder
        return None
