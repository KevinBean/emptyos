"""Picture Dictionary — photo lookup, download cache, and the prefetch job.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md rule 4),
and kept separate regardless of size: this is the only module carrying network,
background-task and SSRF concerns, and mixing it into the spine would make that
review surface impossible to keep small.

Owns: the two-phase Wikimedia lookup, the content-addressed image cache under
``data/apps/dictionary/images/``, its ``_index.json`` (which also carries the
per-photo licence credit), and the background prefetch job.

Why two phases rather than the simpler REST summary endpoint:

* ``prop=pageimages&pilicense=free`` names the article's lead file **and excludes
  non-free ones**, which is what stops us caching an image we may not display.
* ``prop=imageinfo&iiprop=url|extmetadata`` then yields a server-resized URL plus
  the artist and licence. Attribution has to be captured *here*, at fetch time —
  a later offline app cannot re-derive it.

Measured behaviour worth not rediscovering: the service serves only a fixed set of
thumbnail widths. ``iiurlwidth=800`` reports ``thumbwidth: 800`` while returning a
``960px-`` URL, and a hand-built ``640px-`` URL is rejected outright with
``HTTP 400: Use thumbnail sizes listed on ...``. So never construct a thumbnail
URL — always fetch the one the API returned, verbatim.

Cross-module callers reach these via ``self.X`` after re-binding.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.utils import safe_path_segment
from emptyos.sdk.web_search import is_public_web_url

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only


# ─── Bind to DictionaryApp class as ─────────────────────────────────
#   load_index          = _images.load_index
#   save_index          = _images.save_index
#   image_url           = _images.image_url
#   slugs_with_image    = _images.slugs_with_image
#   image_credit        = _images.image_credit
#   coverage            = _images.coverage
#   start_prefetch      = _images.start_prefetch
#   _prefetch_loop      = _images._prefetch_loop
#   _fetch_one          = _images._fetch_one
#   _lookup_lead_files  = _images._lookup_lead_files
#   _lookup_file_info   = _images._lookup_file_info
#   _download           = _images._download
#   _api_get            = _images._api_get
#   api_picture_image           = _images.api_image
#   api_picture_prefetch        = _images.api_prefetch
#   api_picture_prefetch_status = _images.api_prefetch_status
#   api_picture_prefetch_cancel = _images.api_prefetch_cancel
#   api_picture_refetch         = _images.api_refetch
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

COMMONS_HOST = "commons.wikimedia.org"
BATCH = 50
ALLOWED_IMAGE_HOSTS = (".wikimedia.org", ".wikipedia.org")
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_BYTES = 8 * 1024 * 1024
MAX_ATTEMPTS = 3
INDEX_FLUSH_EVERY = 10
# `image_hint` value meaning "the lead image is wrong and no better file was
# chosen" — the object keeps its emoji tile instead of a misleading photo.
NO_PHOTO = "none"


def _is_no_photo(item: dict) -> bool:
    return (item.get("image_hint") or "").strip() == NO_PHOTO

_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _TAG.sub("", s or "").strip()


def _title_key(name: str) -> str:
    """Normalise a file title for round-tripping between the two API phases.

    MediaWiki treats ``_`` and space as the same character in a title and echoes
    back the *space* form, while ``pageimage`` reports the *underscore* form. So
    a lookup keyed on the raw Phase-A name misses every file whose name contains
    a space — which is most of them. That mismatch silently failed 45 of 49
    photos before this existed: every animal whose lead image had a multi-word
    filename, which is nearly all of them.
    """
    return re.sub(r"^File:", "", (name or "").replace("_", " ")).strip()


def _cache_name(slug: str, url: str) -> str:
    """Content-addressed on the URL (query stripped), prefixed with the slug so
    the cache stays humanly debuggable. Mirrors the footage plugin."""
    digest = hashlib.md5(url.split("?")[0].encode("utf-8")).hexdigest()[:10]
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if ext not in ALLOWED_EXT:
        # Many taxon "images" on Commons are SVG range maps. Refusing anything
        # but a raster keeps a diagram out of a photo gallery, and keeps an
        # inline-scriptable format out of an <img> we serve.
        ext = ".jpg"
    return f"{safe_path_segment(slug)}-{digest}{ext}"


# ─── Index ───────────────────────────────────────────────────────────


def load_index(self) -> dict:
    p = self.data_subdir("images") / "_index.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_index(self, idx: dict) -> None:
    p = self.data_subdir("images") / "_index.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def image_url(self, slug: str) -> str:
    """The servable URL for a slug's photo, or "" when we have none. The UI
    falls back to the emoji tile on "", so this must never guess."""
    rec = self._index.get(slug) or {}
    if rec.get("status") != "ok" or not rec.get("file"):
        return ""
    if not (self.data_subdir("images") / rec["file"]).exists():
        return ""
    return f"/dictionary/api/picture/image/{urllib.parse.quote(slug)}"


def slugs_with_image(self) -> set:
    return {s for s in self.items if self.image_url(s)}


def image_credit(self, slug: str) -> dict:
    rec = self._index.get(slug) or {}
    if rec.get("status") != "ok":
        return {}
    return {"artist": rec.get("artist", ""), "license": rec.get("license", ""),
            "license_url": rec.get("license_url", ""),
            "source": rec.get("descriptor_url", "")}


def coverage(self) -> dict:
    ok = sum(1 for s in self.items if self.image_url(s))
    idx = self._index
    failed = sum(1 for s in self.items
                 if not _is_no_photo(self.items[s])
                 and (idx.get(s) or {}).get("status") in ("failed", "nopic"))
    return {"total": len(self.items), "with_photo": ok,
            "missing": len(self.items) - ok, "failed": failed}


# ─── Wikimedia ───────────────────────────────────────────────────────


async def _api_get(self, host: str, params: dict) -> dict:
    """One throttled GET against a MediaWiki action API.

    Blocking urllib work runs in a thread — doing it inline would pin the whole
    event loop, and the bus runs handlers serially in the same task.
    """
    await self._throttle()
    url = f"https://{host}/w/api.php?" + urllib.parse.urlencode(params)
    ua = self._user_agent()

    def _fetch():
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    return await asyncio.to_thread(_fetch)


async def _lookup_lead_files(self, titles: list[str]) -> dict:
    """Phase A — article title -> the *free-licensed* lead image filename."""
    out: dict[str, str] = {}
    for i in range(0, len(titles), BATCH):
        chunk = titles[i:i + BATCH]
        try:
            d = await self._api_get(self._wiki_host(), {
                "action": "query", "format": "json", "formatversion": "2",
                "redirects": "1", "prop": "pageimages", "pilicense": "free",
                "piprop": "name", "titles": "|".join(chunk),
            })
        except Exception:
            continue
        q = d.get("query", {})
        norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        redir = {r["from"]: r["to"] for r in q.get("redirects", [])}
        pages = {p.get("title"): p for p in q.get("pages", [])}
        for t in chunk:
            resolved = redir.get(norm.get(t, t), norm.get(t, t))
            f = (pages.get(resolved) or {}).get("pageimage")
            if f:
                out[t] = f
    return out


async def _lookup_file_info(self, files: list[str], width: int) -> dict:
    """Phase B — File: title -> sized URL plus the licence credit."""
    out: dict[str, dict] = {}
    for i in range(0, len(files), BATCH):
        chunk = files[i:i + BATCH]
        try:
            d = await self._api_get(COMMONS_HOST, {
                "action": "query", "format": "json", "formatversion": "2",
                "prop": "imageinfo", "iiprop": "url|extmetadata",
                "iiurlwidth": str(width),
                "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl",
                "titles": "|".join(f"File:{f}" for f in chunk),
            })
        except Exception:
            continue
        for p in d.get("query", {}).get("pages", []):
            ii = (p.get("imageinfo") or [{}])[0]
            em = ii.get("extmetadata", {}) or {}
            title = _title_key(p.get("title", ""))
            url = ii.get("thumburl") or ii.get("url") or ""
            if not url:
                continue
            out[title] = {
                "url": url,
                "descriptor_url": ii.get("descriptionurl", ""),
                "artist": _strip_html(em.get("Artist", {}).get("value", ""))[:120],
                "license": _strip_html(em.get("LicenseShortName", {}).get("value", "")),
                "license_url": em.get("LicenseUrl", {}).get("value", ""),
            }
    return out


async def _download(self, slug: str, url: str) -> tuple[str, str]:
    """Fetch one image into the cache. Returns ``(filename, error)``."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not host.endswith(ALLOWED_IMAGE_HOSTS):
        return "", f"refusing off-domain image host {host!r}"
    if not await asyncio.to_thread(is_public_web_url, url):
        return "", "refusing non-public image url"

    dest = self.data_subdir("images") / _cache_name(slug, url)
    if dest.exists() and dest.stat().st_size > 0:
        return dest.name, ""  # idempotent: re-running prefetch costs nothing

    await self._throttle()
    ua = self._user_agent()

    def _fetch():
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.read(MAX_BYTES + 1)

    try:
        blob = await asyncio.to_thread(_fetch)
        if not blob:
            return "", "empty response"
        if len(blob) > MAX_BYTES:
            return "", "image larger than the 8 MB ceiling"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        return dest.name, ""
    except Exception as e:
        # A half-written file must never look like a warm cache entry.
        dest.unlink(missing_ok=True)
        return "", f"{type(e).__name__}: {e}"


