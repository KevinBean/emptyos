"""Routing — multi-stop routes via OSRM.

Free public service (`router.project-osrm.org`). For self-hosted OSRM or
Valhalla, override `base_url` via `[apps.routing]` in `emptyos.toml`.

Apps call via `self.call_app("routing", "route", points=..., profile=...)` or
HTTP `POST /routing/api/route`. Frontend: `EOS.getRoute(points, profile)`.
"""

from __future__ import annotations

import json

from emptyos.sdk import ExternalServiceBase, web_route
from emptyos.sdk.utils import now_iso, safe_path_segment, slugify

DEFAULT_BASE = "https://router.project-osrm.org"
MAX_WAYPOINTS = 25  # OSRM public demo caps at ~100; vault trips stay small

# routing-no-cost-estimate: a simple distance-derived fuel/running-cost
# estimate. No new data source — purely `distance_m x configurable $/km`,
# per the gap analysis's suggested change. Default is a rough AU-petrol
# rule of thumb (~$1.80/L at ~8L/100km); tune via Settings or
# `[apps.routing] cost_per_km` in emptyos.toml.
DEFAULT_COST_PER_KM = 0.15

# Saved trips (routing-no-saved-trips): a trip persists the STOPS the user
# entered, not the computed route — reopening a trip re-runs `route()` fresh,
# so it can never go stale against a changed OSRM response and needs no new
# compute path.
#
# Stops are JSON-encoded into `stops_json`, NOT a nested `geo:` block — the
# geo.md convention's `geo:` block does not actually round-trip through the
# vault frontmatter parser today (found while building this: the hand-rolled
# parser has no nested-mapping support at all, so `fm.get("geo")` comes back
# as an opaque string, not a dict). See the KNOWN LIMITATION callout in
# `.claude/rules/geo.md`. JSON-string encoding is the working pattern
# an engineering app's `attribute_schema` already uses for the same reason.
TRIP_TAG = "routing-trip"
TRIP_DIR = "30_Resources/EmptyOS/routing/trips"


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

    def _with_cost_estimate(self, result: dict) -> dict:
        """Attach a fuel/running-cost estimate to an already-computed route.

        Read fresh on every call rather than baked into the cached route
        entry, so a settings change takes effect immediately even for a
        route that's already in `self._cache`.
        """
        out = dict(result)
        rate = self.setting_or_config("routing.cost_per_km", DEFAULT_COST_PER_KM)
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            rate = DEFAULT_COST_PER_KM
        out["cost_per_km"] = rate
        out["cost_estimate"] = (
            round((out.get("distance_m", 0) / 1000.0) * rate, 2) if rate > 0 else None
        )
        return out

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
            return self._with_cost_estimate(self._cache[cache_key])

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
        return self._with_cost_estimate(result)

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

    # ── Saved trips ────────────────────────────────────────────────
    def _trip_path(self, trip_id: str) -> str:
        return f"{TRIP_DIR}/{safe_path_segment(trip_id)}.md"

    async def save_trip(self, name: str, points, profile: str = "driving") -> dict:
        """Persist a named, revisitable trip. Flagged in gap analysis
        (routing-no-saved-trips): a computed route was never persisted —
        every visit started from a blank stop list.

        Saving again under the same name overwrites that trip (same slug,
        same path) — a simple upsert, no separate rename/versioning.
        """
        name = (name or "").strip()
        if not name:
            return {"error": "name is required"}
        pts = self._coerce_points(points)
        if len(pts) < 2:
            return {"error": "need at least 2 points"}
        profile = (profile or "driving").lower()
        if profile not in ("driving", "walking", "cycling"):
            profile = "driving"
        trip_id = safe_path_segment(slugify(name)) or "trip"
        path = self._trip_path(trip_id)
        ts = now_iso()
        fm = {
            "tags": [TRIP_TAG],
            "trip_id": trip_id,
            "name": name,
            "profile": profile,
            "stop_count": len(pts),
            "stops_json": json.dumps([[lat, lng] for lat, lng in pts]),
            "created": ts,
            "updated": ts,
        }
        self.vault_create_note(path, fm, f"# {name}\n\nTrip with {len(pts)} stops.\n")
        await self.emit("routing:trip_saved", {"trip_id": trip_id, "name": name, "stops": len(pts)})
        return {"ok": True, "trip_id": trip_id, "path": path}

    async def list_trips(self) -> list[dict]:
        out = []
        for note in self.vault_query(tags=[TRIP_TAG]):
            p = note.get("properties", {}) or {}
            out.append({
                "trip_id": str(p.get("trip_id", "") or ""),
                "name": str(p.get("name", "") or note.get("name", "")),
                "profile": str(p.get("profile", "") or "driving"),
                "stop_count": int(str(p.get("stop_count", "") or 0) or 0),
                "updated": str(p.get("updated", "") or ""),
            })
        out.sort(key=lambda t: t["updated"], reverse=True)
        return out

    async def get_trip(self, trip_id: str) -> dict:
        trip_id = (trip_id or "").strip()
        rows = self.vault_query(tags=[TRIP_TAG], trip_id=trip_id)
        if not rows:
            return {"error": "trip not found"}
        p = rows[0].get("properties", {}) or {}
        try:
            coords = json.loads(str(p.get("stops_json", "") or "[]"))
        except (TypeError, ValueError):
            coords = []
        points = [
            {"lat": c[0], "lng": c[1]}
            for c in coords
            if isinstance(c, (list, tuple)) and len(c) >= 2
        ]
        return {
            "trip_id": str(p.get("trip_id", "") or trip_id),
            "name": str(p.get("name", "") or ""),
            "profile": str(p.get("profile", "") or "driving"),
            "points": points,
        }

    async def delete_trip(self, trip_id: str) -> dict:
        trip_id = (trip_id or "").strip()
        rows = self.vault_query(tags=[TRIP_TAG], trip_id=trip_id)
        if not rows:
            return {"ok": True, "missing": True}
        (self.vault_root / rows[0]["path"]).unlink(missing_ok=True)
        await self.emit("routing:trip_deleted", {"trip_id": trip_id})
        return {"ok": True, "trip_id": trip_id}

    @web_route("POST", "/api/trips")
    async def api_save_trip(self, request):
        body = await request.json()
        return await self.save_trip(body.get("name", ""), body.get("points"), body.get("profile", "driving"))

    @web_route("GET", "/api/trips")
    async def api_list_trips(self, request):
        return {"trips": await self.list_trips()}

    @web_route("GET", "/api/trips/{trip_id}")
    async def api_get_trip(self, request):
        return await self.get_trip(request.path_params.get("trip_id", ""))

    @web_route("DELETE", "/api/trips/{trip_id}")
    async def api_delete_trip(self, request):
        return await self.delete_trip(request.path_params.get("trip_id", ""))
