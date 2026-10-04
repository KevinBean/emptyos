"""Stock-footage plugin — injects Pexels/Pixabay providers into `footage`.

Graceful-enhancement pattern (like email-smtp → send): when an API key is
configured, the `footage` capability gains a cloud provider that turns a
keyword into a downloaded royalty-free clip; when not, the chain falls through
to the human fallback (interactive) or raises (daemon) and apps degrade.

This is the EmptyOS port of MoneyPrinterTurbo's `app/services/material.py`
primitive — the one thing MPT had that we lacked. Kept deliberately small:
search → pick best file by orientation/duration → content-addressed download
cache (`vid-<md5(url)>.mp4`, shared across runs).

Config in emptyos.toml (gitignored — keeps keys out of git per Rule 13):

    [plugins.footage]
    pexels_api_key_env = "PEXELS_API_KEY"     # env var holding the key
    pixabay_api_key_env = "PIXABAY_API_KEY"   # optional second provider
    per_page = 15
    timeout = 60

Both providers declare `trust = "service"`, so they are cloud-classified
whatever their host looks like, and the first fetch passes the consent gate. Grant
once (consent policy "always" for `pexels`/`pixabay`) and clips flow without a
per-call prompt.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path

import aiohttp

from emptyos.capabilities import Provider
from emptyos.sdk import BasePlugin

_ORIENTATION = {"landscape", "portrait", "square"}


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()  # noqa: S324 — cache key, not security


class FootagePlugin(BasePlugin):
    name = "footage"

    async def connect(self):
        cap = self.kernel.capabilities.get("footage")
        if cap is None:
            return

        pexels_key = self._key("pexels_api_key_env", "PEXELS_API_KEY")
        pixabay_key = self._key("pixabay_api_key_env", "PIXABAY_API_KEY")
        if not (pexels_key or pixabay_key):
            # Shipped in `standard`, so most installs land here: no network, no
            # directory, one line saying why the chain is human-only.
            print("[footage] no Pexels/Pixabay key — `footage` has only the human fallback")
            return

        cache_dir = self._cache_dir()
        timeout = _positive_int(self.config("timeout", 60), 60)
        per_page = _positive_int(self.config("per_page", 15), 15)
        registered = []
        # priority 0 — both tried before the human fallback, in declared order.
        if pexels_key:
            cap.add_provider(
                PexelsFootageProvider(pexels_key, cache_dir, per_page, timeout), priority=0
            )
            registered.append("pexels")
        if pixabay_key:
            cap.add_provider(
                PixabayFootageProvider(pixabay_key, cache_dir, per_page, timeout), priority=0
            )
            registered.append("pixabay")

        print(f"[footage] providers: {', '.join(registered)} (cache: {cache_dir})")

    def _key(self, env_cfg: str, default_env: str) -> str:
        env_name = str(self.config(env_cfg, default_env) or default_env).strip()
        return (os.environ.get(env_name, "") or "").strip()

    def _cache_dir(self) -> Path:
        # Under the configured machine-state root, like every other plugin: a
        # Docker deployment mounts `data_dir`, and a path beside the config file
        # would put clips in the container's writable layer.
        d = Path(self.kernel.config.data_dir) / "footage" / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _positive_int(value, default: int) -> int:
    """A config number, or `default` when it is missing, malformed or < 1 —
    a typo in emptyos.toml must not stop the plugin loading."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _write_atomic(dest: Path, data: bytes) -> None:
    """Write via a unique temp file and `os.replace`, so the cache never holds a
    truncated clip (the cache-hit check trusts any non-empty file forever) and
    two concurrent fetches of one URL cannot interleave into one file."""
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


