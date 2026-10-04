"""daily-brief — user-managed feed sources (subscriptions).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the custom feed registry + the per-source enable/disable
overrides layered on top of the config/DEFAULT built-in sources, and the
add(validate)/remove/toggle routes. This is the "follow a feed from the UI"
affordance — before it, sources were emptyos.toml-only.

The spine's ``_sources()`` composes ``_builtin_sources()`` + ``_custom_sources()``
minus ``_disabled_ids()``; everything user-mutable lives here.

State: ``data/apps/daily-brief/sources.json`` =
``{"custom": [ {id,name,url,kind,category,added_at} ], "disabled": [ id, ... ]}``.
A source (built-in *or* custom) is enabled unless its id is in ``disabled``;
built-ins can be disabled but not removed, custom feeds can be both.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._builtin_sources (spine) + self._fetch_one
(feeds); shared._UA. Do not import from ``.app`` (it imports us → cycle).
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from emptyos.sdk import load_json, save_json, web_route
from emptyos.sdk.web_search import is_public_web_url

from .shared import _UA

if TYPE_CHECKING:
    from .app import DailyBriefApp  # noqa: F401 — for type hints only


# ─── Bind to DailyBriefApp class as ────────────────────────────────
#   _sources_file        = _sources_mod._sources_file
#   _sources_state       = _sources_mod._sources_state
#   _save_sources_state  = _sources_mod._save_sources_state
#   _custom_sources      = _sources_mod._custom_sources
#   _disabled_ids        = _sources_mod._disabled_ids
#   _all_sources_managed = _sources_mod._all_sources_managed
#   _validate_source     = _sources_mod._validate_source
#   api_sources_add      = _sources_mod.api_sources_add
#   api_sources_remove   = _sources_mod.api_sources_remove
#   api_sources_toggle   = _sources_mod.api_sources_toggle
#   api_sources_manage   = _sources_mod.api_sources_manage
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _source_id(url: str) -> str:
    """Stable id for a user feed — hash of the URL, prefixed so it can never
    collide with a built-in id (reneweconomy, hacker-news, …)."""
    return "custom-" + hashlib.md5((url or "").strip().encode("utf-8")).hexdigest()[:8]


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or url).replace("www.", "")
    except Exception:  # noqa: BLE001 — malformed URL → show it raw
        return url


# ── State ──────────────────────────────────────────────────────────
def _sources_file(self):
    return self.data_dir / "sources.json"


def _sources_state(self) -> dict:
    st = load_json(self._sources_file(), {}) or {}
    custom = st.get("custom")
    disabled = st.get("disabled")
    return {
        "custom": custom if isinstance(custom, list) else [],
        "disabled": disabled if isinstance(disabled, list) else [],
    }


def _save_sources_state(self, st: dict):
    save_json(self._sources_file(), {
        "custom": (st.get("custom") or [])[:200],
        "disabled": list(dict.fromkeys(st.get("disabled") or []))[:500],
    })


def _custom_sources(self) -> list[dict]:
    """User-added feeds, normalized to the same shape as _builtin_sources()."""
    out = []
    for s in self._sources_state()["custom"]:
        if not isinstance(s, dict) or not s.get("url"):
            continue
        out.append({
            "id": s.get("id") or _source_id(s["url"]),
            "name": s.get("name") or s.get("id") or s["url"],
            "url": s["url"],
            "kind": (s.get("kind") or "rss").lower(),
            "category": s.get("category") or "general",
        })
    return out


def _disabled_ids(self) -> set:
    return set(self._sources_state()["disabled"])


def _all_sources_managed(self) -> list[dict]:
    """Every source (built-in + custom) with origin + enabled flags, for the
    Sources management UI. Deduped by url (a custom dup of a built-in hides)."""
    disabled = self._disabled_ids()
    seen, out = set(), []
    for s in self._builtin_sources():
        seen.add(s["url"])
        out.append({**s, "origin": "built-in", "enabled": s["id"] not in disabled})
    for s in self._custom_sources():
        if s["url"] in seen:
            continue
        seen.add(s["url"])
        out.append({**s, "origin": "custom", "enabled": s["id"] not in disabled})
    return out


# ── Validate-on-add (SSRF-guarded test fetch) ──
async def _validate_source(self, url: str, kind: str) -> list[dict]:
    """Fetch + parse a candidate feed to confirm it's real. SSRF-guarded: the
    user-supplied URL **and every redirect hop** must resolve to a public host
    (``is_public_web_url``) — a feed URL pointing at loopback / link-local
    (169.254.169.254) / RFC1918 is refused, so a hosted daemon can't be aimed
    at internal targets. Redirects are followed manually (feeds legitimately
    301 http→https / feedburner→origin) with a per-hop re-check rather than the
    trusted ``_fetch_one`` path used for built-in sources. Raises on transport
    / parse failure or a blocked host; the caller decides if empty is fatal."""
    import aiohttp
    from yarl import URL

    async def _guard(u: str):
        # Blocking DNS resolution → off the event loop.
        if not await asyncio.to_thread(is_public_web_url, u):
            raise RuntimeError("feed URL must point at a public host")

    await _guard(url)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(
        timeout=timeout, headers={"User-Agent": _UA}
    ) as session:
        cur = url
        for _ in range(4):  # initial + up to 3 redirects, each re-guarded
            async with session.get(cur, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location", "")
                    if not loc:
                        return []
                    cur = str(URL(cur).join(URL(loc)))
                    await _guard(cur)
                    continue
                resp.raise_for_status()
                body = await self._read_body(resp)
                return self._parse_json(body) if kind == "json" else self._parse_feed(body)
        raise RuntimeError("too many redirects")


# ── Routes ─────────────────────────────────────────────────────────
@web_route("GET", "/api/sources/manage")
async def api_sources_manage(self, request):
    """Every source with origin + enabled — the Sources tab reads this."""
    return {"sources": self._all_sources_managed()}


@web_route("POST", "/api/sources")
async def api_sources_add(self, request):
    """Follow a new feed. Validates by fetching + parsing it first (auto-tries
    rss then json) so a typo or a non-feed URL is rejected with a clear error
    rather than silently contributing nothing on the next pull."""
    body = await request.json()
    url = str(body.get("url") or "").strip()
    if not url or not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "a valid http(s) feed URL is required"}
    # SSRF guard — refuse loopback / link-local / private / reserved targets
    # before we ever fetch. (_validate_source re-guards each redirect hop.)
    if not await asyncio.to_thread(is_public_web_url, url):
        return {"ok": False,
                "error": "feed URL must point at a public host — internal / "
                         "loopback addresses are blocked"}

    if any(b["url"] == url for b in self._builtin_sources()):
        return {"ok": False, "error": "that feed is already a built-in source"}
    st = self._sources_state()
    sid = _source_id(url)
    if any((c.get("id") == sid or c.get("url") == url) for c in st["custom"]):
        return {"ok": False, "error": "you're already following that feed", "dup": True}

    kind = str(body.get("kind", "") or "").strip().lower()
    if kind in ("rss", "json"):
        kinds = [kind]
    elif url.lower().rstrip("/").endswith(".json"):
        kinds = ["json", "rss"]
    else:
        kinds = ["rss", "json"]
    items, used_kind, err = [], kinds[0], ""
    for k in kinds:
        try:
            items = await self._validate_source(url, k)
        except Exception as e:  # noqa: BLE001 — bad URL / transport / parse
            err = str(e)[:200]
            items = []
        if items:
            used_kind = k
            break
    if not items:
        tail = f" ({err})" if err else ""
        return {"ok": False, "error": "no feed items found at that URL" + tail}

    name = str(body.get("name") or "").strip() or _domain(url)
    category = str(body.get("category") or "").strip() or "general"
    st["custom"].insert(0, {
        "id": sid, "name": name, "url": url, "kind": used_kind,
        "category": category, "added_at": datetime.now(UTC).isoformat(),
    })
    # a freshly-followed feed is enabled — clear any stale disable of this id
    st["disabled"] = [d for d in st["disabled"] if d != sid]
    self._save_sources_state(st)
    await self.emit("daily-brief:source_added", {"id": sid, "url": url, "name": name})
    self.log_activity({"event": "source_added", "url": url, "items": len(items)})
    return {"ok": True, "id": sid, "name": name, "kind": used_kind,
            "category": category, "item_count": len(items)}


@web_route("DELETE", "/api/sources/{id}")
async def api_sources_remove(self, request):
    """Unfollow a custom feed. Built-in sources can't be removed — only disabled."""
    sid = request.path_params.get("id")
    st = self._sources_state()
    kept = [c for c in st["custom"] if c.get("id") != sid]
    if len(kept) == len(st["custom"]):
        return {"ok": False,
                "error": "built-in sources can't be removed — disable it instead"}
    st["custom"] = kept
    st["disabled"] = [d for d in st["disabled"] if d != sid]
    self._save_sources_state(st)
    return {"ok": True}


@web_route("POST", "/api/sources/{id}/toggle")
async def api_sources_toggle(self, request):
    """Enable/disable any source (built-in or custom) for the next pull."""
    sid = request.path_params.get("id")
    if sid not in {s["id"] for s in self._all_sources_managed()}:
        return {"ok": False, "error": "unknown source"}
    st = self._sources_state()
    if sid in st["disabled"]:
        st["disabled"] = [d for d in st["disabled"] if d != sid]
        enabled = True
    else:
        st["disabled"].append(sid)
        enabled = False
    self._save_sources_state(st)
    return {"ok": True, "enabled": enabled}