async def _fetch_one(self, slug: str, width: int) -> dict:
    """Resolve and download one animal's photo. Returns its index record."""
    item = self.items.get(slug)
    if not item:
        return {"status": "failed", "error": "unknown slug"}

    fname = (item.get("image_hint") or "").replace("File:", "").strip()
    if _is_no_photo(item):
        # The pack author checked the article's lead image and it shows the wrong
        # thing (an antique, a painting, a different object). A wrong photo
        # teaches the wrong word, so the emoji tile is the intended state.
        return {"status": "nopic", "error": "no suitable photo — emoji by design",
                "file": ""}
    if not fname:
        lead = await self._lookup_lead_files([item["wiki"]])
        fname = lead.get(item["wiki"], "")
    if not fname:
        # No free lead image — almost always a disambiguation page. Record it so
        # the UI keeps the emoji tile and the pack can be corrected, rather than
        # retrying forever.
        return {"status": "nopic", "error": "no free lead image for "
                                            f"{item['wiki']!r}", "file": ""}

    info = (await self._lookup_file_info([fname], width)).get(_title_key(fname))
    if not info:
        return {"status": "failed", "error": f"no image info for {fname!r}", "file": ""}

    name, err = await self._download(slug, info["url"])
    if err:
        return {"status": "failed", "error": err, "file": ""}
    return {
        "status": "ok", "file": name, "source_url": info["url"],
        "descriptor_url": info["descriptor_url"], "artist": info["artist"],
        "license": info["license"], "license_url": info["license_url"],
        "wiki": item["wiki"], "fetched": time.strftime("%Y-%m-%d"), "error": "",
    }


