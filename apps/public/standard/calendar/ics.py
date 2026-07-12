"""Calendar — cached, read-only iCalendar subscriptions.

Configured feeds are fetched only by an explicit refresh or the daily cron.
Month rendering reads the local JSON cache, so opening Calendar never performs
an outbound request. User-provided URLs and every redirect hop are SSRF-guarded.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from emptyos.sdk import scheduled, web_route
from emptyos.sdk.web_search import is_public_web_url

if TYPE_CHECKING:
    from .app import CalendarApp  # noqa: F401

_MAX_ICS_BYTES = 2 * 1024 * 1024
_UA = "EmptyOS Calendar/1.0"


def _unescape(value: str) -> str:
    return (
        (value or "")
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_start(raw: str) -> tuple[str, str]:
    value = (raw or "").strip()
    if len(value) < 8:
        return "", ""
    try:
        day = datetime.strptime(value[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        return "", ""
    if "T" not in value or len(value) < 13:
        return day, ""
    try:
        hour, minute = int(value[9:11]), int(value[11:13])
        if value.endswith("Z"):
            utc_dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
            local = utc_dt.astimezone()
            return local.date().isoformat(), local.strftime("%H:%M")
        return day, f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError):
        return day, ""


def parse_ics(text: str, *, source: str = "Subscribed calendar") -> list[dict]:
    """Parse VEVENTs into the bounded shape Calendar's aggregator consumes."""
    events: list[dict] = []
    current: dict[str, str] | None = None
    for line in _unfold(text):
        marker = line.strip().upper()
        if marker == "BEGIN:VEVENT":
            current = {}
            continue
        if marker == "END:VEVENT":
            if current is not None:
                day, time = _parse_start(current.get("DTSTART", ""))
                title = _unescape(current.get("SUMMARY", "")).strip()
                if day and title and current.get("STATUS", "").upper() != "CANCELLED":
                    uid = current.get("UID") or hashlib.sha256(
                        f"{source}|{day}|{time}|{title}".encode()
                    ).hexdigest()[:16]
                    events.append(
                        {
                            "id": uid[:200],
                            "date": day,
                            "time": time,
                            "title": title[:500],
                            "source": source[:120],
                            "href": current.get("URL", "")[:1000],
                        }
                    )
            current = None
            continue
        if current is None or ":" not in line:
            continue
        raw_key, value = line.split(":", 1)
        key = raw_key.split(";", 1)[0].strip().upper()
        if key in {"DTSTART", "SUMMARY", "UID", "URL", "STATUS"} and key not in current:
            current[key] = value.strip()
    return events


def _ics_enabled(self) -> bool:
    live = self.setting("calendar.feature.calendar-ics-import.enabled", None)
    if live is not None:
        return bool(live)
    return bool(self.app_config("feature.calendar-ics-import.enabled", False))


def _ics_sources(self) -> list[dict]:
    raw = self.setting("calendar.ics_feed_urls", "") or ""
    values = raw if isinstance(raw, list) else re.split(r"[\r\n,]+", str(raw))
    out: list[dict] = []
    seen: set[str] = set()
    for item in values:
        value = str(item or "").strip()
        if not value:
            continue
        name, sep, url = value.partition("|")
        if not sep:
            url, name = value, "Subscribed calendar"
        name, url = name.strip() or "Subscribed calendar", url.strip()
        if not url.lower().startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "id": hashlib.sha256(url.encode("utf-8")).hexdigest()[:12],
                "name": name[:120],
                "url": url,
            }
        )
    return out[:20]


def _ics_cache_path(self):
    return self.data_dir / "ics_cache.json"


def _load_ics_cache(self) -> dict:
    try:
        data = json.loads(self._ics_cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"feeds": []}
    except Exception:
        return {"feeds": []}


def _save_ics_cache(self, data: dict) -> None:
    path = self._ics_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _fetch_ics(self, url: str) -> str:
    import aiohttp
    from yarl import URL

    async def _guard(candidate: str) -> None:
        if not await asyncio.to_thread(is_public_web_url, candidate):
            raise RuntimeError("calendar feed URL must point at a public host")

    await _guard(url)
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": _UA}) as session:
        current = url
        for _ in range(4):
            async with session.get(current, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location", "")
                    if not location:
                        raise RuntimeError("calendar feed redirect had no location")
                    current = str(URL(current).join(URL(location)))
                    await _guard(current)
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > _MAX_ICS_BYTES:
                        raise RuntimeError("calendar feed exceeds 2 MB")
                    chunks.append(chunk)
                return b"".join(chunks).decode(
                    response.charset or "utf-8", errors="replace"
                )
        raise RuntimeError("too many calendar feed redirects")


async def refresh_ics_feeds(self) -> dict:
    """Refresh every configured feed, retaining stale cache on a failed fetch."""
    if not self._ics_enabled():
        return {"enabled": False, "feeds": 0, "events": 0}
    sources = self._ics_sources()
    old = self._load_ics_cache()
    previous = {feed.get("id"): feed for feed in old.get("feeds", []) if feed.get("id")}
    feeds: list[dict] = []
    for source in sources:
        try:
            body = await self._fetch_ics(source["url"])
            events = parse_ics(body, source=source["name"])
            feeds.append(
                {
                    "id": source["id"],
                    "name": source["name"],
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "events": events[:10000],
                    "error": "",
                }
            )
        except Exception as exc:
            stale = dict(previous.get(source["id"], {}))
            stale.update({"id": source["id"], "name": source["name"], "error": str(exc)[:200]})
            stale.setdefault("events", [])
            feeds.append(stale)
    cache = {"updated_at": datetime.now(UTC).isoformat(), "feeds": feeds}
    self._save_ics_cache(cache)
    return {
        "enabled": True,
        "feeds": len(feeds),
        "events": sum(len(feed.get("events", [])) for feed in feeds),
        "errors": sum(bool(feed.get("error")) for feed in feeds),
    }


def _cached_ics_events(self, start: date, end: date) -> list[dict]:
    if not self._ics_enabled():
        return []
    low, high = start.isoformat(), end.isoformat()
    out: list[dict] = []
    for feed in self._load_ics_cache().get("feeds", []):
        for event in feed.get("events", []):
            day = event.get("date", "")
            if low <= day < high:
                out.append(event)
    return out


@scheduled("15 4 * * *", id="calendar-ics-refresh")
async def scheduled_ics_refresh(self):
    return await self.refresh_ics_feeds()


@web_route("POST", "/api/ics/refresh")
async def api_ics_refresh(self, request):
    return await self.refresh_ics_feeds()


@web_route("GET", "/api/ics/status")
async def api_ics_status(self, request):
    cache = self._load_ics_cache()
    feeds = cache.get("feeds", [])
    return {
        "enabled": self._ics_enabled(),
        "configured": len(self._ics_sources()),
        "updated_at": cache.get("updated_at", ""),
        "feeds": [
            {
                "id": feed.get("id", ""),
                "name": feed.get("name", ""),
                "events": len(feed.get("events", [])),
                "error": feed.get("error", ""),
            }
            for feed in feeds
        ],
    }
