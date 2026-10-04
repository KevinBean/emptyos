"""Geocode — address ↔ lat/lon via OpenStreetMap Nominatim.

Free public service, no API key. Respects Nominatim usage policy:
  - Descriptive User-Agent
  - ≤1 request/second (we throttle to 1.1s)
  - Cache so repeated lookups don't re-hit the service — persisted to
    data/apps/geocode/cache.json (geocode-cache-not-persisted) so a daemon
    restart doesn't re-hit the rate-limited public service for addresses
    already resolved yesterday. Machine telemetry, not user content, so
    data/ is the right home per CLAUDE.md's data/-vs-vault split.

Apps call via `self.call_app("geocode", "lookup", address=...)` or HTTP
`GET /geocode/api/lookup?q=...`. Frontend: `EOS.geocode(address)` in eos.js.
"""

from __future__ import annotations

import json

from emptyos.sdk import ExternalServiceBase, web_route

DEFAULT_BASE = "https://nominatim.openstreetmap.org"
# A batch call already pays lookup()'s own ~1.1s/request throttle
# sequentially — this just bounds how long one HTTP request can run.
MAX_BATCH = 50
# A persisted cache no longer resets on restart, so unlike the old in-memory
# dict it can genuinely grow forever over months of daemon uptime. Cap each
# side and evict oldest-first (dict preserves insertion order) once full.
MAX_CACHE_ENTRIES = 5000


