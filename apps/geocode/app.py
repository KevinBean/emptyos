"""Geocode — address ↔ lat/lon via OpenStreetMap Nominatim.

Free public service, no API key. Respects Nominatim usage policy:
  - Descriptive User-Agent
  - ≤1 request/second (we throttle to 1.1s)
  - In-memory cache so repeated lookups don't re-hit the service

Apps call via `self.call_app("geocode", "lookup", address=...)` or HTTP
`GET /geocode/api/lookup?q=...`. Frontend: `EOS.geocode(address)` in eos.js.
"""

from __future__ import annotations

from emptyos.sdk import ExternalServiceBase, web_route

DEFAULT_BASE = "https://nominatim.openstreetmap.org"


class GeocodeApp(ExternalServiceBase):
    DEMO_BASE = DEFAULT_BASE
    SERVICE_LABEL = "Geocoding via the OSM Nominatim demo"
    MIN_INTERVAL_S = 1.1  # Nominatim policy: ≤1 req/s

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._cache: dict[str, list[dict]] = {}
        self._reverse_cache: dict[tuple[float, float], dict] = {}

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
                "q": address, "format": "json",
                "addressdetails": "1", "limit": str(limit),
            }
            async with aiohttp.ClientSession(headers={"User-Agent": self._user_agent()}) as session:
                async with session.get(f"{self._base_url()}/search", params=params, timeout=15) as r:
                    if r.status != 200:
                        return []
                    raw = await r.json()
        except Exception:
            return []

        results = [self._normalize(it) for it in (raw or [])]
        self._cache[key] = results
        return results

    async def reverse(self, lat: float, lon: float) -> dict:
        """Reverse geocode: (lat, lon) → nearest labelled address."""
        if not self._status()["enabled"]:
            return {}
        try:
            lat = float(lat); lon = float(lon)
        except (TypeError, ValueError):
            return {}
        key = (round(lat, 6), round(lon, 6))
        if key in self._reverse_cache:
            return self._reverse_cache[key]

        await self._throttle()
        try:
            import aiohttp
            params = {"lat": str(lat), "lon": str(lon),
                      "format": "json", "addressdetails": "1"}
            async with aiohttp.ClientSession(headers={"User-Agent": self._user_agent()}) as session:
                async with session.get(f"{self._base_url()}/reverse", params=params, timeout=15) as r:
                    if r.status != 200:
                        return {}
                    raw = await r.json()
        except Exception:
            return {}

        result = self._normalize(raw or {})
        self._reverse_cache[key] = result
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

    @web_route("GET", "/api/cache-stats")
    async def api_cache_stats(self, request):
        return {
            "forward": len(self._cache),
            "reverse": len(self._reverse_cache),
        }

    @web_route("GET", "/api/status")
    async def api_status(self, request):
        return self._status()
