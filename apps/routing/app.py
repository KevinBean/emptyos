"""Routing — multi-stop routes via OSRM.

Free public service (`router.project-osrm.org`). For self-hosted OSRM or
Valhalla, override `base_url` via `[apps.routing]` in `emptyos.toml`.

Apps call via `self.call_app("routing", "route", points=..., profile=...)` or
HTTP `POST /routing/api/route`. Frontend: `EOS.getRoute(points, profile)`.
"""

from __future__ import annotations

from emptyos.sdk import ExternalServiceBase, web_route

DEFAULT_BASE = "https://router.project-osrm.org"
MAX_WAYPOINTS = 25  # OSRM public demo caps at ~100; vault trips stay small


class RoutingApp(ExternalServiceBase):
    DEMO_BASE = DEFAULT_BASE
    SERVICE_LABEL = "Routing via the OSRM demo"
    MIN_INTERVAL_S = 1.0  # OSRM demo is a shared resource — throttle politely

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._cache: dict[str, dict] = {}

    def _coerce_points(self, raw) -> list[tuple[float, float]]:
        """Accept [[lat,lng], ...] or [{lat, lng|lon}, ...]; return [(lat,lng), ...]."""
        points: list[tuple[float, float]] = []
        for p in raw or []:
            if isinstance(p, dict):
                lat = p.get("lat")
                lng = p.get("lng") if p.get("lng") is not None else p.get("lon")
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                lat, lng = p[0], p[1]
            else:
                continue
            try:
                points.append((float(lat), float(lng)))
            except (TypeError, ValueError):
                continue
        return points

    async def route(self, points, profile: str = "driving") -> dict:
        """Compute a multi-stop route.

        Returns `{geometry, distance_m, duration_s, legs, waypoints}` where
        `geometry` is `[[lat,lng], ...]` suitable for `EOS_MAP.setPolylines`.
        """
        status = self._status()
        if not status["enabled"]:
            return {"error": status["reason"], "disabled": True}
        pts = self._coerce_points(points)
        if len(pts) < 2:
            return {"error": "need at least 2 points"}
        if len(pts) > MAX_WAYPOINTS:
            return {"error": f"too many waypoints (max {MAX_WAYPOINTS})"}

        profile = (profile or "driving").lower()
        if profile not in ("driving", "walking", "cycling"):
            profile = "driving"

        # OSRM wants lng,lat; we use lat,lng everywhere else to match Leaflet.
        coord_str = ";".join(f"{lng},{lat}" for lat, lng in pts)
        cache_key = f"{profile}|{coord_str}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        await self._throttle()
        try:
            import aiohttp

            url = f"{self._base_url()}/route/v1/{profile}/{coord_str}"
            params = {"overview": "full", "geometries": "geojson", "steps": "false"}
            async with aiohttp.ClientSession(headers={"User-Agent": self._user_agent()}) as session:
                async with session.get(url, params=params, timeout=30) as r:
                    if r.status != 200:
                        return {"error": f"routing service returned {r.status}"}
                    raw = await r.json()
        except Exception as e:
            return {"error": f"routing failed: {e.__class__.__name__}"}

        if raw.get("code") != "Ok" or not raw.get("routes"):
            return {"error": raw.get("message") or "no route found"}

        best = raw["routes"][0]
        coords = best.get("geometry", {}).get("coordinates", []) or []
        result = {
            "geometry": [[lat, lng] for lng, lat in coords],  # back to lat,lng
            "distance_m": best.get("distance", 0),
            "duration_s": best.get("duration", 0),
            "legs": [
                {"distance_m": leg.get("distance", 0), "duration_s": leg.get("duration", 0)}
                for leg in best.get("legs", [])
            ],
            "waypoints": [
                {
                    "lat": wp.get("location", [0, 0])[1],
                    "lng": wp.get("location", [0, 0])[0],
                    "name": wp.get("name", ""),
                }
                for wp in raw.get("waypoints", [])
            ],
            "profile": profile,
        }
        self._cache[cache_key] = result
        await self.emit(
            "routing:planned",
            {
                "profile": profile,
                "stops": len(pts),
                "distance_m": result["distance_m"],
                "duration_s": result["duration_s"],
            },
        )
        return result

    # ── HTTP ─────────────────────────────────────────────────────

    @web_route("POST", "/api/route")
    async def api_route(self, request):
        try:
            body = await request.json()
        except Exception:
            return {"error": "invalid json"}
        return await self.route(body.get("points"), body.get("profile", "driving"))

    @web_route("GET", "/api/cache-stats")
    async def api_cache_stats(self, request):
        return {"routes": len(self._cache)}

    @web_route("GET", "/api/status")
    async def api_status(self, request):
        return self._status()