class GeocodeApp(ExternalServiceBase):
    DEMO_BASE = DEFAULT_BASE
    SERVICE_LABEL = "Geocoding via the OSM Nominatim demo"
    MIN_INTERVAL_S = 1.1  # Nominatim policy: ≤1 req/s

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._cache: dict[str, list[dict]] = {}
        self._reverse_cache: dict[tuple[float, float], dict] = {}
        self._load_cache()

    def _cache_path(self):
        return self.data_dir / "cache.json"

    def _load_cache(self) -> None:
        """Fail-soft: a missing/corrupt cache file just starts cold, same
        as the pre-persistence behaviour — never a reason to fail boot."""
        try:
            raw = json.loads(self._cache_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        forward = raw.get("forward")
        if isinstance(forward, dict):
            self._cache = forward
        reverse = raw.get("reverse")
        if isinstance(reverse, list):
            for entry in reverse:
                if not (isinstance(entry, list) and len(entry) == 3):
                    continue
                lat, lon, value = entry
                try:
                    self._reverse_cache[(float(lat), float(lon))] = value
                except (TypeError, ValueError):
                    continue

    def _save_cache(self) -> None:
        # Reverse-cache keys are (lat, lon) tuples — not valid JSON object
        # keys — so store them as [lat, lon, value] triples instead.
        payload = {
            "forward": self._cache,
            "reverse": [[k[0], k[1], v] for k, v in self._reverse_cache.items()],
        }
        try:
            self._cache_path().write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass  # best-effort — a failed cache write must never break a lookup

    @staticmethod
    def _evict_oldest(cache: dict, limit: int) -> None:
        overflow = len(cache) - limit
        if overflow <= 0:
            return
        for key in list(cache.keys())[:overflow]:
            del cache[key]

    def _normalize(self, raw: dict) -> dict:
        return {
            "display_name": raw.get("display_name", ""),
            "lat": float(raw["lat"]) if raw.get("lat") is not None else None,
            "lon": float(raw["lon"]) if raw.get("lon") is not None else None,
            "type": raw.get("type", ""),
            "address": raw.get("address", {}) or {},
        }

    async def lookup(self, address: str, limit: int = 5) -> list[dict]:
        """Forward geocode: 'Bondi Beach NSW' → [{lat, lon, display_name, ...}]."""
        if not self._status()["enabled"]:
            return []
        address = (address or "").strip()
        if not address:
            return []
        limit = max(1, min(int(limit or 5), 10))
        key = f"{address.lower()}|{limit}"
        if key in self._cache:
            return self._cache[key]

        await self._throttle()
        try:
            import aiohttp

            params = {
                "q": address,
                "format": "json",
                "addressdetails": "1",
                "limit": str(limit),
            }
            async with aiohttp.ClientSession(headers={"User-Agent": self._user_agent()}) as session:
                async with session.get(
                    f"{self._base_url()}/search", params=params, timeout=15
                ) as r:
                    if r.status != 200:
                        return []
                    raw = await r.json()
        except Exception:
            return []

        results = [self._normalize(it) for it in (raw or [])]
        self._cache[key] = results
        self._evict_oldest(self._cache, MAX_CACHE_ENTRIES)
        self._save_cache()
        return results

    async def reverse(self, lat: float, lon: float) -> dict:
        """Reverse geocode: (lat, lon) → nearest labelled address."""
        if not self._status()["enabled"]:
            return {}
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return {}
        key = (round(lat, 6), round(lon, 6))
        if key in self._reverse_cache:
            return self._reverse_cache[key]

        await self._throttle()
        try:
            import aiohttp

            params = {"lat": str(lat), "lon": str(lon), "format": "json", "addressdetails": "1"}
            async with aiohttp.ClientSession(headers={"User-Agent": self._user_agent()}) as session:
                async with session.get(
                    f"{self._base_url()}/reverse", params=params, timeout=15
                ) as r:
                    if r.status != 200:
                        return {}
                    raw = await r.json()
        except Exception:
            return {}

        result = self._normalize(raw or {})
        self._reverse_cache[key] = result
        self._evict_oldest(self._reverse_cache, MAX_CACHE_ENTRIES)
        self._save_cache()
        return result

    # ── HTTP ─────────────────────────────────────────────────────

    @web_route("GET", "/api/lookup")
    async def api_lookup(self, request):
        q = request.query_params.get("q", "")
        limit = request.query_params.get("limit", "5")
        return await self.lookup(q, limit)

    @web_route("GET", "/api/reverse")
    async def api_reverse(self, request):
        lat = request.query_params.get("lat", "")
        lon = request.query_params.get("lon", "")
        if not lat or not lon:
            return {"error": "lat and lon required"}
        return await self.reverse(lat, lon)

    @web_route("POST", "/api/batch-lookup")
    async def api_batch_lookup(self, request):
        """Batch forward geocode (geocode-no-batch-lookup). Body:
        {addresses: [str, ...], limit?}. Sequential by design — each
        lookup() call already throttles to Nominatim's <=1 req/s usage
        policy (self._throttle()), so looping naturally stays compliant;
        a cached address skips the wait entirely."""
        body = await request.json()
        addresses = body.get("addresses")
        if not isinstance(addresses, list) or not addresses:
            return {"error": "addresses (list) required"}
        if len(addresses) > MAX_BATCH:
            return {"error": f"too many addresses (max {MAX_BATCH})"}
        try:
            limit = int(body.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5

        results = []
        for raw in addresses:
            address = str(raw or "").strip()
            if not address:
                results.append({"address": raw, "results": [], "error": "empty address"})
                continue
            matches = await self.lookup(address, limit=limit)
            results.append({"address": address, "results": matches})
        return {
            "results": results,
            "summary": {
                "total": len(results),
                "matched": sum(1 for r in results if r["results"]),
                "unmatched": sum(1 for r in results if not r["results"]),
            },
        }

    @web_route("POST", "/api/batch-reverse")
    async def api_batch_reverse(self, request):
        """Batch reverse geocode. Body: {points: [{lat, lon}, ...]}."""
        body = await request.json()
        points = body.get("points")
        if not isinstance(points, list) or not points:
            return {"error": "points (list) required"}
        if len(points) > MAX_BATCH:
            return {"error": f"too many points (max {MAX_BATCH})"}

        results = []
        for raw in points:
            if not isinstance(raw, dict):
                results.append({"lat": None, "lon": None, "result": {}, "error": "invalid point"})
                continue
            lat, lon = raw.get("lat"), raw.get("lon")
            result = await self.reverse(lat, lon)
            results.append({"lat": lat, "lon": lon, "result": result})
        return {"results": results}

    @web_route("GET", "/api/cache-stats")
    async def api_cache_stats(self, request):
        return {
            "forward": len(self._cache),
            "reverse": len(self._reverse_cache),
        }

    @web_route("GET", "/api/status")
    async def api_status(self, request):
        return self._status()
