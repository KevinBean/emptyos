"""daily-brief — RSS/JSON feed fetch + parse.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Fetching every configured source over aiohttp (size-capped, defusedxml-hardened) and parsing RSS/Atom + JSON-feed bodies into normalized item dicts. The ingress edge of the brief.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._sources / self._int_cfg (spine) for the source list + per-feed caps.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import aiohttp
from .shared import _strip_html, _text, _UA, _MAX_FEED_BYTES

try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring  # type: ignore
except ImportError:  # pragma: no cover - fallback path
    from xml.etree.ElementTree import fromstring as _xml_fromstring
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import DailyBriefApp  # noqa: F401 — for type hints only


# ─── Bind to DailyBriefApp class as ────────────────────────────────
#   _fetch_all   = _feeds._fetch_all
#   _read_body   = _feeds._read_body  # @staticmethod
#   _fetch_one   = _feeds._fetch_one
#   _parse_feed  = _feeds._parse_feed  # @staticmethod
#   _parse_json  = _feeds._parse_json  # @staticmethod
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# ── Fetch + parse ──────────────────────────────────────────
async def _fetch_all(self) -> tuple[list[dict], list[dict]]:
    """Fetch every source concurrently. Returns (items, source_status).
    Fail-soft per source — a dead feed contributes nothing, not an error."""
    srcs = self._sources()
    per = self._int_cfg("per_source", 6)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(
        timeout=timeout, headers={"User-Agent": _UA}
    ) as session:
        results = await asyncio.gather(
            *(self._fetch_one(session, s, per) for s in srcs),
            return_exceptions=True,
        )
    items: list[dict] = []
    status: list[dict] = []
    for src, res in zip(srcs, results, strict=False):
        if isinstance(res, Exception):
            status.append({"id": src["id"], "name": src["name"], "ok": False,
                           "count": 0, "error": str(res)[:200]})
            continue
        items.extend(res)
        status.append({"id": src["id"], "name": src["name"], "ok": True,
                       "count": len(res), "error": ""})
    return items, status


@staticmethod
async def _read_body(resp) -> str:
    """Read an aiohttp response to EOF with a size cap. NB: resp.content
    .read(n) returns only the first buffered chunk (truncates larger
    bodies mid-document) — always stream via iter_chunked."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(65536):
        total += len(chunk)
        if total > _MAX_FEED_BYTES:
            raise RuntimeError(f"body exceeds {_MAX_FEED_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks).decode(resp.charset or "utf-8", errors="replace")


async def _fetch_one(self, session, src: dict, per: int) -> list[dict]:
    async with session.get(src["url"]) as resp:
        resp.raise_for_status()
        body = await self._read_body(resp)
    if src["kind"] == "json":
        parsed = self._parse_json(body)
    else:
        parsed = self._parse_feed(body)
    items = []
    for it in parsed[:per]:
        items.append({
            "source": src["name"],
            "category": src["category"],
            "title": it["title"],
            "url": it["url"],
            "summary": it["summary"][:280],
        })
    return items


@staticmethod
def _parse_feed(text: str) -> list[dict]:
    """Parse RSS 2.0 or Atom into [{title, url, summary}]. Namespace-tolerant.
    Uses defusedxml (or stdlib fallback) — any parse/entity error → []."""
    try:
        root = _xml_fromstring(text)
    except Exception:  # noqa: BLE001 - malformed/hostile feed → drop it
        return []

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    out: list[dict] = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        title = url = summary = ""
        for child in el:
            t = local(child.tag)
            if t == "title" and not title:
                title = _text(child)
            elif t == "link":
                # RSS: <link>url</link>; Atom: <link href="..."/>
                href = child.get("href")
                if href:
                    if not url or child.get("rel") in (None, "alternate"):
                        url = href.strip()
                elif not url:
                    url = _text(child)
            elif t in ("description", "summary", "content") and not summary:
                summary = _strip_html(_text(child))
        if title and url:
            out.append({"title": title, "url": url, "summary": summary})
    return out


@staticmethod
def _parse_json(text: str) -> list[dict]:
    """Best-effort JSON feed parse — JSON Feed spec or a bare list/items array."""
    import json
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    arr = data.get("items") if isinstance(data, dict) else data
    if not isinstance(arr, list):
        return []
    out = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        title = it.get("title") or it.get("name") or ""
        url = it.get("url") or it.get("link") or it.get("external_url") or ""
        summary = _strip_html(str(it.get("summary") or it.get("content_text")
                                  or it.get("description") or ""))
        if title and url:
            out.append({"title": title, "url": url, "summary": summary})
    return out
