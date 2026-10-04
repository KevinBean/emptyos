"""HTML → MP4 recorder — deterministic frame capture of an HTML/CSS animation.

Composes two primitives EmptyOS already runs — no new dependency, no new
capability:

  * frame capture : the ``playwright`` plugin's headless Chromium (passed in
                    as the plugin/service instance; we only touch its public
                    ``navigate`` / ``eval`` / ``screenshot`` / ``close_context``
                    surface, so the plugin stays unmodified)
  * encoding      : ``emptyos.sdk.media.video.frames_to_mp4`` (ffmpeg)

This is the recorder borrowed from html-video (Open Design / nexu-io) — the one
video path EmptyOS lacked. The ``animate`` capability is diffusion (comfyui-ltx,
GPU); ``assemble_video`` is an image+audio slideshow. Neither records a live
HTML/CSS animation. This does, locally, CPU-only, deterministically.

Technique
---------
Load the page, then for each frame pause every animation via the Web Animations
API (``document.getAnimations()``) and seek its ``currentTime`` to
``frame / fps`` before screenshotting. Because we seek each animation to an
exact virtual time rather than racing the wall-clock, the output is frame-exact
regardless of how slow each screenshot is (a real-time capture loop can't hold
24 fps when a PNG screenshot costs ~40 ms).

Stepping mechanism: WAAPI seek (the one that works)
---------------------------------------------------
``getAnimations().pause()`` + set ``currentTime`` per frame. Captures any
animation that surfaces in ``document.getAnimations()`` — the **Web Animations
API** (``element.animate(...)``) and CSS ``@keyframes``. This is the path the
viz ``anim-explainer`` preset targets: it instructs the model to choreograph
with ``element.animate()`` (phases positioned in absolute time via the
``delay`` option), precisely because that is what this seek can step.
Anime.js v4's ``waapi.animate()`` module also qualifies — it wraps native
``Element.animate()``, so its per-property Animation objects appear in
``getAnimations()`` and seek correctly (probe-verified 2026-07-03); only the
Anime.js *JS engine* (``animate()`` / ``createTimeline()``) is unrecordable.

Why there is NO virtual-clock path (removed 2026-06-20)
-------------------------------------------------------
A Playwright ``page.clock`` path was added (2026-06-14) to try to step library
timelines that live OUTSIDE ``getAnimations()`` — Anime.js, GSAP, raw rAF
loops. It was **removed** after a controlled test proved it does not work: a
hand-written sequential Anime.js timeline recorded directly produced
all-final-phase frames (the timeline runs in real time and the capture freezes
on its end-state). Rather than carry a non-working path, the fix lives upstream
in *generation*: the preset + the ``viz-motion-craft-explainer`` KB pattern
steer the model to the recordable WAAPI mechanism. If a future need to record an
Anime.js/GSAP/rAF timeline appears, re-introduce a clock path only after proving
it actually steps such a timeline frame-by-frame in a real browser.

Limitation (honest)
-------------------
``<canvas>`` scenes driven by ``requestAnimationFrame`` (Three.js ``3d-scene``,
Chart.js) draw into a bitmap the screenshot captures fine, but their rAF loop is
NOT driven by the WAAPI seek, so they would record as a frozen frame — the same
reason the clock path was removed. The first consumer gates export to
``anim-explainer`` only anyway (canvas shapes are excluded from the record
allow-set in ``viz/shared.py``); lifting that gate needs a verified stepping
mechanism for canvas first.

Consumers
---------
First: ``apps/public/standard/viz`` (anim-explainer "Export MP4"). Second
expected: the MV staged-pipeline (code-driven scenes vs diffusion frames). The
≥2-consumer bar (CLAUDE.md rule 9) is why this lands as a shared SDK seam rather
than inline in viz.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from emptyos.sdk.media.video import frames_to_mp4

# Pause + seek every animation to ``t_ms``. Wrapped per-animation in try/catch
# so one broken effect can't abort the whole seek.
def _seek_expr(t_ms: float) -> str:
    return (
        "document.getAnimations().forEach(function(a){a.pause();"
        f"try{{a.currentTime={t_ms};}}catch(e){{}}}})"
    )


# Ensure the page's WAAPI animations exist before we seek them. A click-to-play
# page only creates its ``element.animate()`` effects on the play action, so
# without this they're absent from ``getAnimations()`` and the seek is a no-op
# (one static frame). Prefer an explicit ``window.EOS_PLAY()`` hook (the preset
# tells the model to expose one), then a small allow-list of play controls, then
# any <button> labelled "play". Returns the trigger used (string) or null —
# purely diagnostic. (A page that creates its animations on load matches nothing
# here, which is fine — they already exist for the seek.)
_START_PLAYBACK_EXPR = (
    "(function(){"
    "if(typeof window.EOS_PLAY==='function'){try{window.EOS_PLAY();return 'EOS_PLAY';}catch(e){}}"
    "var sel=['#playBtn','#play','.play-btn','[data-role=play]','[data-play]'];"
    "for(var i=0;i<sel.length;i++){var el=document.querySelector(sel[i]);"
    "if(el){try{el.click();return sel[i];}catch(e){}}}"
    "var btns=document.querySelectorAll('button');"
    "for(var j=0;j<btns.length;j++){if(/^\\s*play\\s*$/i.test(btns[j].textContent||'')){"
    "try{btns[j].click();return 'text:play';}catch(e){}}}"
    "return null;})()"
)


# Longest animation end time (delay + active duration) on the page, in seconds.
_DURATION_EXPR = (
    "(function(){var m=0;var L=document.getAnimations();"
    "for(var i=0;i<L.length;i++){var ef=L[i].effect;"
    "if(ef&&ef.getComputedTiming){var t=ef.getComputedTiming();"
    "var d=(t.delay||0)+(t.activeDuration||0);"
    "if(isFinite(d)&&d>m)m=d;}}return m/1000;})()"
)


# Explicit author-declared clip length, in seconds. A JS-driven page (Anime.js,
# rAF) has no entry in getAnimations(), so the WAAPI probe can't see its length;
# the viz anim-explainer preset is told to set ``window.SCENE_DURATION_MS`` so
# the recorder can size the clip exactly instead of falling back to a default.
_SCENE_DURATION_EXPR = (
    "(function(){var d=window.SCENE_DURATION_MS;"
    "return (typeof d==='number'&&isFinite(d)&&d>0)?d/1000:0;})()"
)


async def infer_duration_s(plugin, context_id: str) -> float:
    """Best-effort clip duration (s). 0.0 when undeterminable / on error.

    Tries an explicit ``window.SCENE_DURATION_MS`` hint first (the only signal
    available for JS-driven timelines), then the longest WAAPI / CSS-keyframe
    animation. An ``animation-iteration-count: infinite`` effect reports its
    ``activeDuration`` as Infinity → filtered out. Callers fall back to a
    default when this returns 0.
    """
    try:
        hint = await plugin.eval(_SCENE_DURATION_EXPR, context_id=context_id)
        val = float(hint.get("value") or 0)
        if val > 0:
            return val
    except Exception:
        pass
    try:
        res = await plugin.eval(_DURATION_EXPR, context_id=context_id)
        val = float(res.get("value") or 0)
        return val if val > 0 else 0.0
    except Exception:
        return 0.0


async def record_html_to_mp4(
    plugin,
    source: str | Path,
    out_mp4: str | Path,
    *,
    fps: int = 24,
    duration_s: float | None = None,
    max_duration_s: float = 30.0,
    settle_ms: int = 400,
    context_id: str | None = None,
    keep_frames: bool = False,
    viewport: str | dict | None = None,
) -> dict:
    """Record an HTML page's animation timeline to an MP4.

    ``plugin``    : the ``playwright`` plugin instance (``app.service("playwright")``)
    ``source``    : a filesystem path OR an ``http(s)`` / ``file://`` URL
    ``out_mp4``   : destination path (parent dirs created)
    ``duration_s``: clip length; inferred from the page's longest animation when
                    omitted, falling back to 6 s, and always clamped to
                    ``max_duration_s``.
    ``viewport``  : ``"1280x720"`` or ``{width, height}`` for this page.
                    Omitted, no size is sent and the context's current size
                    applies — normally the plugin default (1280x800), which is
                    not 16:9, so a caller assembling a 720p video must pass it
                    or every recorded frame gets letterboxed.

    Returns ``{ok, path, frames, fps, duration_s}`` or ``{ok: False, error}``.
    Never raises — capture/encoding failures come back as ``ok: False``.
    """
    if plugin is None:
        return {"ok": False, "error": "playwright plugin not available"}
    try:
        if not await plugin.available():
            return {"ok": False, "error": "playwright/chromium not installed"}
    except Exception as e:  # pragma: no cover - defensive
        return {"ok": False, "error": f"playwright probe failed: {e}"}

    src = str(source)
    url = src if "://" in src else Path(src).resolve().as_uri()
    cid = context_id or f"htmlrec-{abs(hash(src)) % 10**8}"
    frames_dir = Path(tempfile.mkdtemp(prefix="eos-htmlrec-"))
    pattern = str(frames_dir / "f_%05d.png")

    try:
        nav_kwargs = {"wait": "load", "context_id": cid}
        if viewport:
            nav_kwargs["viewport"] = viewport
        await plugin.navigate(url, **nav_kwargs)
        # Let webfonts + first layout settle so frame 0 isn't a flash of
        # unstyled / unlaid-out content. The page's animations may run during
        # this real-time wait, but the per-frame seek below pauses + rewinds each
        # one to its exact time, so the settle never offsets the capture.
        await asyncio.sleep(max(0, settle_ms) / 1000.0)

        if not duration_s or duration_s <= 0:
            duration_s = await infer_duration_s(plugin, cid) or 6.0
        duration_s = max(0.1, min(float(duration_s), float(max_duration_s)))
        nframes = max(1, int(round(duration_s * fps)))

        # Ensure the page's WAAPI animations exist before we seek them — a
        # click-to-play page only creates them on the play action. Calls
        # window.EOS_PLAY() then a small allow-list of play controls; harmless
        # (no match) on a page that creates its animations on load.
        trigger = None
        try:
            res0 = await plugin.eval(_START_PLAYBACK_EXPR, context_id=cid)
            trigger = res0.get("value")
        except Exception:
            pass

        for i in range(nframes):
            t_ms = (i / fps) * 1000.0
            # WAAPI / CSS-keyframe seek — the stepping path. Pauses every
            # element.animate()/@keyframes effect in getAnimations() and sets its
            # currentTime to t, so the screenshot captures that exact moment.
            await plugin.eval(_seek_expr(t_ms), context_id=cid)
            await plugin.screenshot(
                path=str(frames_dir / f"f_{i:05d}.png"), context_id=cid
            )

        ok = await frames_to_mp4(pattern, out_mp4, fps=fps)
        if not ok:
            return {"ok": False, "error": "ffmpeg frames→mp4 failed (is ffmpeg on PATH?)"}
        return {
            "ok": True,
            "path": str(out_mp4),
            "frames": nframes,
            "fps": fps,
            "duration_s": round(duration_s, 3),
            "playback_trigger": trigger,
        }
    except Exception as e:
        return {"ok": False, "error": f"record failed: {e}"}
    finally:
        try:
            await plugin.close_context(cid)
        except Exception:
            pass
        if not keep_frames:
            shutil.rmtree(frames_dir, ignore_errors=True)
