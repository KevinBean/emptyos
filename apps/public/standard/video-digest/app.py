"""Video Digest — turn YouTube URLs into vault Web-Clip digests.

A daemon-side reimplementation of the `yt-digest` Claude Code skill. Adds a
persistent queue, list UI, hub panel, voice intent, and (in Phase 4) KB
extraction proposals. Output notes are byte-compatible with the skill's
existing Web-Clips frontmatter so a digest from either path is interchangeable.

External tools required on PATH:
    - yt-dlp (for video metadata)
The Python dependency `youtube-transcript-api` is imported lazily so a daemon
without it still boots; the app raises a clear error on first attempt to fetch.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any

from emptyos.sdk import BaseApp, web_route, cli_command
from emptyos.sdk.utils import parse_llm_json
from emptyos.sdk.web_search import is_http_url

from . import digest as _digest
from . import listen as _listen
from . import queue as _queue


from .shared import (
    CATEGORIZE_SYSTEM,
    DEFAULT_DOMAINS,
    DIGEST_SYSTEM,
    EXTRACTION_SYSTEM,
    WEB_CLIPS_DIR,
    WEB_DIGEST_SYSTEM,
    _parse_frontmatter_from_disk,
    extract_video_id,
    is_youtube_url,
    normalize_domain,
    note_stem,
    render_clip_note,
    render_web_note,
    slugify,
)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------



class VideoDigestApp(BaseApp):

    async def setup(self) -> None:
        # self.data_dir is already data/apps/video-digest — don't re-nest.
        self._queue_path: Path = self.data_dir / "queue.json"
        self._queue_lock = asyncio.Lock()
        self._processing_lock = asyncio.Lock()
        await self._ensure_queue_file()
        await self._recover_orphaned_running()

    async def panel_inbox(self) -> dict | None:
        items = await self._read_queue()
        pending = sum(1 for it in items if it.get("status") in ("queued", "failed", "running"))
        if pending == 0:
            return None
        return {"label": "Video digests pending", "value": pending, "href": "/video-digest/"}

    async def voice_queue_video(self, url: str) -> dict:
        url = (url or "").strip()
        if not url or not extract_video_id(url):
            return {"say": "I couldn't read a YouTube URL in that. Try again."}
        item = {
            "id": "vd-" + secrets.token_hex(5),
            "url": url,
            "status": "queued",
            "queued_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        async with self._queue_lock:
            items = await self._read_queue()
            items.append(item)
            await self._write_queue(items)
        asyncio.create_task(self.emit("video-digest:queued", {"id": item["id"], "url": url}))
        return {
            "say": "Queued for digest.",
            "link": {"text": "Open video-digest", "href": "/video-digest/"},
        }

    @cli_command("video-digest")
    async def cli_video_digest(self, url: str | None = None, drain: bool = False) -> str:
        """Queue a URL (`eos video-digest <url>`) or drain the queue (`--drain`)."""
        if drain:
            processed = await self._drain_loop()
            return f"Drained {len(processed)} item(s)."
        if not url:
            items = await self._read_queue()
            lines = [f"{it.get('id')}\t{it.get('status')}\t{it.get('url')}" for it in items]
            return "\n".join(lines) if lines else "(empty queue)"

        is_web = False
        if not extract_video_id(url):
            if self._web_clip_enabled() and is_http_url(url):
                is_web = True
            else:
                return f"Could not extract YouTube video id from: {url}"
        item = {
            "id": "vd-" + secrets.token_hex(5),
            "url": url,
            "status": "queued",
            "queued_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        if is_web:
            item["kind"] = "web"
        async with self._queue_lock:
            items = await self._read_queue()
            items.append(item)
            await self._write_queue(items)
        return f"Queued {item['id']} ({url})"

    # ── Digest (extracted to digest.py) ──
    _domains                = _digest._domains
    _infer_domain           = _digest._infer_domain
    _web_clip_enabled       = _digest._web_clip_enabled
    _digest_one             = _digest._digest_one
    _digest_one_inner       = _digest._digest_one_inner
    _digest_web_inner       = _digest._digest_web_inner
    _propose_kb_extractions = _digest._propose_kb_extractions
    _scan_existing_digests  = _digest._scan_existing_digests
    api_digests             = _digest.api_digests
    api_domains             = _digest.api_domains
    api_categorize_all      = _digest.api_categorize_all

    # ── Listen (extracted to listen.py) ──
    _listen_lock             = _listen._listen_lock
    _resolve_digest          = _listen._resolve_digest
    _transcript_json_path    = _listen._transcript_json_path
    _load_transcript_json    = _listen._load_transcript_json
    _persist_transcript_json = _listen._persist_transcript_json
    api_listen               = _listen.api_listen
    api_listen_end           = _listen.api_listen_end

    # ── Queue (extracted to queue.py) ──
    _recover_orphaned_running         = _queue._recover_orphaned_running
    _ensure_queue_file                = _queue._ensure_queue_file
    _read_queue                       = _queue._read_queue
    _write_queue                      = _queue._write_queue
    _update_item                      = _queue._update_item
    _fetch_meta                       = _queue._fetch_meta
    _fetch_transcript                 = _queue._fetch_transcript
    _fetch_transcript_with_timestamps = _queue._fetch_transcript_with_timestamps
    _fail                             = _queue._fail
    api_queue                         = _queue.api_queue
    api_attach_transcript             = _queue.api_attach_transcript
    api_adopt                         = _queue.api_adopt
    api_queue_list                    = _queue.api_queue_list
    api_run                           = _queue.api_run
    api_drain                         = _queue.api_drain
    _drain_loop                       = _queue._drain_loop
    api_cancel                        = _queue.api_cancel
    api_delete                        = _queue.api_delete
