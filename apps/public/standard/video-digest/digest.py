"""video-digest — video → vault Web-Clip digest writer.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Domain catalog + inference, the `_digest_one_inner` LLM pipeline that turns a transcript into a digested note, KB extraction proposals, and the digest listing / categorisation endpoints + sweep of existing digests.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._update_item / self._fetch_transcript_with_timestamps (queue) for state updates and transcript fetches.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import datetime
import json
import subprocess
from pathlib import Path
from emptyos.sdk import web_route
from emptyos.sdk.web_search import read_web_source, site_label, source_fencer
from typing import TYPE_CHECKING

from .shared import (
    CATEGORIZE_SYSTEM,
    DEFAULT_DOMAINS,
    DIGEST_SYSTEM,
    DIGEST_TIMESTAMP_CLAUSE,
    EXTRACTION_SYSTEM,
    WEB_CLIPS_DIR,
    WEB_DIGEST_SYSTEM,
    _DATE_PREFIX_RE,
    extract_video_id,
    format_timestamped_transcript,
    is_youtube_url,
    linkify_timestamps,
    normalize_domain,
    note_stem,
    render_clip_note,
    render_web_note,
)

if TYPE_CHECKING:
    from .app import VideoDigestApp  # noqa: F401 — for type hints only


# ─── Bind to VideoDigestApp class as ────────────────────────────────
#   _domains                 = _digest._domains
#   _infer_domain            = _digest._infer_domain
#   _web_clip_enabled        = _digest._web_clip_enabled
#   _digest_one              = _digest._digest_one
#   _digest_one_inner        = _digest._digest_one_inner
#   _digest_web_inner        = _digest._digest_web_inner
#   _propose_kb_extractions  = _digest._propose_kb_extractions
#   api_digests              = _digest.api_digests
#   api_domains              = _digest.api_domains
#   api_categorize_all       = _digest.api_categorize_all
#   _scan_existing_digests   = _digest._scan_existing_digests
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _domains(self) -> list[str]:
    configured = self.setting_or_config("video-digest.domains", None, config_key="domains")
    if isinstance(configured, str):
        # The settings textarea stores one comma-separated string; TOML gives a list.
        configured = configured.replace("\n", ",").split(",")
    if isinstance(configured, list) and configured:
        out = [str(d).strip().lower() for d in configured if str(d).strip()]
        if out:
            if "other" not in out:
                out.append("other")
            return out
    return list(DEFAULT_DOMAINS)


async def _infer_domain(self, *, title: str, speaker: str, body_excerpt: str) -> str:
    """Ask the model to pick one domain from the configured vocabulary.

    Returns a domain string guaranteed to be in the vocabulary (falls back
    to ``"other"`` on any parse failure or unrecognised output). Costs one
    LLM call with a tight ~20-token output ceiling.
    """
    domains = self._domains()
    prompt = f"TITLE: {title}\nSPEAKER: {speaker}\nEXCERPT:\n{(body_excerpt or '')[:800]}\n"
    raw = await self.think(
        prompt,
        system=CATEGORIZE_SYSTEM.format(domains=", ".join(domains)),
        domain="text",
        temperature=0.1,
        max_tokens=20,
    )
    return normalize_domain(raw, domains)


def _web_clip_enabled(self) -> bool:
    """Dark flag for the generic-URL web-clip path (link-note-saver borrow).

    Default off — with the flag dark, the queue rejects non-YouTube URLs and
    every list/scan surface is byte-identical to the pre-feature output.
    """
    return bool(self.app_config("feature.web-clip.enabled", False))


async def _digest_one(self, item: dict) -> dict:
    """Run the full pipeline for one queue item. Returns updated item.

    Wrapped in ``asyncio.wait_for`` so a single hung subprocess or
    human-only-think-provider can't wedge the whole drain. Default 15 min;
    override via ``[apps.video-digest] item_timeout_s``. On timeout the
    item is marked ``failed`` with an explicit timeout error so the queue
    moves on. Surfaced by persona run 20260517T142538-b31c2f which observed
    items stuck ``running`` for 30+ minutes with no recovery."""
    timeout_s = int(self.app_config("item_timeout_s", 900) or 900)
    try:
        return await asyncio.wait_for(self._digest_one_inner(item), timeout=timeout_s)
    except asyncio.TimeoutError:
        return await self._fail(
            item["id"],
            f"digest timed out after {timeout_s}s — likely a hung think/yt-dlp call. "
            f"Try POST /api/queue/{item['id']}/run to retry or DELETE to drop.",
        )


async def _digest_one_inner(self, item: dict) -> dict:
    if item.get("kind") == "web":
        return await self._digest_web_inner(item)
    url = item["url"]
    video_id = extract_video_id(url)
    if not video_id:
        return await self._fail(item["id"], "Could not extract video id from URL")

    pasted_transcript = (item.get("pasted_transcript") or "").strip()
    user_title = (item.get("user_title") or "").strip()
    user_channel = (item.get("user_channel") or "").strip()

    def _paste_fallback_meta(reason: str = "") -> dict:
        return {
            "id": video_id,
            "title": user_title or "Untitled",
            "channel": user_channel or "Unknown",
            "duration_s": 0,
            "upload_date": None,
            "url": url,
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "description": f"(yt-dlp metadata unavailable: {reason})" if reason else "",
        }

    # Metadata: prefer yt-dlp; fall back to user-supplied + defaults when
    # yt-dlp is rate-limited (the same IP block that hits the transcript
    # endpoint sometimes hits metadata too).
    meta: dict
    try:
        meta = await self._fetch_meta(video_id)
    except subprocess.CalledProcessError as e:
        if pasted_transcript:
            meta = _paste_fallback_meta((e.stderr or "")[:200])
        else:
            return await self._fail(item["id"], f"yt-dlp metadata failed: {(e.stderr or '')[:500]}")
    except Exception as e:
        if pasted_transcript:
            meta = _paste_fallback_meta()
        else:
            return await self._fail(item["id"], f"metadata fetch failed: {e}")

    # User overrides win when supplied alongside a pasted transcript —
    # they typed them on purpose.
    if pasted_transcript:
        if user_title:
            meta["title"] = user_title
        if user_channel:
            meta["channel"] = user_channel

    if pasted_transcript:
        transcript = pasted_transcript
    else:
        try:
            transcript = await self._fetch_transcript(video_id)
        except Exception as e:
            return await self._fail(
                item["id"],
                f"transcript fetch failed: {e} — paste the transcript manually if YouTube is rate-limiting this IP",
            )

    if not transcript.strip():
        return await self._fail(item["id"], "Empty transcript — captions may be disabled")

    # Fetch the timestamped transcript BEFORE summarising. It is fetched for
    # Listen mode regardless, so anchoring the digest to it costs one reordering
    # and no extra call — without it nothing links a claim in the summary back
    # to the second it was said. Best-effort: a failure here leaves the digest
    # unanchored rather than failing it, and Listen still lazy-fetches on demand.
    # Skipped for a pasted transcript, which has no timestamps to anchor to.
    timestamped_lines: list[dict] = []
    if not pasted_transcript:
        try:
            timestamped_lines = await self._fetch_transcript_with_timestamps(video_id)
        except Exception:
            timestamped_lines = []

    prompt_transcript = format_timestamped_transcript(timestamped_lines) if timestamped_lines else ""
    if prompt_transcript:
        system = DIGEST_SYSTEM + DIGEST_TIMESTAMP_CLAUSE
    else:
        prompt_transcript = transcript
        system = DIGEST_SYSTEM

    target_words = int(self.setting_or_config("video-digest.summary_words", 600, config_key="summary_words") or 600)
    summary_prompt = (
        f"Distill this transcript into a digest of approximately {target_words} words.\n\n"
        f"VIDEO: {meta.get('title')} — {meta.get('channel')}\n"
        f"URL: {meta.get('url')}\n\n"
        f"TRANSCRIPT:\n{prompt_transcript}\n"
    )
    try:
        summary = await self.think(
            summary_prompt,
            system=system,
            domain="text",
            temperature=0.5,
            max_tokens=4000,
        )
    except Exception as e:
        return await self._fail(item["id"], f"summarisation failed: {e}")

    # Turn whatever [mm:ss] anchors the model emitted into deep links at that
    # second. A no-op when it emitted none, so the unanchored path is unchanged.
    summary = linkify_timestamps(summary, str(meta.get("url") or ""))

    # Inline categorization — best-effort. On any failure the field is left
    # blank, and the manual "Categorize all" backfill catches it later.
    try:
        meta["domain"] = await self._infer_domain(
            title=str(meta.get("title") or ""),
            speaker=str(meta.get("channel") or ""),
            body_excerpt=summary,
        )
    except Exception:
        meta["domain"] = ""

    today = datetime.date.today().isoformat()
    stem = note_stem(meta, today)
    rel_path = f"{WEB_CLIPS_DIR}/{stem}.md"
    transcript_rel = f"{WEB_CLIPS_DIR}/{stem}.transcript.txt"
    transcript_json_rel = f"{WEB_CLIPS_DIR}/{stem}.transcript.json"

    try:
        await self.write(rel_path, render_clip_note(meta, summary))
        await self.write(transcript_rel, transcript)
        if timestamped_lines:
            payload = {
                "video_id": video_id,
                "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "lines": timestamped_lines,
            }
            await self.write(
                transcript_json_rel,
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
    except Exception as e:
        return await self._fail(item["id"], f"vault write failed: {e}")

    patched = (
        await self._update_item(
            item["id"],
            {
                "status": "done",
                "digest_path": rel_path,
                "transcript_path": transcript_rel,
                "title": meta.get("title"),
                "channel": meta.get("channel"),
                "duration_s": meta.get("duration_s"),
                "error": None,
            },
        )
        or item
    )

    self.spawn_background(
        self.emit(
            "video-digest:digested",
            {
                "id": item["id"],
                "url": url,
                "digest_path": rel_path,
                "title": meta.get("title"),
            },
        )
    )

    # Extraction is best-effort — never fail the digest because the
    # extraction LLM call hiccupped or returned malformed JSON.
    try:
        await self._propose_kb_extractions(summary, source_stem=stem)
    except Exception as e:
        # Surface as an event but leave status=done — the digest itself
        # is the load-bearing output; KB candidates are downstream nice-to-have.
        self.spawn_background(
            self.emit(
                "video-digest:kb_extraction_failed",
                {"id": item["id"], "error": str(e)[:200]},
            )
        )
    return patched


async def _digest_web_inner(self, item: dict) -> dict:
    """Generic-URL pipeline: fetch page via browse → digest → Web-Clips note.

    The web sibling of the YouTube pipeline above (link-note-saver borrow:
    one gesture, URL in → reusable note out). Items only acquire
    ``kind="web"`` at enqueue time when ``feature.web-clip.enabled`` is on,
    so this never fires on a dark deployment; the re-check here guards
    items that were queued before the flag was flipped back off.
    """
    url = item["url"]
    if not self._web_clip_enabled():
        return await self._fail(
            item["id"], "web-clip digests are disabled ([apps.video-digest] feature.web-clip.enabled)"
        )

    page_chars = int(self.app_config("web_page_chars", 16000) or 16000)
    page = await read_web_source(self, url, per_page_chars=page_chars)
    if not page.get("ok"):
        return await self._fail(item["id"], f"page fetch failed: {page.get('error')}")

    meta = {
        "title": (item.get("user_title") or page.get("title") or "").strip() or site_label(url),
        "channel": site_label(url),
        "url": url,
        "type": "article",
    }

    target_words = int(self.setting_or_config("video-digest.summary_words", 600, config_key="summary_words") or 600)
    fencer = source_fencer(self)
    summary_prompt = (
        f"Distill this web page into a reusable digest of approximately {target_words} words.\n\n"
        f"PAGE: {meta['title']} — {meta['channel']}\n"
        f"URL: {url}\n\n"
        f"PAGE TEXT:\n{fencer.wrap(page.get('text') or '', label=meta['channel'])}\n"
    )
    try:
        summary = await self.think(
            summary_prompt,
            system=fencer.system(WEB_DIGEST_SYSTEM),
            domain="text",
            temperature=0.5,
            max_tokens=4000,
        )
    except Exception as e:
        return await self._fail(item["id"], f"summarisation failed: {e}")

    try:
        meta["domain"] = await self._infer_domain(
            title=meta["title"], speaker=meta["channel"], body_excerpt=summary
        )
    except Exception:
        meta["domain"] = ""

    today = datetime.date.today().isoformat()
    stem = note_stem(meta, today)
    rel_path = f"{WEB_CLIPS_DIR}/{stem}.md"
    try:
        await self.write(rel_path, render_web_note(meta, summary))
    except Exception as e:
        return await self._fail(item["id"], f"vault write failed: {e}")

    patched = (
        await self._update_item(
            item["id"],
            {
                "status": "done",
                "digest_path": rel_path,
                "title": meta["title"],
                "channel": meta["channel"],
                "error": None,
            },
        )
        or item
    )

    self.spawn_background(
        self.emit(
            "video-digest:digested",
            {
                "id": item["id"],
                "url": url,
                "digest_path": rel_path,
                "title": meta["title"],
                "kind": "web",
            },
        )
    )

    try:
        await self._propose_kb_extractions(summary, source_stem=stem, content_label="web-page digest")
    except Exception as e:
        self.spawn_background(
            self.emit(
                "video-digest:kb_extraction_failed",
                {"id": item["id"], "error": str(e)[:200]},
            )
        )
    return patched


async def _propose_kb_extractions(
    self, summary: str, *, source_stem: str, content_label: str = "video digest"
) -> list[dict]:
    """Ask the model for KB-note candidates and file each via the review gate.

    Each successful proposal becomes a pending action card in the global
    pending dashboard. Apply → ``kb.create_note``. Reject → discarded.
    Never auto-applies — KB writes are free-form content (per
    ``.claude/rules/autopilot-grants.md`` non-grantable).

    The extract→parse→propose loop lives in ``BaseApp.propose_kb_extractions``
    (extracted on the rooms-distill second consumer, CLAUDE.md rule 9); this
    wrapper keeps video-digest's own ``EXTRACTION_SYSTEM`` + event shape.
    """
    proposed = await self.propose_kb_extractions(
        summary,
        source_ref=f"[[{source_stem}]]",
        system=EXTRACTION_SYSTEM,
        content_label=content_label,
    )
    if proposed:
        self.spawn_background(
            self.emit(
                "video-digest:kb_proposed",
                {
                    "count": len(proposed),
                    "source": source_stem,
                    "action_ids": [a.get("id") for a in proposed],
                },
            )
        )
    return proposed


@web_route("GET", "/api/digests")
async def api_digests(self, request):
    """All digests in the vault (YouTube + flagged web clips), merged with queue provenance.

    Vault is the source of truth: every web-clip-tagged note with a YouTube
    source URL counts as a digest (plus ``type: article`` notes when the
    web-clip flag is on), regardless of whether the app processed
    it or the standalone yt-digest skill did before the app existed. The
    queue overlays processing metadata (queue id, queued-at) onto matching
    vault entries; vault notes without a matching queue row are marked
    ``provenance: "skill"``.
    """
    # Vault scan — every web-clip-tagged note with a YouTube source URL.
    vault_digests = self._scan_existing_digests()

    # Queue overlay — processing metadata keyed by digest path.
    items = await self._read_queue()
    queue_by_path: dict[str, dict] = {}
    for it in items:
        path = it.get("digest_path")
        if it.get("status") == "done" and path:
            queue_by_path[path] = it

    merged: list[dict] = []
    seen_paths: set[str] = set()
    for clip in vault_digests:
        path = clip.get("digest_path") or ""
        seen_paths.add(path)
        q = queue_by_path.get(path)
        if q:
            clip["provenance"] = "app"
            clip["queue_id"] = q.get("id")
            clip["queued_at"] = q.get("queued_at")
            clip["adopted"] = bool(q.get("adopted"))
        else:
            clip["provenance"] = "skill"
            clip["adopted"] = False
        merged.append(clip)

    # Queue-tracked items whose vault path no longer exists (renamed / moved).
    for q in queue_by_path.values():
        path = q.get("digest_path") or ""
        if path in seen_paths:
            continue
        merged.append(
            {
                "queue_id": q.get("id"),
                "digest_path": path,
                "title": q.get("title"),
                "channel": q.get("channel"),
                "url": q.get("url"),
                "duration_s": q.get("duration_s"),
                "clipped": (q.get("queued_at") or "")[:10],
                "provenance": "app-orphan",
            }
        )

    # Within a clipped-date tie, newer file mtime floats up so a just-written
    # note lands at the top of its day (the skill-write path produced ties
    # ranked by vault-scan order; mtime is the natural recency signal).
    merged.sort(
        key=lambda x: (x.get("clipped") or "", x.get("_mtime") or 0.0),
        reverse=True,
    )
    for clip in merged:
        clip.pop("_mtime", None)
    return {"digests": merged, "count": len(merged)}


@web_route("GET", "/api/domains")
async def api_domains(self, request):
    """Vocabulary + counts of categorized digests.

    Powers the filter-chip row in the UI. ``uncategorized`` is the count
    of YouTube-source web-clip notes that still have an empty ``domain:``
    frontmatter field — drives the "Categorize all" button visibility.
    """
    vocabulary = self._domains()
    counts: dict[str, int] = {d: 0 for d in vocabulary}
    uncategorized = 0
    for clip in self._scan_existing_digests():
        d = (clip.get("domain") or "").strip()
        if not d:
            uncategorized += 1
            continue
        counts[d] = counts.get(d, 0) + 1
    return {
        "vocabulary": vocabulary,
        "counts": counts,
        "uncategorized": uncategorized,
    }


@web_route("POST", "/api/categorize-all")
async def api_categorize_all(self, request):
    """LLM-categorize every uncategorized YouTube-source web-clip note.

    Idempotent by default — re-running skips notes that already have a
    ``domain:`` field. Pass ``{"force": true}`` to re-categorize every
    note regardless. Sequential one-call-per-note; ~10s per note in
    practice.
    """
    body = await self.safe_json(request)
    force = bool(body.get("force"))
    web_enabled = self._web_clip_enabled()
    notes = self.vault_query(tags=["web-clip"]) or []
    processed: list[dict] = []
    for n in notes:
        props = n.get("properties", {}) or {}
        url = str(props.get("source") or "").strip()
        if not is_youtube_url(url) and not (
            web_enabled and str(props.get("type") or "") == "article"
        ):
            continue
        if not force and str(props.get("domain") or "").strip():
            continue
        path = n.get("path", "")
        try:
            body_text = self.vault_read_body(path) or ""
        except Exception:
            body_text = ""
        try:
            inferred = await self._infer_domain(
                title=str(props.get("title") or ""),
                speaker=str(props.get("speaker") or props.get("channel") or props.get("site") or ""),
                body_excerpt=body_text,
            )
        except Exception as e:
            processed.append({"path": path, "ok": False, "error": str(e)[:200]})
            continue
        try:
            self.vault_update(path, {"domain": inferred})
        except Exception as e:
            processed.append(
                {"path": path, "ok": False, "error": f"vault_update failed: {e!s:.200}"}
            )
            continue
        processed.append({"path": path, "ok": True, "domain": inferred})
    self.spawn_background(self.emit("video-digest:categorized", {"count": len(processed)}))
    ok_count = sum(1 for p in processed if p.get("ok"))
    return {"ok": True, "processed": processed, "count": len(processed), "ok_count": ok_count}


def _scan_existing_digests(self) -> list[dict]:
    """Find every web-clip-tagged note with a YouTube source URL.

    Vault-side discovery so the digest list covers notes the standalone
    ``yt-digest`` skill wrote before this app existed, plus any future
    notes added by hand that follow the same frontmatter convention.

    When ``feature.web-clip.enabled`` is on, ``type: article`` notes (the
    generic-URL web-clip path) are included too, marked ``kind: "web"``.
    With the flag dark the output is byte-identical to the YouTube-only scan.
    """
    web_enabled = self._web_clip_enabled()
    notes = self.vault_query(tags=["web-clip"]) or []
    out: list[dict] = []
    for n in notes:
        props = n.get("properties", {}) or {}
        url = str(props.get("source") or "").strip()
        is_video = is_youtube_url(url)
        if not is_video and not (web_enabled and str(props.get("type") or "") == "article"):
            continue
        path = n.get("path", "")
        clipped = str(props.get("clipped") or "").strip()
        if not clipped:
            # Fall back to YYYY-MM-DD prefix in the filename.
            m = _DATE_PREFIX_RE.match(Path(path).stem)
            clipped = m.group(1) if m else ""
        try:
            duration = int(props.get("duration_s") or 0)
        except (TypeError, ValueError):
            duration = 0
        try:
            mtime = (self.vault_root / path).stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append(
            {
                "digest_path": path,
                "url": url,
                "domain": str(props.get("domain") or "").strip(),
                "title": str(props.get("title") or Path(path).stem),
                "channel": str(
                    props.get("speaker") or props.get("channel") or props.get("site") or ""
                ),
                "duration_s": duration,
                "clipped": clipped,
                "type": str(props.get("type") or ""),
                "kind": "video" if is_video else "web",
                "_mtime": mtime,
            }
        )
    return out