class _BaseFootageProvider(Provider):
    """Shared search→pick→download→cache machinery. Subclasses implement
    `_search` (provider API) and set `host`/`name`."""

    def __init__(self, api_key: str, cache_dir: Path, per_page: int, timeout: int):
        self._key = api_key
        self._cache = cache_dir
        self._per_page = per_page
        self._timeout = timeout

    async def available(self) -> bool:
        return bool(self._key)

    async def health(self) -> dict:
        if self._key:
            return {"available": True, "reason": None, "recovery": None}
        return {
            "available": False,
            "reason": f"{self.name} API key not set",
            "recovery": {"kind": "config", "path": "emptyos.toml", "section": "[plugins.footage]"},
        }

    def consent_summary(self, **kwargs) -> str:
        return f"Search {self.name} stock video for: {kwargs.get('query', '')!r}"

    async def execute(
        self, *, query: str, orientation: str = "landscape",
        min_duration: float = 0.0, **kwargs,
    ) -> dict:
        if orientation not in _ORIENTATION:
            orientation = "landscape"
        empty = {"path": "", "provider": self.name, "url": "",
                 "duration": 0.0, "width": 0, "height": 0}
        if not (query or "").strip():
            return empty
        async with aiohttp.ClientSession() as session:
            try:
                candidates = await self._search(session, query.strip(), orientation)
            except Exception:
                return empty
            # Pick the first candidate that meets the duration floor (already
            # ordered best-first by the subclass), else the first overall.
            pick = next((c for c in candidates if c["duration"] >= min_duration), None)
            if pick is None:
                pick = candidates[0] if candidates else None
            if pick is None:
                return empty
            path = await self._download(session, pick["url"])
            if not path:
                return empty
            return {"path": path, "provider": self.name, "url": pick["url"],
                    "duration": pick["duration"], "width": pick["width"],
                    "height": pick["height"]}

    async def _search(self, session, query: str, orientation: str) -> list[dict]:
        """Return candidate clips best-first: [{url, duration, width, height}]."""
        raise NotImplementedError

    async def _download(self, session, url: str) -> str:
        """Content-addressed download into the shared cache. Returns local path."""
        dest = self._cache / f"vid-{_md5(url.split('?')[0])}.mp4"
        if dest.exists() and dest.stat().st_size > 0:
            return str(dest)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=self._timeout)) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.read()
            if not data:
                return ""
            # Off the event loop: a clip is tens of MB.
            try:
                await asyncio.to_thread(_write_atomic, dest, data)
            except OSError:
                # Windows refuses to replace a file another process holds open
                # (a concurrent fetch of the same URL, or a player reading it).
                # The clip already there is a complete one — use it.
                if dest.exists() and dest.stat().st_size > 0:
                    return str(dest)
                raise
            return str(dest)
        except Exception:
            return ""


class PexelsFootageProvider(_BaseFootageProvider):
    name = "pexels"
    host = "https://api.pexels.com"
    trust = "service"  # declared, not inferred from the host (rented-compute.md)
    metered = False  # free API key; no per-call charge for the spend cap to count

    async def _search(self, session, query: str, orientation: str) -> list[dict]:
        url = f"{self.host}/videos/search"
        params = {"query": query, "per_page": str(self._per_page), "orientation": orientation}
        headers = {"Authorization": self._key}
        async with session.get(
            url, params=params, headers=headers,
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        out = []
        for vid in data.get("videos", []):
            files = [f for f in vid.get("video_files", [])
                     if (f.get("file_type") or "").endswith("mp4") and f.get("link")]
            # Prefer the largest file at or below 1080p (avoid huge 4K downloads).
            files.sort(key=lambda f: (f.get("width") or 0), reverse=True)
            best = next((f for f in files if (f.get("width") or 0) <= 1920), None) or (files[0] if files else None)
            if not best:
                continue
            out.append({
                "url": best["link"],
                "duration": float(vid.get("duration", 0) or 0),
                "width": int(best.get("width") or 0),
                "height": int(best.get("height") or 0),
            })
        return out


class PixabayFootageProvider(_BaseFootageProvider):
    name = "pixabay"
    host = "https://pixabay.com"
    trust = "service"
    metered = False  # free API key; no per-call charge for the spend cap to count

    async def _search(self, session, query: str, orientation: str) -> list[dict]:
        url = f"{self.host}/api/videos/"
        params = {"q": query, "per_page": str(max(3, self._per_page)),
                  "key": self._key, "video_type": "film"}
        async with session.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=self._timeout),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        out = []
        for hit in data.get("hits", []):
            streams = hit.get("videos", {}) or {}
            # medium is a good size/quality balance; fall back through the ladder.
            stream = streams.get("medium") or streams.get("large") or streams.get("small") or {}
            link = stream.get("url")
            if not link:
                continue
            out.append({
                "url": link,
                "duration": float(hit.get("duration", 0) or 0),
                "width": int(stream.get("width") or 0),
                "height": int(stream.get("height") or 0),
            })
        return out
