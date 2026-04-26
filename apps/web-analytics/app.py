"""Web Analytics — privacy-first pageview collector for published sites.

Accepts beacon POSTs from static sites built by the publish app, aggregates
into daily buckets via the TimeSeriesCounter SDK primitive, exposes a
dashboard. No cookies. No fingerprinting. Session IDs are daily-rolling
hashes (ip + ua + utc_date + secret salt) — they cannot be linked across
days. Raw IPs are never persisted.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
from datetime import datetime, timedelta, timezone

from emptyos.sdk import BaseApp, TimeSeriesCounter, cli_command, days_ago_utc, scheduled, today_utc, web_route


_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}


def _country_from_accept_language(header: str) -> str:
    """Best-effort country guess from Accept-Language (e.g. 'en-US,en;q=0.9' → 'US').

    Phase 2 (Cloudflare Worker) will provide CF-IPCountry which is more accurate.
    """
    if not header:
        return ""
    first = header.split(",")[0].strip()
    if "-" in first:
        tag = first.split("-", 1)[1].split(";")[0].strip().upper()
        if len(tag) == 2 and tag.isalpha():
            return tag
    return ""


def _is_local_ip(ip: str) -> bool:
    return ip in ("127.0.0.1", "::1", "localhost") or ip.startswith("192.168.") or ip.startswith("10.")


def _ip_matches_any(ip: str, patterns: list[str]) -> bool:
    """True if `ip` matches any exact address or CIDR range in `patterns`."""
    if not ip or not patterns:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for raw in patterns:
        raw = raw.strip()
        if not raw:
            continue
        try:
            if "/" in raw:
                if addr in ipaddress.ip_network(raw, strict=False):
                    return True
            elif str(addr) == str(ipaddress.ip_address(raw)):
                return True
        except ValueError:
            continue
    return False


def _client_ip(request) -> str:
    """Best-effort client IP, honoring Cloudflare + proxy headers."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