# ─── The background job ──────────────────────────────────────────────


def _pending(self, *, force: bool, slugs: list | None) -> list:
    idx = self._index
    # A by-design emoji card is not work to do — fetching it would only record
    # a "failure" and burn its retry budget.
    pool = [s for s in (slugs or self.items)
            if s in self.items and not _is_no_photo(self.items[s])]
    if force:
        return pool
    out = []
    for s in pool:
        rec = idx.get(s) or {}
        if rec.get("status") == "ok" and self.image_url(s):
            continue
        if int(rec.get("attempts", 0)) >= MAX_ATTEMPTS:
            continue  # stop hammering a title that will not resolve
        out.append(s)
    return out


async def start_prefetch(self, *, force: bool = False, slugs: list | None = None) -> dict:
    if self._prefetch.get("running"):
        return {"started": False, "reason": "already running", **self._prefetch}
    st = self._status()
    if not st.get("enabled", True):
        return {"started": False, "reason": st.get("reason", "disabled")}

    todo = _pending(self, force=force, slugs=slugs)
    if not todo:
        return {"started": False, "reason": "every photo is already downloaded"}

    self._prefetch = {"running": True, "total": len(todo), "done": 0, "ok": 0,
                      "failed": 0, "current": "", "started_at": time.time(),
                      "last_error": "", "cancel": False}
    # spawn_background, never a bare create_task: asyncio only weakly references
    # a running task, so a ~90s job is a real GC target.
    self.spawn_background(self._prefetch_loop(todo), label="dictionary picture prefetch")
    return {"started": True, "total": len(todo)}


