"""Vlog — a daily personal video diary.

Record or import a short clip per day; each clip is auto-transcribed through the
``listen`` capability, laid out on a calendar/heatmap, and (optionally) compiled
into a montage. The vault owns the clips — they're recovery-critical user
content, not telemetry — so they live under
``30_Resources/EmptyOS/vlog/clips/{date}/`` and the per-day metadata note under
``30_Resources/EmptyOS/vlog/{date}.md`` (``tags: [vlog]``, queried via
``VaultLibrary``).

Composes existing primitives — no new capability, no new dependency:
  * collection : VaultLibrary (tag="vlog")
  * transcribe : self.listen() chain (cloud-consent-gated, self-routing)
  * media      : ffmpeg/ffprobe via media.py (already a runtime assumption)
  * ripple     : vlog:saved → reactor → journal breadcrumb

FFmpeg shell-outs live in media.py (multi-module pattern); this spine owns the
HTTP surface, the day-note read-modify-write (serialised with write_lock), and
the montage job lifecycle.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from pathlib import Path

from emptyos.sdk import BaseApp, scheduled, web_route
from emptyos.sdk.utils import parse_frontmatter, streak_from_dates, strip_frontmatter
from emptyos.sdk.vault_library import VaultLibrary

from . import media as _media
from . import panels as _panels

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VIDEO_EXTS = {"webm", "mp4", "mov", "m4v", "ogg", "ogv"}
_VIDEO_MIME = {
    ".webm": "video/webm", ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".m4v": "video/x-m4v", ".ogg": "video/ogg", ".ogv": "video/ogg",
}


class VlogLibrary(VaultLibrary):
    tag = "vlog"
    fields = {
        "date": str,
        "clip_count": int,
        "duration": int,
        "mood": str,
        "transcript": str,
        "topics": list,
    }
    sort_key = "date"
    sort_reverse = True
    search_fields = ["transcript", "topics", "mood"]
    fallback_folder = "30_Resources/EmptyOS/vlog"


def _safe_ext(filename: str) -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    return ext if ext in _VIDEO_EXTS else "webm"


class VlogApp(BaseApp):

    # ── FFmpeg helpers (extracted to media.py) ──
    extract_thumbnail = _media.extract_thumbnail
    extract_audio = _media.extract_audio
    probe_duration = _media.probe_duration
    compile_montage = _media.compile_montage

    # ── Hub panel (extracted to panels.py) ──
    panel_vlog_today = _panels.panel_vlog_today

    async def setup(self):
        await super().setup()
        self.lib = VlogLibrary(self)

    # ── Path helpers ─────────────────────────────────────────────────────
    def _clips_dir(self, d: str) -> Path:
        return self.vault_path(f"clips/{d}")

    def _note_path(self, d: str) -> Path:
        return self.vault_path(f"{d}.md")

    def _note_rel(self, d: str) -> str:
        return self.vault_rel(self._note_path(d))

    async def day_path(self, id: str = "") -> str:
        """Timeline entity_source — vault-relative path of a day note (or '')."""
        if not _DATE_RE.match(id or ""):
            return ""
        return self._note_rel(id) if self._note_path(id).exists() else ""

    def _list_clip_files(self, d: str) -> list[Path]:
        cdir = self._clips_dir(d)
        if not cdir.exists():
            return []
        out: list[Path] = []
        for p in cdir.iterdir():
            if p.is_file() and p.suffix.lstrip(".").lower() in _VIDEO_EXTS:
                out.append(p)
        return sorted(out, key=lambda p: p.name)

    def _scan_days(self) -> list[dict]:
        """Day rows straight from the vlog notes dir — authoritative + lag-free
        (a just-captured clip appears immediately, before VaultIndex catches the
        new file). Mirrors journal's read-by-path approach. Newest first."""
        out: list[dict] = []
        d = self.vault_dir
        if not d.exists():
            return out
        for p in sorted(d.glob("*.md"), reverse=True):
            name = p.stem
            if not _DATE_RE.match(name):
                continue
            try:
                fm = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                fm = {}
            out.append({
                "date": name,
                "clip_count": int(fm.get("clip_count") or 0),
                "duration": int(fm.get("duration") or 0),
                "mood": fm.get("mood") or "",
            })
        return out

    # ── Daily capture reminder — routes through the shared proactive_notify
    # gate (quiet-hours + dedup + daily-cap), same mechanism `countdown` uses.
    # Found dark during the 2026-08 gap-analysis pass: 1 Second Everyday's
    # whole retention mechanic is this exact evening nudge.
    # ────────────────────────────────────────────────────────────────────

    @scheduled("0 20 * * *", id="vlog-reminder")
    async def _check_reminder(self):
        today = date.today().isoformat()
        days = self._scan_days()
        today_row = next((d for d in days if d["date"] == today), None)
        if today_row and today_row.get("clip_count", 0) > 0:
            return
        await self.proactive_notify(
            "vlog",
            "No clip yet today — capture one before the day's gone.",
            dedup_key=f"vlog:{today}",
            link={"text": "Open vlog", "href": "/vlog/"},
        )

    # ── List + heatmap ───────────────────────────────────────────────────
    @web_route("GET", "/api/days")
    async def api_days(self, request):
        """Calendar data: per-day rows (newest first) + heatmap {date: count}."""
        heatmap: dict[str, int] = {}
        days: list[dict] = []
        for it in self._scan_days():
            d = it["date"]
            # Count actual clip files when the note's clip_count hasn't caught up
            # yet (the background transcribe sets it ~2s after the clip lands).
            n = it["clip_count"] or len(self._list_clip_files(d))
            heatmap[d] = n
            days.append({**it, "clip_count": n, "thumb": f"/vlog/api/thumb/{d}/0"})
        streak = streak_from_dates({d for d, n in heatmap.items() if n > 0})
        return {"days": days, "heatmap": heatmap, "count": len(days), "streak": streak}

    @web_route("GET", "/api/search")
    async def api_search(self, request):
        """Search across clip transcripts (VaultIndex-backed). Returns day rows."""
        q = (request.query_params.get("q") or "").strip()
        if not q:
            return {"results": [], "q": q}
        rows = [{
            "date": it.get("date") or "",
            "mood": it.get("mood") or "",
            "transcript": it.get("transcript") or "",
        } for it in self.lib.search(q) if _DATE_RE.match(it.get("date") or "")]
        return {"results": rows, "q": q, "count": len(rows)}

    # ── Single day ───────────────────────────────────────────────────────
    @web_route("GET", "/api/day/{day}")
    async def api_day(self, request):
        d = request.path_params["day"]
        if not _DATE_RE.match(d):
            return {"error": "bad date"}
        files = self._list_clip_files(d)
        clips = [{
            "index": i,
            "url": f"/vlog/api/clip/{d}/{i}",
            "thumb": f"/vlog/api/thumb/{d}/{i}",
        } for i in range(len(files))]
        note = self._note_path(d)
        fm: dict = {}
        body = ""
        if note.exists():
            content = note.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(content)
            body = strip_frontmatter(content)
        return {
            "date": d,
            "exists": note.exists() or bool(files),
            "mood": fm.get("mood") or "",
            "transcript": fm.get("transcript") or "",
            "duration": int(fm.get("duration") or 0),
            "topics": fm.get("topics") or [],
            "clips": clips,
            "body": body,
            "_vault_path": self._note_rel(d),
        }

    @web_route("POST", "/api/day/{day}")
    async def api_update_day(self, request):
        """Update editable per-day fields (mood, topics)."""
        d = request.path_params["day"]
        if not _DATE_RE.match(d):
            return {"error": "bad date"}
        try:
            body = await request.json()
        except Exception:
            body = {}
        updates: dict = {}
        if "mood" in body:
            updates["mood"] = str(body.get("mood") or "")
        if "topics" in body:
            t = body.get("topics")
            updates["topics"] = t if isinstance(t, list) else [
                s.strip() for s in str(t or "").split(",") if s.strip()
            ]
        if not updates:
            return {"error": "nothing to update"}
        async with self.write_lock(f"vlog:{d}"):
            self._ensure_note(d)
            self._update_note_fm(d, updates)
        return {"ok": True, "date": d}

    # ── Capture (record OR upload) ───────────────────────────────────────
    @web_route("POST", "/api/capture")
    async def api_capture(self, request):
        """Multipart: `clip` (blob), optional `date`. Stores the clip in the
        vault and kicks a background transcribe; returns immediately so the POST
        never blocks on the slow listen() step."""
        form = await request.form()
        clip_file = form.get("clip")
        if clip_file is None or not hasattr(clip_file, "read"):
            return {"error": "no clip"}
        data = await clip_file.read()
        if not data:
            return {"error": "empty clip"}
        d = (form.get("date") or "").strip() or date.today().isoformat()
        if not _DATE_RE.match(d):
            return {"error": "bad date"}
        ext = _safe_ext(getattr(clip_file, "filename", "") or "")

        async with self.write_lock(f"vlog:{d}"):
            idx = len(self._list_clip_files(d))
            cdir = self._clips_dir(d)
            cdir.mkdir(parents=True, exist_ok=True)
            clip_path = cdir / f"clip-{idx}.{ext}"
            clip_path.write_bytes(data)
            self._ensure_note(d)

        self.spawn_background(self._process_clip(d, idx, clip_path))
        return {"ok": True, "date": d, "index": idx, "status": "processing"}

    async def _process_clip(self, d: str, idx: int, clip_path: Path):
        """Background: thumbnail + audio-extract + transcribe + note append."""
        cdir = self._clips_dir(d)
        await self.extract_thumbnail(clip_path, cdir / f"clip-{idx}.jpg")

        transcript = ""
        audio = cdir / f"_audio-{idx}.m4a"
        if await self.extract_audio(clip_path, audio):
            try:
                transcript = (await self.listen(audio=str(audio)) or "").strip()
            except Exception as e:
                self.log(f"vlog transcribe failed for {d}#{idx}: {e}", level="warn")
            finally:
                audio.unlink(missing_ok=True)

        # Flatten — the day-note frontmatter serializer is line-based.
        transcript = " ".join(transcript.split())
        dur = await self.probe_duration(clip_path)
        ts = datetime.now().strftime("%H:%M")
        snippet = (transcript[:140] + "…") if len(transcript) > 140 else transcript

        async with self.write_lock(f"vlog:{d}"):
            self._append_clip_to_note(d, idx, ts, dur, transcript, snippet)

        self.spawn_background(self.emit("vlog:saved", {
            "date": d, "index": idx, "duration": dur,
            "transcript": transcript, "path": self._note_rel(d),
        }))

    # ── Day-note read-modify-write (always called under write_lock) ───────
    def _ensure_note(self, d: str) -> None:
        note = self._note_path(d)
        if note.exists():
            return
        weekday = date.fromisoformat(d).strftime("%A")
        content = (
            "---\n"
            f"date: {d}\n"
            "tags:\n  - vlog\n"
            "author: both\n"
            "clip_count: 0\n"
            "duration: 0\n"
            "mood: \n"
            'transcript: ""\n'
            "---\n\n"
            f"# {d} {weekday} — Vlog\n\n"
            "## Clips\n"
        )
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(content, encoding="utf-8")

    def _append_clip_to_note(self, d: str, idx: int, ts: str, dur: int,
                             transcript: str, snippet: str) -> None:
        note = self._note_path(d)
        content = note.read_text(encoding="utf-8", errors="replace") if note.exists() else ""
        fm = parse_frontmatter(content)
        body = strip_frontmatter(content)

        prev_t = str(fm.get("transcript") or "").strip()
        fm["clip_count"] = len(self._list_clip_files(d))
        fm["duration"] = int(fm.get("duration") or 0) + int(dur or 0)
        fm["transcript"] = (prev_t + " " + transcript).strip() if transcript else prev_t
        fm["author"] = "both"

        bullet = f"- **{ts}** ({dur}s) — {snippet}".rstrip(" —")
        if "## Clips" not in body:
            body = (body.rstrip() + "\n\n## Clips\n") if body.strip() else "## Clips\n"
        body = body.rstrip() + "\n" + bullet + "\n"

        note.write_text(_serialize_note(fm, body), encoding="utf-8")

    def _update_note_fm(self, d: str, updates: dict) -> None:
        note = self._note_path(d)
        content = note.read_text(encoding="utf-8", errors="replace") if note.exists() else ""
        fm = parse_frontmatter(content)
        body = strip_frontmatter(content)
        fm.update(updates)
        note.write_text(_serialize_note(fm, body), encoding="utf-8")

    # ── Serve clips + thumbnails ─────────────────────────────────────────
    @web_route("GET", "/api/clip/{day}/{idx}")
    async def api_clip(self, request):
        return self._serve_clip_asset(request, kind="video")

    @web_route("GET", "/api/thumb/{day}/{idx}")
    async def api_thumb(self, request):
        return self._serve_clip_asset(request, kind="thumb")

    def _serve_clip_asset(self, request, *, kind: str):
        from starlette.responses import FileResponse, JSONResponse
        d = request.path_params["day"]
        raw_idx = request.path_params["idx"]
        if not _DATE_RE.match(d) or not str(raw_idx).isdigit():
            return JSONResponse({"error": "bad request"}, status_code=400)
        idx = int(raw_idx)
        cdir = self._clips_dir(d).resolve()
        clips_root = self.vault_path("clips").resolve()
        if not str(cdir).startswith(str(clips_root)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if kind == "thumb":
            path = cdir / f"clip-{idx}.jpg"
            if not path.exists():
                return JSONResponse({"error": "not found"}, status_code=404)
            return FileResponse(str(path), media_type="image/jpeg")
        files = self._list_clip_files(d)
        if idx < 0 or idx >= len(files):
            return JSONResponse({"error": "not found"}, status_code=404)
        path = files[idx]
        mime = _VIDEO_MIME.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(str(path), media_type=mime)

    # ── Montage compile ──────────────────────────────────────────────────
    @web_route("POST", "/api/compile")
    async def api_compile(self, request):
        """Body: {period: week|month|year|all}. Stitches the first clip of each
        day in range into one montage. Runs as a background job (polled)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        period = (body.get("period") or "all").strip().lower()
        dates = self._dates_in_period(period)
        clips: list[Path] = []
        for d in dates:
            files = self._list_clip_files(d)
            if files:
                clips.append(files[0])
        if not clips:
            return {"error": "no clips in range"}
        handle = self.runs("montage").new()
        out_name = f"montage-{period}-{handle.run_id[:13]}.mp4"
        handle.write_state({
            "status": "running", "period": period,
            "clip_count": len(clips), "out_name": out_name,
        })
        self.spawn_background(self._run_montage(handle.run_id, clips, out_name))
        return {"ok": True, "run_id": handle.run_id, "status": "running", "count": len(clips)}

    async def _run_montage(self, run_id: str, clips: list[Path], out_name: str):
        reg = self.runs("montage")
        out = self.vault_path(f"outputs/{out_name}")
        try:
            await self.compile_montage(clips, out)
            reg.update_state(run_id, status="done", url=f"/vlog/api/montage/{run_id}")
            self.spawn_background(self.emit("vlog:montage_ready",
                                          {"run_id": run_id, "out": out_name}))
        except Exception as e:
            self.log(f"vlog montage failed: {e}", level="error")
            reg.update_state(run_id, status="error", error=str(e)[:300])

    @web_route("GET", "/api/compile/{run_id}")
    async def api_compile_status(self, request):
        run_id = request.path_params["run_id"]
        handle = self.runs("montage").get(run_id)
        if handle is None:
            return {"error": "unknown run"}
        return handle.read_state() or {"error": "no state"}

    @web_route("GET", "/api/montage/{run_id}")
    async def api_montage(self, request):
        from starlette.responses import FileResponse, JSONResponse
        run_id = request.path_params["run_id"]
        handle = self.runs("montage").get(run_id)
        state = handle.read_state() if handle else None
        if not state or state.get("status") != "done":
            return JSONResponse({"error": "not ready"}, status_code=404)
        out_name = state.get("out_name") or ""
        if "/" in out_name or "\\" in out_name or ".." in out_name:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        path = self.vault_path(f"outputs/{out_name}")
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(path), media_type="video/mp4")

    def _dates_in_period(self, period: str) -> list[str]:
        all_dates = sorted(
            d for d in (self._list_dates()) if _DATE_RE.match(d)
        )
        if period == "all" or not all_dates:
            return all_dates
        from datetime import timedelta
        today = date.today()
        span = {"week": 7, "month": 31, "year": 366}.get(period, 0)
        if not span:
            return all_dates
        cutoff = today - timedelta(days=span)
        return [d for d in all_dates if date.fromisoformat(d) >= cutoff]

    def _list_dates(self) -> list[str]:
        return [it["date"] for it in self._scan_days()]


def _serialize_note(fm: dict, body: str) -> str:
    """Rebuild a note from frontmatter + body. Block-style tags (CLAUDE.md
    gotcha); quote strings carrying ``:`` / ``"`` / ``[[``."""
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        elif isinstance(v, str) and ("[[" in v or '"' in v or ":" in v or v == ""):
            esc = v.replace('"', '\\"')
            lines.append(f'{k}: "{esc}"')
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    if body.strip():
        lines.append(body.rstrip())
        lines.append("")
    return "\n".join(lines)
