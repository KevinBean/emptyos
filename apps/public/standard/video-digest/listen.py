"""video-digest — audio-listen mode for a finished digest (TTS + transcript persistence).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Per-digest async lock, digest path resolver, transcript-json sidecar I/O, and the `api_listen` / `api_listen_end` endpoints powering the audio listen UI.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._fetch_transcript_with_timestamps (queue) when seeding a new transcript sidecar.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
from emptyos.sdk import web_route
from typing import TYPE_CHECKING

from .shared import WEB_CLIPS_DIR, _parse_frontmatter_from_disk, extract_video_id

if TYPE_CHECKING:
    from .app import VideoDigestApp  # noqa: F401 — for type hints only


# ─── Bind to VideoDigestApp class as ────────────────────────────────
#   _listen_lock              = _listen._listen_lock
#   _resolve_digest           = _listen._resolve_digest
#   _transcript_json_path     = _listen._transcript_json_path
#   _load_transcript_json     = _listen._load_transcript_json
#   _persist_transcript_json  = _listen._persist_transcript_json
#   api_listen                = _listen.api_listen
#   api_listen_end            = _listen.api_listen_end
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _listen_lock(self, digest_path: str) -> asyncio.Lock:
    """Per-digest-path async lock. Prevents the `## Listening Sessions`
    append from racing other writers on the same vault note."""
    if not hasattr(self, "_listen_locks"):
        self._listen_locks: dict[str, asyncio.Lock] = {}
    if digest_path not in self._listen_locks:
        self._listen_locks[digest_path] = asyncio.Lock()
    return self._listen_locks[digest_path]


def _resolve_digest(self, digest_path: str) -> dict | None:
    """Return {path, props, stem, source, video_id, title} for a digest,
    or None if it doesn't exist. ``digest_path`` is vault-relative and
    always points to a `.md` web-clip note."""
    digest_path = (digest_path or "").strip().lstrip("/")
    if not digest_path or not digest_path.endswith(".md"):
        return None
    vault = self.kernel.config.notes_path
    if not vault:
        return None
    abs_path = vault / digest_path
    if not abs_path.exists():
        return None
    props = self.vault_get_properties(digest_path) or {}
    # If VaultIndex hasn't caught up yet (file just written, watcher
    # debounce still running), force-index it and re-read. Falls back to a
    # disk-side YAML parse if even that misses (rare; mainly hit by tests
    # that write fixtures inline). Without this, Listen-mode is racy:
    # `api_adopt` + a follow-on `api_listen` in the same second can crash
    # with "source is not a recognisable YouTube URL" against a freshly
    # adopted digest.
    if not props.get("source"):
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            try:
                vi.index_file(digest_path)
                props = self.vault_get_properties(digest_path) or props
            except Exception:
                pass
    if not props.get("source"):
        props = props | _parse_frontmatter_from_disk(abs_path)
    source = str(props.get("source") or "").strip()
    return {
        "path": digest_path,
        "props": props,
        "stem": Path(digest_path).stem,
        "source": source,
        "video_id": extract_video_id(source) or "",
        "title": str(props.get("title") or Path(digest_path).stem),
    }


def _transcript_json_path(self, stem: str) -> str:
    return f"{WEB_CLIPS_DIR}/{stem}.transcript.json"


async def _load_transcript_json(self, stem: str) -> dict | None:
    """Read the timestamped transcript sidecar, or None on miss."""
    rel = self._transcript_json_path(stem)
    vault = self.kernel.config.notes_path
    if not vault:
        return None
    abs_path = vault / rel
    if not abs_path.exists():
        return None
    try:
        return json.loads(abs_path.read_text(encoding="utf-8"))
    except Exception:
        return None


async def _persist_transcript_json(
    self,
    stem: str,
    video_id: str,
    lines: list[dict],
) -> None:
    payload = {
        "video_id": video_id,
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "lines": lines,
    }
    await self.write(
        self._transcript_json_path(stem),
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


@web_route("GET", "/api/listen")
async def api_listen(self, request):
    """Return everything Listen mode needs to mount a player + transcript.

    Query param: ``digest_path`` — vault-relative path to the digest
    ``.md`` note (returned by ``GET /api/digests``).

    If no `.transcript.json` sidecar exists (older digests, or the
    skill-written variant), lazy-fetch from youtube-transcript-api and
    persist for next time. On fetch failure (captions disabled,
    region-blocked, library missing), return ``{error}`` with HTTP 200
    so the UI can degrade to player-only.
    """
    digest_path = request.query_params.get("digest_path", "").strip()
    resolved = self._resolve_digest(digest_path)
    if not resolved:
        return {"error": "digest not found", "digest_path": digest_path}
    if not resolved["video_id"]:
        return {
            "error": "digest source is not a recognisable YouTube URL",
            "digest_path": digest_path,
            "source": resolved["source"],
        }

    cached = await self._load_transcript_json(resolved["stem"])
    lines: list[dict] = []
    if cached and isinstance(cached.get("lines"), list):
        lines = cached["lines"]
    else:
        try:
            lines = await self._fetch_transcript_with_timestamps(resolved["video_id"])
        except Exception as e:
            # Graceful degradation — Listen still works as player-only.
            return {
                "digest_path": digest_path,
                "video_id": resolved["video_id"],
                "video_title": resolved["title"],
                "source_url": resolved["source"],
                "transcript": [],
                "transcript_error": f"transcript unavailable: {e!s:.200}",
            }
        if lines:
            try:
                await self._persist_transcript_json(
                    resolved["stem"],
                    resolved["video_id"],
                    lines,
                )
            except Exception:
                pass  # cache-fail is non-fatal — UI still works this session

    return {
        "digest_path": digest_path,
        "video_id": resolved["video_id"],
        "video_title": resolved["title"],
        "source_url": resolved["source"],
        "transcript": lines,
    }


@web_route("POST", "/api/listen/end")
async def api_listen_end(self, request):
    """End a Listen session — append a row to the digest's
    ``## Listening Sessions`` table and emit ``english:active_listening``.

    Body: ``{digest_path, seconds, words_captured: [str]}``.
    Returns ``{ok, sessions_total}``.

    Notes on safety:
    - Vault read-modify-write is wrapped in a per-digest-path
      ``asyncio.Lock`` (see CLAUDE.md § Development Gotchas, mirrors
      journal ``_daily_lock`` + shadowing ``_passage_lock``).
    - The event emit is dispatched via ``asyncio.create_task`` OUTSIDE
      the lock so a slow handler can't deadlock concurrent end-session
      POSTs (memory: ``feedback_lock_held_emit_deadlock``,
      ``feedback_long_handler_in_http_request``).
    """
    body = await self.safe_json(request)
    digest_path = str(body.get("digest_path") or "").strip()
    try:
        seconds = max(0, int(body.get("seconds") or 0))
    except (TypeError, ValueError):
        seconds = 0
    words_raw = body.get("words_captured") or []
    if not isinstance(words_raw, list):
        words_raw = []
    # Dedupe + clean
    seen: set[str] = set()
    words: list[str] = []
    for w in words_raw:
        s = str(w or "").strip().lower()
        if s and s not in seen:
            seen.add(s)
            words.append(s)

    resolved = self._resolve_digest(digest_path)
    if not resolved:
        return {"ok": False, "error": "digest not found", "digest_path": digest_path}
    if seconds <= 0:
        return {"ok": False, "error": "seconds must be > 0"}

    async with self._listen_lock(digest_path):
        vault = self.kernel.config.notes_path
        abs_path = vault / digest_path
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"read failed: {e!s:.200}"}

        today_iso = datetime.date.today().isoformat()
        minutes = max(1, round(seconds / 60))
        words_display = ", ".join(words[:8]) if words else "—"
        row = f"| {today_iso} | {minutes} | {words_display} |"

        # Find or append the section. Idempotent on the section header
        # (we never duplicate it). Each call always adds exactly one row.
        if "## Listening Sessions" in content:
            section_marker = "## Listening Sessions"
            idx = content.index(section_marker)
            # Insert row at the END of the section (after the last row
            # we can identify; else just append the row to the doc).
            # Find the next top-level "## " heading after the section.
            tail_idx = content.find("\n## ", idx + len(section_marker))
            if tail_idx == -1:
                # Section runs to end of file
                content = content.rstrip() + "\n" + row + "\n"
            else:
                head = content[:tail_idx].rstrip()
                rest = content[tail_idx:]
                content = head + "\n" + row + "\n\n" + rest.lstrip()
        else:
            # Create section + header row + this row
            section_block = (
                "\n## Listening Sessions\n"
                "| Date | Minutes | Words captured |\n"
                "| --- | --- | --- |\n"
                f"{row}\n"
            )
            content = content.rstrip() + "\n" + section_block

        try:
            abs_path.write_text(content, encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"write failed: {e!s:.200}"}

        # Re-index the note so vault_query reflects the appended section
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            try:
                vi.index_file(digest_path)
            except Exception:
                pass

    # Outside the lock: emit aggregator event. Background task so a slow
    # listener can't block the response.
    self.spawn_background(
        self.emit(
            "english:active_listening",
            {
                "seconds": seconds,
                "minutes": minutes,
                "source_url": resolved["source"],
                "video_title": resolved["title"],
                "digest_path": digest_path,
                "words_captured": words,
            },
        )
    )
    # Tally how many sessions this digest has accumulated (count rows
    # under the section header — handy for the UI summary toast).
    sessions_total = 0
    try:
        after_content = abs_path.read_text(encoding="utf-8")
        if "## Listening Sessions" in after_content:
            section = after_content.split("## Listening Sessions", 1)[1]
            next_hdr = section.find("\n## ")
            if next_hdr != -1:
                section = section[:next_hdr]
            # Count data rows (lines starting with "| 2" — date-prefixed)
            sessions_total = sum(
                1 for line in section.splitlines() if line.startswith("| 2") and "|" in line[2:]
            )
    except Exception:
        pass

    return {
        "ok": True,
        "digest_path": digest_path,
        "minutes": minutes,
        "words": words,
        "sessions_total": sessions_total,
    }