async def _prefetch_loop(self, todo: list) -> None:
    width = self._image_width()
    idx = self.load_index()
    try:
        for n, slug in enumerate(todo, 1):
            if self._prefetch.get("cancel"):
                break
            self._prefetch["current"] = self.items.get(slug, {}).get("name", slug)
            prev = idx.get(slug) or {}
            try:
                rec = await self._fetch_one(slug, width)
            except Exception as e:
                rec = {"status": "failed", "error": f"{type(e).__name__}: {e}", "file": ""}
            rec["attempts"] = int(prev.get("attempts", 0)) + 1
            idx[slug] = rec
            self._index = idx
            self._prefetch["done"] = n
            if rec.get("status") == "ok":
                self._prefetch["ok"] += 1
            else:
                self._prefetch["failed"] += 1
                self._prefetch["last_error"] = f"{slug}: {rec.get('error', '')}"
            if n % INDEX_FLUSH_EVERY == 0:
                self.save_index(idx)
    finally:
        self.save_index(idx)
        self._index = idx
        self._prefetch["running"] = False
        self._prefetch["current"] = ""
        self.spawn_background(
            self.emit("dictionary:picture_images_ready", self.coverage()),
            label="dictionary picture_images_ready",
        )


# ─── Routes ──────────────────────────────────────────────────────────


@web_route("GET", "/api/picture/image/{slug}")
async def api_picture_image(self, request):
    slug = request.path_params.get("slug", "")
    rec = self._index.get(slug) or {}
    if rec.get("status") != "ok" or not rec.get("file"):
        from starlette.responses import JSONResponse
        return JSONResponse({"error": "no photo for that animal"}, status_code=404)
    ext = Path(rec["file"]).suffix.lower()
    media = {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
    return self.serve_data_file("images", rec["file"], media_type=media)


@web_route("POST", "/api/picture/images/prefetch")
async def api_picture_prefetch(self, request):
    """Starts the job and returns immediately. Awaiting it here would be the
    long-handler wedge — the request 500s at ~30s while the job runs on unseen."""
    body = await request.json() if await request.body() else {}
    slugs = body.get("slugs")
    return await self.start_prefetch(force=bool(body.get("force")),
                                     slugs=slugs if isinstance(slugs, list) else None)


@web_route("GET", "/api/picture/images/status")
async def api_picture_prefetch_status(self, request):
    p = dict(self._prefetch)
    started = p.get("started_at") or 0
    elapsed = round(time.time() - started, 1) if started else 0
    done, total = p.get("done", 0), p.get("total", 0)
    p["elapsed_s"] = elapsed
    p["eta_s"] = round((total - done) * elapsed / done) if done and total > done else 0
    # Nested, NOT merged: coverage carries its own `total` and `failed`, and
    # flattening them here overwrote the job's counters — the progress bar read
    # "done / 130 in the catalogue" instead of "done / 122 in this run", and the
    # failure count showed every historic failure rather than this run's.
    p["coverage"] = self.coverage()
    return p


@web_route("POST", "/api/picture/images/cancel")
async def api_picture_prefetch_cancel(self, request):
    if not self._prefetch.get("running"):
        return {"ok": True, "running": False}
    self._prefetch["cancel"] = True
    return {"ok": True, "cancelling": True}


@web_route("POST", "/api/picture/images/refetch/{slug}")
async def api_picture_refetch(self, request):
    """Single item, awaited — it is one round trip, and the user is watching."""
    slug = request.path_params.get("slug", "")
    if slug not in self.items:
        return {"error": f"unknown animal '{slug}'"}
    st = self._status()
    if not st.get("enabled", True):
        return {"error": st.get("reason", "photo lookup is disabled here")}
    idx = self.load_index()
    old = (idx.get(slug) or {}).get("file", "")
    rec = await self._fetch_one(slug, self._image_width())
    rec["attempts"] = 0
    idx[slug] = rec
    self.save_index(idx)
    self._index = idx
    if old and old != rec.get("file"):
        (self.data_subdir("images") / old).unlink(missing_ok=True)
    if rec.get("status") != "ok":
        return {"error": rec.get("error", "could not fetch a photo")}
    return {"ok": True, "slug": slug, "image": self.image_url(slug),
            "credit": self.image_credit(slug)}