class WebAnalyticsApp(BaseApp):

    async def setup(self):
        await super().setup()
        self._init_schema()
        self._hits = TimeSeriesCounter(
            self.db, "hits",
            dims=["site", "path", "referrer", "country"],
            granularity="day",
        )
        self._sessions = TimeSeriesCounter(
            self.db, "sessions",
            dims=["site", "session"],
            granularity="day",
        )

    def _init_schema(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS recent_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                site TEXT NOT NULL,
                path TEXT NOT NULL,
                referrer TEXT NOT NULL DEFAULT '',
                country TEXT NOT NULL DEFAULT '',
                session TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        # First-run salt for privacy hashing
        cur = self.db.execute("SELECT value FROM meta WHERE key = 'salt'").fetchone()
        if cur is None:
            self.db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                ("salt", secrets.token_hex(16)),
            )
            self.db.commit()

    def _get_salt(self) -> str:
        try:
            row = self.db.execute("SELECT value FROM meta WHERE key = 'salt'").fetchone()
            return row[0] if row else ""
        except Exception:
            return ""

    def _session_id(self, ip: str, ua: str) -> str:
        date = today_utc()
        raw = f"{ip}|{ua}|{date}|{self._get_salt()}".encode("utf-8")
        return hashlib.blake2b(raw, digest_size=8).hexdigest()

    def _excluded_ips(self) -> list[str]:
        raw = self.setting("web-analytics.excluded_ips", "")
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(x) for x in raw]
        return [p.strip() for p in str(raw).replace("\n", ",").split(",") if p.strip()]


    # --- Collector ------------------------------------------------------

    @web_route("POST", "/api/collect")
    async def api_collect(self, request):
        if not self.setting("web-analytics.enabled", True):
            return {"status": "disabled"}

        # sendBeacon sends Blob as text/plain — parse raw body as JSON.
        try:
            raw = await request.body()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            return {"status": "error", "reason": "invalid_json"}

        site = (payload.get("site") or "").strip()[:64]
        path = (payload.get("path") or "/").strip()[:512]
        referrer = (payload.get("referrer") or "").strip()[:512]
        if not site:
            return {"status": "error", "reason": "missing_site"}

        client = _client_ip(request)
        if self.setting("web-analytics.drop_local", True) and _is_local_ip(client):
            return {"status": "dropped", "reason": "local"}
        excluded = self._excluded_ips()
        if _ip_matches_any(client, excluded):
            return {"status": "dropped", "reason": "excluded"}

        ua = request.headers.get("user-agent", "")
        country = (
            request.headers.get("cf-ipcountry", "")
            or _country_from_accept_language(request.headers.get("accept-language", ""))
        )[:2].upper()

        # Normalise referrer to hostname only (path/query dropped for privacy + aggregation)
        if referrer:
            try:
                from urllib.parse import urlparse
                host = urlparse(referrer).hostname or ""
                referrer = host
            except Exception:
                referrer = ""

        session = self._session_id(client, ua)

        self._hits.bump({
            "site": site, "path": path, "referrer": referrer, "country": country,
        })
        self._sessions.bump({"site": site, "session": session})

        # Ring-buffer the last ~1000 hits for a live tail.
        self.db.execute(
            "INSERT INTO recent_hits (ts, site, path, referrer, country, session) VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), site, path, referrer, country, session),
        )
        # Trim ring buffer
        self.db.execute(
            "DELETE FROM recent_hits WHERE id <= (SELECT MAX(id) - 1000 FROM recent_hits)"
        )
        self.db.commit()

        await self.emit("web-analytics:hit", {"site": site, "path": path})
        return {"status": "ok"}

    # --- Queries --------------------------------------------------------

    def _stats(self, site: str | None, range_key: str) -> dict:
        days = _RANGE_DAYS.get(range_key, 30)
        start = days_ago_utc(days - 1)
        end = today_utc()
        where = {"site": site} if site else None

        total = self._hits.total(start=start, end=end, where=where)
        daily_raw = {r["key"]: r["count"] for r in self._hits.range(start=start, end=end, where=where, group_by="bucket")}
        series = [{"bucket": days_ago_utc(i), "count": daily_raw.get(days_ago_utc(i), 0)} for i in range(days - 1, -1, -1)]

        unique_sessions_rows = self._sessions.range(start=start, end=end, where=({"site": site} if site else None))
        unique = len({(r["bucket"], r["session"]) for r in unique_sessions_rows})

        top_paths = self._hits.top("path", start=start, end=end, where=where, limit=10)
        top_refs = [r for r in self._hits.top("referrer", start=start, end=end, where=where, limit=10) if r["key"]]
        top_countries = [r for r in self._hits.top("country", start=start, end=end, where=where, limit=10) if r["key"]]

        return {
            "site": site,
            "range": range_key,
            "total": total,
            "unique_sessions": unique,
            "series": series,
            "top_paths": top_paths,
            "top_referrers": top_refs,
            "top_countries": top_countries,
        }

    def _site_ids(self) -> list[str]:
        rows = self.db.execute("SELECT DISTINCT site FROM recent_hits ORDER BY site").fetchall()
        ids = [r[0] for r in rows]
        # Fallback: scan the counter table if ring buffer is empty (e.g. after trim)
        if not ids:
            rows = self.db.execute("SELECT DISTINCT site FROM hits ORDER BY site").fetchall()
            ids = [r[0] for r in rows]
        return ids

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        site = request.query_params.get("site") or None
        range_key = request.query_params.get("range", "30d")
        return self._stats(site, range_key)

    @web_route("GET", "/api/sites")
    async def api_sites(self, request):
        return {"sites": self._site_ids()}

    @web_route("GET", "/api/live")
    async def api_live(self, request):
        site = request.query_params.get("site")
        limit = min(int(request.query_params.get("limit", "50")), 200)
        q = "SELECT ts, site, path, referrer, country FROM recent_hits"
        args: list = []
        if site:
            q += " WHERE site = ?"
            args.append(site)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = self.db.execute(q, args).fetchall()
        return {
            "hits": [
                {"ts": r[0], "site": r[1], "path": r[2], "referrer": r[3], "country": r[4]}
                for r in rows
            ]
        }

    # Called by publish's Analytics tab
    async def stats(self, site: str | None = None, range: str = "30d") -> dict:
        return self._stats(site, range)

    # --- Exclusion management ------------------------------------------

    @web_route("GET", "/api/whoami")
    async def api_whoami(self, request):
        ip = _client_ip(request)
        return {
            "ip": ip,
            "is_local": _is_local_ip(ip),
            "excluded_now": _ip_matches_any(ip, self._excluded_ips()),
            "excluded_list": self._excluded_ips(),
        }

    @web_route("POST", "/api/exclude-me")
    async def api_exclude_me(self, request):
        svc = self.kernel.services.get_optional("settings")
        if svc is None:
            return {"error": "settings service unavailable"}
        ip = _client_ip(request)
        if not ip:
            return {"error": "could not determine client IP"}
        current = self._excluded_ips()
        if _ip_matches_any(ip, current):
            return {"status": "already_excluded", "ip": ip, "excluded_list": current}
        current.append(ip)
        svc.set("web-analytics.excluded_ips", ", ".join(current))
        return {"status": "added", "ip": ip, "excluded_list": current}

    # --- Beacon script template -----------------------------------------

    _BEACON_TEMPLATE = (
        "(function(){try{if(navigator.doNotTrack==='1')return;"
        "var u=__COLLECTOR__,s=__SITE__;"
        "var b={site:s,path:location.pathname,referrer:document.referrer||''};"
        "var blob=new Blob([JSON.stringify(b)],{type:'text/plain'});"
        "if(navigator.sendBeacon){navigator.sendBeacon(u,blob);}"
        "else{fetch(u,{method:'POST',body:JSON.stringify(b),keepalive:true,"
        "headers:{'Content-Type':'text/plain'}}).catch(function(){});}"
        "}catch(e){}})();"
    )

    def render_beacon(self, site: str, collector: str | None = None) -> str:
        r"""Return a self-contained JS snippet for a given site + collector URL.

        Safe to inline into HTML: JSON output has </ replaced with <\/ so an
        attacker-supplied site name like "</script>..." can't break out of the
        enclosing <script> tag in the built site.
        """
        url = collector or self.setting(
            "web-analytics.collector_url",
            "http://localhost:9000/web-analytics/api/collect",
        )
        def _js_str(s: str) -> str:
            return json.dumps(s).replace("</", "<\\/")
        return (
            self._BEACON_TEMPLATE
            .replace("__COLLECTOR__", _js_str(url))
            .replace("__SITE__", _js_str(site or ""))
        )

    @web_route("GET", "/api/beacon.js")
    async def api_beacon(self, request):
        from starlette.responses import Response
        site = request.query_params.get("site", "")
        collector = request.query_params.get("collector")
        return Response(
            self.render_beacon(site, collector),
            media_type="application/javascript",
        )

    # --- CLI ------------------------------------------------------------

    @cli_command("web-analytics", help="Pageview analytics for published sites")
    async def cmd(self, action: str = "summary", site: str = "", range: str = "30d"):
        if action == "sites":
            for s in self._site_ids():
                print(f"  {s}")
            return
        data = self._stats(site or None, range)
        label = f"site={data['site'] or 'ALL'}  range={data['range']}"
        print(f"\n  {label}")
        print(f"  {data['total']} views / {data['unique_sessions']} unique sessions")
        print("\n  Top paths:")
        for r in data["top_paths"][:5]:
            print(f"    {r['count']:>4}  {r['key']}")
        if data["top_referrers"]:
            print("\n  Top referrers:")
            for r in data["top_referrers"][:5]:
                print(f"    {r['count']:>4}  {r['key']}")
        print()

    # --- Retention ------------------------------------------------------

    @scheduled("17 3 * * *", id="web-analytics-trim")  # daily 03:17 UTC
    async def nightly_trim(self):
        days = int(self.setting("web-analytics.retention_days", 365) or 365)
        before = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        removed = self._hits.trim(before) + self._sessions.trim(before)
        self.db.execute("DELETE FROM recent_hits WHERE ts < ?", (before,))
        self.db.commit()
        if removed:
            self.log(f"trimmed {removed} analytics rows older than {before}")
