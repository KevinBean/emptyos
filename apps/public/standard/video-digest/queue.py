"""video-digest — video-digest queue lifecycle: enqueue, fetch, run, drain, cancel.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Queue file I/O + per-item update helper, transcript + metadata fetchers (yt-dlp + youtube-transcript-api), drain orchestrator, public endpoints for queue/attach/adopt/run/drain/cancel/delete + orphan recovery on boot.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._digest_one (digest) when dispatching a queued item.
Do not import from ``.app`` (it imports us, which would cycle).
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
from typing import Any, TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.web_search import is_http_url

from .shared import _parse_frontmatter_from_disk, extract_video_id, is_youtube_url

if TYPE_CHECKING:
    from .app import VideoDigestApp  # noqa: F401 — for type hints only


# ─── Bind to VideoDigestApp class as ────────────────────────────────
#   _recover_orphaned_running          = _queue._recover_orphaned_running
#   _ensure_queue_file                 = _queue._ensure_queue_file
#   _read_queue                        = _queue._read_queue
#   _write_queue                       = _queue._write_queue
#   _update_item                       = _queue._update_item
#   _fetch_meta                        = _queue._fetch_meta
#   _fetch_transcript                  = _queue._fetch_transcript
#   _fetch_transcript_with_timestamps  = _queue._fetch_transcript_with_timestamps
#   _fail                              = _queue._fail
#   api_queue                          = _queue.api_queue
#   api_attach_transcript              = _queue.api_attach_transcript
#   api_adopt                          = _queue.api_adopt
#   api_queue_list                     = _queue.api_queue_list
#   api_run                            = _queue.api_run
#   api_drain                          = _queue.api_drain
#   _drain_loop                        = _queue._drain_loop
#   api_cancel                         = _queue.api_cancel
#   api_delete                         = _queue.api_delete
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def _recover_orphaned_running(self) -> None:
    # Any item left status=running at boot is orphaned — the asyncio task
    # that owned it died with the previous daemon process. Flip back to
    # queued so the next drain picks them up automatically; the item
    # never got a fair attempt, so it isn't really "failed". Keep the
    # interruption note in `error` for UI surfacing.
    #
    # Also sweep up legacy orphans: prior to commit 90e5258, restart-
    # interrupted items were marked status=failed with this exact error
    # string and stayed in the queue forever (persona-reported
    # vd-37ce60ea9a sat from 2026-05-17 onward). The `error` string is
    # the dedup key — only items we ourselves wrote get re-queued; a
    # user-typed message wouldn't match.
    async with self._queue_lock:
        items = await self._read_queue()
        now = datetime.datetime.now().isoformat(timespec="seconds")
        changed = False
        for it in items:
            if it.get("status") == "running":
                it["status"] = "queued"
                it["error"] = "interrupted by daemon restart"
                it["updated_at"] = now
                changed = True
            elif (
                it.get("status") == "failed" and it.get("error") == "interrupted by daemon restart"
            ):
                it["status"] = "queued"
                it["updated_at"] = now
                changed = True
        if changed:
            await self._write_queue(items)


async def _ensure_queue_file(self) -> None:
    if not self._queue_path.exists():
        self._queue_path.write_text(
            json.dumps({"items": []}, indent=2),
            encoding="utf-8",
        )


async def _read_queue(self) -> list[dict]:
    try:
        payload = json.loads(self._queue_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {"items": []}
    return payload.get("items") or []


async def _write_queue(self, items: list[dict]) -> None:
    self._queue_path.write_text(
        json.dumps({"items": items}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


async def _update_item(self, item_id: str, patch: dict) -> dict | None:
    async with self._queue_lock:
        items = await self._read_queue()
        for it in items:
            if it.get("id") == item_id:
                it.update(patch)
                it["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                await self._write_queue(items)
                return it
        return None


async def _fetch_meta(self, video_id: str) -> dict:
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp not found in PATH; install via `pip install yt-dlp`")

    def _run() -> dict:
        cp = subprocess.run(
            [
                "yt-dlp",
                "--skip-download",
                "--print-json",
                "--no-playlist",
                f"https://www.youtube.com/watch?v={video_id}",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        info = json.loads(cp.stdout)
        return {
            "id": info.get("id"),
            "title": info.get("title") or "Untitled",
            "channel": info.get("channel") or info.get("uploader") or "Unknown",
            "duration_s": info.get("duration") or 0,
            "upload_date": info.get("upload_date"),
            "url": info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail": info.get("thumbnail"),
            "description": (info.get("description") or "")[:2000],
        }

    return await asyncio.to_thread(_run)


async def _fetch_transcript(self, video_id: str) -> str:
    def _run() -> str:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError as e:
            raise RuntimeError(
                "youtube-transcript-api not installed; `pip install youtube-transcript-api`"
            ) from e
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=["en"])
        return "\n".join(
            (getattr(s, "text", "") or "").strip()
            for s in fetched
            if (getattr(s, "text", "") or "").strip()
        )

    return await asyncio.to_thread(_run)


async def _fetch_transcript_with_timestamps(self, video_id: str) -> list[dict]:
    """Return the native youtube-transcript-api shape: ``[{text, start,
    duration}]`` per caption line. Powers the Listen mode's
    transcript-synced-to-playback feature. Distinct from
    ``_fetch_transcript`` which joins to prose for the digest pipeline."""

    def _run() -> list[dict]:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError as e:
            raise RuntimeError(
                "youtube-transcript-api not installed; `pip install youtube-transcript-api`"
            ) from e
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=["en"])
        out: list[dict] = []
        for s in fetched:
            txt = (getattr(s, "text", "") or "").strip()
            if not txt:
                continue
            out.append(
                {
                    "text": txt,
                    "start": float(getattr(s, "start", 0.0) or 0.0),
                    "duration": float(getattr(s, "duration", 0.0) or 0.0),
                }
            )
        return out

    return await asyncio.to_thread(_run)


async def _fail(self, item_id: str, error: str) -> dict:
    patched = await self._update_item(
        item_id,
        {"status": "failed", "error": error},
    ) or {"id": item_id, "status": "failed", "error": error}
    asyncio.create_task(self.emit("video-digest:failed", {"id": item_id, "error": error}))
    return patched


@web_route("POST", "/api/queue")
async def api_queue(self, request):
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url required"}
    # Non-YouTube URLs route down the generic web-clip path when the dark
    # flag is on (link-note-saver borrow); otherwise the original rejection.
    is_web = False
    if not extract_video_id(url):
        if self._web_clip_enabled() and is_http_url(url):
            is_web = True
        else:
            return {"ok": False, "error": "could not extract YouTube video id from url"}

    item: dict[str, Any] = {
        "id": "vd-" + secrets.token_hex(5),
        "url": url,
        "status": "queued",
        "queued_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    if is_web:
        item["kind"] = "web"
    # Optional manual-paste fields — used when YouTube is rate-limiting
    # this IP and the API/yt-dlp transcript endpoints are unreachable.
    pasted = (body.get("transcript") or "").strip()
    if pasted:
        item["pasted_transcript"] = pasted
        ut = (body.get("title") or "").strip()
        uc = (body.get("channel") or "").strip()
        if ut:
            item["user_title"] = ut
            item["title"] = ut
        if uc:
            item["user_channel"] = uc
            item["channel"] = uc
    async with self._queue_lock:
        items = await self._read_queue()
        items.append(item)
        await self._write_queue(items)

    asyncio.create_task(self.emit("video-digest:queued", {"id": item["id"], "url": url}))
    return {"ok": True, "item": item}


@web_route("POST", "/api/queue/{item_id}/attach-transcript")
async def api_attach_transcript(self, request):
    """Attach a pasted transcript to an existing item and re-queue it.

    Used to recover items that failed at transcript fetch (YouTube IP
    block, captions disabled). Optional title/channel overrides handle
    the case where yt-dlp metadata also failed.
    """
    item_id = request.path_params["item_id"]
    body = await self.safe_json(request)
    transcript = (body.get("transcript") or "").strip()
    if not transcript:
        return {"ok": False, "error": "transcript required"}
    patch: dict[str, Any] = {
        "pasted_transcript": transcript,
        "status": "queued",
        "error": None,
    }
    ut = (body.get("title") or "").strip()
    uc = (body.get("channel") or "").strip()
    if ut:
        patch["user_title"] = ut
        patch["title"] = ut
    if uc:
        patch["user_channel"] = uc
        patch["channel"] = uc
    patched = await self._update_item(item_id, patch)
    if not patched:
        return {"ok": False, "error": "not found"}
    return {"ok": True, "item": patched}


@web_route("POST", "/api/adopt")
async def api_adopt(self, request):
    """Adopt a digest that was written directly to the vault.

    The ``yt-digest`` Claude Code skill (and any human) can drop a Web-Clips
    note into the vault without going through this app's queue. Adoption
    inserts a queue row with ``status=done`` + ``adopted=true`` so every
    surface — CLI, EOS chat, web UI — sees the same source of truth and
    ``video-digest:digested`` fires (hub badges, voice ack, KB-extraction
    proposals downstream). Idempotent by ``digest_path``: re-adopting
    updates the existing row instead of duplicating.
    """
    body = await self.safe_json(request)
    digest_path = (body.get("digest_path") or "").strip().lstrip("/").replace("\\", "/")
    if not digest_path:
        return {"ok": False, "error": "digest_path required"}

    abs_path = self.vault_root / digest_path
    if not abs_path.exists():
        return {"ok": False, "error": f"vault note not found: {digest_path}"}

    # Force-index the path before reading. Fresh writes from outside
    # (yt-digest skill, manual curl, test fixtures) aren't in the
    # in-memory index until the watcher pumps — vault_force_index
    # closes that race so the user/test doesn't have to retry. Even
    # so, under load the indexer sometimes runs after this call
    # returns; fall back to a disk-side YAML parse to belt-and-brace.
    # Mirrors the three-tier pattern in ``_resolve_digest``
    # (see ``_parse_frontmatter_from_disk``).
    self.vault_force_index(digest_path)
    props = self.vault_get_properties(digest_path) or {}
    if not props.get("source"):
        props = props | _parse_frontmatter_from_disk(abs_path)
    url = str(props.get("source") or "").strip()
    if not is_youtube_url(url):
        return {"ok": False, "error": "note has no YouTube source URL"}

    title = str(props.get("title") or Path(digest_path).stem)
    channel = str(props.get("speaker") or props.get("channel") or "")
    try:
        duration = int(props.get("duration_s") or 0)
    except (TypeError, ValueError):
        duration = 0

    now = datetime.datetime.now().isoformat(timespec="seconds")
    async with self._queue_lock:
        items = await self._read_queue()
        existing = next((it for it in items if it.get("digest_path") == digest_path), None)
        if existing:
            existing.update(
                {
                    "status": "done",
                    "url": url,
                    "title": title,
                    "channel": channel,
                    "duration_s": duration,
                    "updated_at": now,
                    "adopted": True,
                }
            )
            item = existing
        else:
            item = {
                "id": "vd-" + secrets.token_hex(5),
                "url": url,
                "status": "done",
                "digest_path": digest_path,
                "title": title,
                "channel": channel,
                "duration_s": duration,
                "queued_at": now,
                "updated_at": now,
                "adopted": True,
            }
            items.append(item)
        await self._write_queue(items)

    asyncio.create_task(
        self.emit(
            "video-digest:digested",
            {
                "id": item["id"],
                "digest_path": digest_path,
                "url": url,
                "adopted": True,
            },
        )
    )
    return {"ok": True, "item": item}


@web_route("GET", "/api/queue")
async def api_queue_list(self, request):
    items = await self._read_queue()
    items_sorted = sorted(items, key=lambda x: x.get("queued_at", ""), reverse=True)
    return {"items": items_sorted}


@web_route("POST", "/api/run/{item_id}")
async def api_run(self, request):
    item_id = request.path_params["item_id"]
    items = await self._read_queue()
    item = next((it for it in items if it.get("id") == item_id), None)
    if not item:
        return {"ok": False, "error": "not found"}
    if item.get("status") == "running":
        return {"ok": False, "error": "already running"}

    await self._update_item(item_id, {"status": "running", "error": None})
    async with self._processing_lock:
        result = await self._digest_one(item)
    return {"ok": result.get("status") == "done", "item": result}


@web_route("POST", "/api/drain")
async def api_drain(self, request):
    """Kick off the drain in the background; return immediately.

    Each item takes minutes (yt-dlp + transcript + LLM summarisation,
    capped at ``item_timeout_s`` = 15 min), so awaiting the full sweep
    in the HTTP request blows past the upstream 60s timeout — surfaced
    by persona run 20260520T063550-ac40e9 which got HTTP 000 / 0 bytes.
    Status is observable via ``GET /api/queue`` polling.
    """
    items = await self._read_queue()
    # Drain only retries items that haven't been tried yet. `failed` items
    # stay terminal until the user explicitly retries via /api/run/{id} —
    # otherwise a permanently-failing item (bad URL, deleted video) would
    # block the queue forever, and counting failed items as "pending"
    # misleads about what drain will actually process.
    pending = [it for it in items if it.get("status") == "queued"]
    if not pending:
        return {"ok": True, "pending": 0, "started": False}
    if self._processing_lock.locked():
        return {"ok": True, "pending": len(pending), "started": False, "note": "already draining"}
    # Flip the first pending item to "running" synchronously. Otherwise the
    # background task hasn't acquired the processing lock yet and an
    # immediate follow-up GET /api/queue sees status=queued, which looks
    # like drain didn't pick the item up at all (run 20260520T063550-ac40e9).
    first = (
        await self._update_item(pending[0]["id"], {"status": "running", "error": None})
        or pending[0]
    )
    asyncio.create_task(self._drain_loop(first_item=first))
    return {"ok": True, "pending": len(pending), "started": True}


async def _drain_loop(self, first_item: dict | None = None) -> list[dict]:
    """Run every queued or failed item sequentially until the queue is empty.

    ``first_item`` is an already-claimed (status=running) item handed in by
    ``api_drain`` so the synchronous claim and the background processing
    agree on which item is first. Subsequent iterations re-read the queue.
    """
    processed: list[dict] = []
    async with self._processing_lock:
        if first_item is not None:
            processed.append(await self._digest_one(first_item))
        while True:
            items = await self._read_queue()
            pending = [it for it in items if it.get("status") == "queued"]
            if not pending:
                break
            it = pending[0]
            await self._update_item(it["id"], {"status": "running", "error": None})
            processed.append(await self._digest_one(it))
    return processed


@web_route("POST", "/api/queue/{item_id}/cancel")
async def api_cancel(self, request):
    """Mark a stuck running item as failed so the queue moves on.

    Added 2026-05-18 in response to persona-2 friction: items stuck
    ``status: running`` indefinitely with no cancel affordance. The HTTP
    cancel doesn't kill the in-flight asyncio task (that's the timeout's
    job — see _digest_one wait_for) but it flips the queue state so the
    UI shows the item is dead and api_drain doesn't loop on it forever.
    """
    item_id = request.path_params["item_id"]
    async with self._queue_lock:
        items = await self._read_queue()
        item = next((it for it in items if it.get("id") == item_id), None)
        if not item:
            return {"ok": False, "error": "not found"}
        if item.get("status") not in ("running", "queued"):
            return {
                "ok": False,
                "error": f"item is {item.get('status')!r}, can only cancel running/queued",
            }
        patched = await self._update_item(
            item_id, {"status": "failed", "error": "cancelled by user"}
        )
    return {"ok": True, "item": patched}


@web_route("DELETE", "/api/queue/{item_id}")
async def api_delete(self, request):
    item_id = request.path_params["item_id"]
    async with self._queue_lock:
        items = await self._read_queue()
        kept = [it for it in items if it.get("id") != item_id]
        if len(kept) == len(items):
            return {"ok": False, "error": "not found"}
        await self._write_queue(kept)
    return {"ok": True}
