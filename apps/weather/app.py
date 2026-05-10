"""Weather — current conditions, forecast, history.

Fallback chain (first success wins):
  1. OpenWeatherMap   — rich data, requires api_key in [apps.weather].
  2. Open-Meteo       — keyless, requires location.latitude/longitude in global settings.
  3. wttr.in          — keyless, always-on coarse one-liner. Configure with
                        [apps.weather] base_url = "https://wttr.in/?format=3".

Configure via ``emptyos.toml``::

    [apps.weather]
    api_key = ""                           # OpenWeatherMap key (optional)
    city    = ""                           # city name if using OWM without lat/lng
    units   = "metric"                     # "metric" | "imperial"
    base_url = "https://wttr.in/?format=3" # wttr.in fallback endpoint
    cache_ttl_seconds = 600

Location for the keyless Open-Meteo path comes from global settings
(``location.latitude``, ``location.longitude``, ``location.timezone``) so other
apps can share it.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from datetime import UTC, date, datetime

import aiohttp

from emptyos.sdk import BaseApp, cli_command, web_route


class WeatherApp(BaseApp):
    _OPEN_METEO_CODES = {
        0: "Clear",
        1: "Mostly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Rime fog",
        51: "Light drizzle",
        53: "Drizzle",
        55: "Heavy drizzle",
        61: "Light rain",
        63: "Rain",
        65: "Heavy rain",
        71: "Light snow",
        73: "Snow",
        75: "Heavy snow",
        80: "Light showers",
        81: "Showers",
        82: "Heavy showers",
        95: "Thunderstorm",
    }

    async def setup(self):
        self._wttr_cache: str | None = None
        self._wttr_cache_at: float = 0.0
        self._open_meteo_cache: dict | None = None
        self._open_meteo_cache_at: float = 0.0

    # ── persisted cache (rich OWM payload) ─────────────────────

    def _cache_file(self):
        return self.data_dir / "weather_cache.json"

    def _load_cache(self) -> dict:
        path = self._cache_file()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_cache(self, data: dict):
        self._cache_file().write_text(
            json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
        )

    def _history_file(self):
        return self.data_dir / "weather_history.json"

    def _load_history(self) -> list[dict]:
        path = self._history_file()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save_history(self, data: list[dict]):
        self._history_file().write_text(
            json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
        )

    # ── config ─────────────────────────────────────────────────

    def _owm_config(self) -> dict:
        return {
            "api_key": self.app_config("api_key", "") or "",
            "city": self.app_config("city", "") or "",
            "units": self.app_config("units", "metric") or "metric",
            "lat": self.app_config("lat", "") or "",
            "lon": self.app_config("lon", "") or "",
        }

    def _wttr_config(self) -> tuple[str, int]:
        url = self.app_config("base_url", "https://wttr.in/?format=3") or ""
        ttl = int(self.app_config("cache_ttl_seconds", 600) or 600)
        return url, ttl

    def _global_location(self) -> tuple[str, str, str]:
        try:
            settings = self.require("settings")
        except Exception:
            return "", "", "UTC"
        lat = settings.get("location.latitude", "") or ""
        lng = settings.get("location.longitude", "") or ""
        tz = settings.get("location.timezone", "UTC") or "UTC"
        return str(lat), str(lng), str(tz)

    def _code_to_emoji(self, code: int) -> str:
        if code == 0:
            return "☀️"
        if code < 3:
            return "🌤️"
        if code < 50:
            return "☁️"
        if code < 70:
            return "🌧️"
        if code < 80:
            return "❄️"
        return "⛈️"

    # ── OWM fetch ──────────────────────────────────────────────

    async def _fetch_owm(self) -> dict | None:
        cfg = self._owm_config()
        api_key = cfg["api_key"]
        if not api_key:
            return None

        units = cfg["units"]
        params = {"appid": api_key, "units": units}

        if cfg["lat"] and cfg["lon"]:
            params["lat"] = cfg["lat"]
            params["lon"] = cfg["lon"]
        elif cfg["city"]:
            params["q"] = cfg["city"]
        else:
            # Fall back to global location settings
            lat, lng, _ = self._global_location()
            if lat and lng:
                params["lat"] = lat
                params["lon"] = lng
            else:
                return None

        try:
            async with aiohttp.ClientSession() as session:
                weather_url = "https://api.openweathermap.org/data/2.5/weather"
                forecast_url = "https://api.openweathermap.org/data/2.5/forecast"
                resp_w, resp_f = await asyncio.gather(
                    session.get(weather_url, params=params),
                    session.get(forecast_url, params=params),
                )
                async with resp_w, resp_f:
                    if resp_w.status != 200:
                        return None
                    current = await resp_w.json()
                    forecast_data = await resp_f.json() if resp_f.status == 200 else {}
        except Exception:
            return None

        unit_label = "C" if units == "metric" else "F"
        speed_label = "m/s" if units == "metric" else "mph"

        result = {
            "source": "openweathermap",
            "fetched_at": datetime.now(UTC).isoformat(),
            "city": current.get("name", cfg.get("city", "")),
            "country": current.get("sys", {}).get("country", ""),
            "condition": current.get("weather", [{}])[0].get("main", ""),
            "description": current.get("weather", [{}])[0].get("description", ""),
            "icon": current.get("weather", [{}])[0].get("icon", ""),
            "temp": current.get("main", {}).get("temp"),
            "feels_like": current.get("main", {}).get("feels_like"),
            "temp_min": current.get("main", {}).get("temp_min"),
            "temp_max": current.get("main", {}).get("temp_max"),
            "humidity": current.get("main", {}).get("humidity"),
            "wind_speed": current.get("wind", {}).get("speed"),
            "unit": unit_label,
            "speed_unit": speed_label,
            "sunrise": current.get("sys", {}).get("sunrise"),
            "sunset": current.get("sys", {}).get("sunset"),
        }

        forecast_list = forecast_data.get("list", [])[:8]
        result["forecast"] = [
            {
                "dt": f.get("dt_txt", ""),
                "temp": f.get("main", {}).get("temp"),
                "condition": f.get("weather", [{}])[0].get("main", ""),
                "description": f.get("weather", [{}])[0].get("description", ""),
                "icon": f.get("weather", [{}])[0].get("icon", ""),
                "pop": f.get("pop", 0),
            }
            for f in forecast_list
        ]

        self._save_cache(result)
        self._append_history(result)
        await self.emit(
            "weather:updated",
            {
                "city": result["city"],
                "temp": result["temp"],
                "condition": result["condition"],
                "source": "openweathermap",
            },
        )
        return result

    def _append_history(self, result: dict):
        if result.get("temp") is None:
            return
        history = self._load_history()
        today_key = date.today().isoformat()
        history = [h for h in history if h.get("date") != today_key]
        history.append(
            {
                "date": today_key,
                "temp": result.get("temp"),
                "temp_min": result.get("temp_min"),
                "temp_max": result.get("temp_max"),
                "condition": result.get("condition"),
                "humidity": result.get("humidity"),
            }
        )
        self._save_history(history[-90:])

    # ── Open-Meteo (keyless, needs location) ───────────────────

    async def _fetch_open_meteo(self) -> dict | None:
        lat, lng, tz = self._global_location()
        if not lat or not lng:
            return None

        now = time.monotonic()
        if self._open_meteo_cache and (now - self._open_meteo_cache_at) < 1800:
            return self._open_meteo_cache

        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lng}"
            f"&current=temperature_2m,weather_code"
            f"&timezone={tz}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    payload = await resp.json()
        except Exception:
            return None

        current = payload.get("current") or {}
        temp = current.get("temperature_2m")
        code = current.get("weather_code", 0)
        if temp is None:
            return None
        data = {
            "source": "open-meteo",
            "emoji": self._code_to_emoji(code),
            "temperature": round(temp),
            "temp": round(temp),
            "unit": "C",
            "description": self._OPEN_METEO_CODES.get(code, ""),
            "fetched_at": datetime.now(UTC).isoformat(),
        }
        self._open_meteo_cache = data
        self._open_meteo_cache_at = now
        return data

    # ── wttr.in (keyless, always-on coarse fallback) ───────────

    @staticmethod
    def _fetch_wttr_blocking(url: str) -> str | None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=3) as response:
                text = response.read().decode("utf-8", errors="replace").strip()
            return text or None
        except Exception:
            return None

    async def _fetch_wttr(self) -> str | None:
        url, ttl = self._wttr_config()
        if not url:
            return None
        now = time.monotonic()
        if self._wttr_cache and (now - self._wttr_cache_at) < ttl:
            return self._wttr_cache
        text = await asyncio.to_thread(self._fetch_wttr_blocking, url)
        if text:
            self._wttr_cache = text
            self._wttr_cache_at = now
        return self._wttr_cache

    # ── unified current() ──────────────────────────────────────

    async def current(self) -> dict:
        """Current weather with refresh + fallback chain. Callable via call_app."""
        # 1. OWM cache (<30 min) or refresh
        cache = self._load_cache()
        fetched = cache.get("fetched_at", "")
        if fetched:
            try:
                fetched_dt = datetime.fromisoformat(fetched)
                age_min = (datetime.now(UTC) - fetched_dt).total_seconds() / 60
                if age_min < 30:
                    return cache
            except (ValueError, TypeError):
                pass
        owm = await self._fetch_owm()
        if owm:
            return owm
        # 2. Open-Meteo keyless
        open_meteo = await self._fetch_open_meteo()
        if open_meteo:
            return open_meteo
        # 3. Stale OWM cache if we have one
        if cache:
            cache["stale"] = True
            return cache
        # 4. wttr.in one-liner
        wttr = await self._fetch_wttr()
        if wttr:
            parts = wttr.split(":")
            val = parts[-1].strip() if len(parts) > 1 else wttr
            return {"source": "wttr.in", "description": val, "summary": wttr}
        return {}

    def get_current(self) -> dict:
        """Sync read from cache — for assistant integrations that can't await."""
        return self._load_cache() or {}

    # ── API routes ─────────────────────────────────────────────

    @web_route("GET", "/api/current")
    async def api_current(self, request):
        data = await self.current()
        if not data:
            return {
                "error": "No weather data. Set location.latitude/longitude in settings, "
                "or add an OpenWeatherMap API key in [apps.weather]."
            }
        return data

    @web_route("GET", "/api/forecast")
    async def api_forecast(self, request):
        cache = self._load_cache()
        return cache.get("forecast", [])

    @web_route("POST", "/api/refresh")
    async def api_refresh(self, request):
        result = await self._fetch_owm()
        if result:
            return result
        open_meteo = await self._fetch_open_meteo()
        if open_meteo:
            return open_meteo
        wttr = await self._fetch_wttr()
        if wttr:
            return {"source": "wttr.in", "summary": wttr}
        return {"error": "Failed to fetch weather"}

    @web_route("GET", "/api/history")
    async def api_history(self, request):
        days = int(request.query_params.get("days", "30"))
        history = self._load_history()
        return history[-days:]

    @web_route("GET", "/api/config-status")
    async def api_config_status(self, request):
        cfg = self._owm_config()
        lat, lng, _ = self._global_location()
        has_location = bool(cfg["city"] or (cfg["lat"] and cfg["lon"]) or (lat and lng))
        return {
            "configured": bool(cfg["api_key"] and has_location) or has_location,
            "has_api_key": bool(cfg["api_key"]),
            "has_location": has_location,
            "city": cfg["city"],
            "wttr_enabled": bool(self._wttr_config()[0]),
        }

    # ── contributions ──────────────────────────────────────────

    async def assistant_context(self) -> str | None:
        data = await self.current()
        if not data:
            return None
        if data.get("temp") is not None:
            temp = data["temp"]
            desc = data.get("description") or data.get("condition") or ""
            unit = data.get("unit", "C")
            city = data.get("city", "")
            parts = [f"{round(temp)}°{unit}", desc]
            if city:
                parts.insert(0, city)
            return "Current Weather: " + ", ".join(p for p in parts if p)
        if data.get("summary"):
            return f"Current Weather: {data['summary']}"
        if data.get("description"):
            return f"Current Weather: {data['description']}"
        return None

    async def panel_hero_weather(self) -> dict | None:
        data = await self.current()
        if not data:
            return None
        if data.get("temp") is not None:
            desc = data.get("description") or data.get("condition") or ""
            # OWM uses 2-char icon codes; Open-Meteo path already ships an emoji.
            emoji = data.get("emoji")
            if not emoji:
                icon_code = str(data.get("icon") or "")
                emoji = self._owm_icon_to_emoji(icon_code)
            return {
                "emoji": emoji,
                "temperature": round(data["temp"])
                if isinstance(data["temp"], (int, float))
                else data["temp"],
                "unit": data.get("unit", "C"),
                "description": desc,
            }
        if data.get("summary"):
            return {"emoji": "☁️", "temperature": "", "unit": "", "description": data["summary"]}
        return None

    async def panel_weather(self) -> dict | None:
        """Compact stat tile fallback for dashboards that don't render hero-weather."""
        hero = await self.panel_hero_weather()
        if not hero:
            return None
        temp = hero.get("temperature")
        val = (
            f"{temp}°{hero.get('unit', '')}"
            if temp not in ("", None)
            else hero.get("description", "")
        )
        return self.stat_tile(
            hero.get("emoji") or "☁️", val, hero.get("description") or "weather", "/weather/"
        )

    @staticmethod
    def _owm_icon_to_emoji(code: str) -> str:
        # Tiny mapping; good-enough icon glyphs for the hub hero.
        first = (code or "")[:2]
        return {
            "01": "☀️",
            "02": "🌤️",
            "03": "☁️",
            "04": "☁️",
            "09": "🌧️",
            "10": "🌦️",
            "11": "⛈️",
            "13": "❄️",
            "50": "🌫️",
        }.get(first, "☁️")

    # ── CLI ─────────────────────────────────────────────────────

    @cli_command("weather", help="Show current weather")
    async def cmd_weather(self):
        data = await self.current()
        if not data:
            print("\n  No weather data. Configure location or an API key.\n")
            return
        if data.get("temp") is not None:
            unit = data.get("unit", "C")
            city = data.get("city") or ""
            desc = data.get("description") or data.get("condition") or ""
            print(f"\n  {city}: {round(data['temp'])}°{unit} ({desc})")
            if data.get("feels_like") is not None:
                print(
                    f"  Feels like {round(data['feels_like'])}°{unit}, "
                    f"Humidity {data.get('humidity', '?')}%"
                )
            if data.get("wind_speed") is not None:
                print(f"  Wind: {data.get('wind_speed')} {data.get('speed_unit', '')}")
            print()
        elif data.get("summary"):
            print(f"\n  {data['summary']}\n")
        else:
            print(f"\n  {data.get('description', '(no data)')}\n")
