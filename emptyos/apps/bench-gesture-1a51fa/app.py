"""Bench Gesture (camera demo).

This is a lightweight benchmark-style app: the browser captures camera frames,
performs a simple, heuristic hand-pose classification (open-palm vs closed fist
vs thumbs up/down) using landmarks exposed by MediaPipe Hands.

The frontend logs detected gestures to the backend so we can measure end-to-end
latency and track development effort.

Note: MediaPipe runs client-side; this app does not ship ML models.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from emptyos.sdk import BaseApp, web_route


class BenchGestureApp(BaseApp):
    def _data_dir(self) -> Path:
        d = self.kernel.data_dir / "bench-gesture-1a51fa"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _events_path(self) -> Path:
        return self._data_dir() / "gesture_events.jsonl"

    def _config_path(self) -> Path:
        return self._data_dir() / "config.json"

    def _read_config(self) -> dict:
        p = self._config_path()
        if not p.exists():
            return {
                "max_events": 5000,
                "echo_back": True,
            }
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {"max_events": 5000, "echo_back": True}

    def _append_event(self, ev: dict) -> None:
        p = self._events_path()
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

        cfg = self._read_config()
        max_events = int(cfg.get("max_events") or 5000)
        # Keep file bounded (best-effort)
        try:
            lines = p.read_text(encoding="utf-8").splitlines()[-max_events:]
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    @web_route("GET", "/api/config")
    async def api_config(self, request):
        return self._read_config()

    @web_route("POST", "/api/config")
    async def api_config_set(self, request):
        data = await request.json()
        if not isinstance(data, dict):
            data = {}
        cfg = self._read_config()
        # Only allow a small allowlist
        if "max_events" in data:
            cfg["max_events"] = int(data.get("max_events") or 5000)
        if "echo_back" in data:
            cfg["echo_back"] = bool(data.get("echo_back"))
        self._config_path().write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ok": True, "config": cfg}

    @web_route("POST", "/api/gesture")
    async def api_gesture(self, request):
        data = await request.json()
        gesture = (data.get("gesture") or "").strip()
        confidence = data.get("confidence", None)
        if not gesture:
            return {"ok": False, "error": "missing gesture"}

        ev = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "unix_ms": int(time.time() * 1000),
            "gesture": gesture,
            "confidence": confidence,
            "meta": {
                "mirror": data.get("mirror", True),
                "source": data.get("source", "camera"),
            },
        }
        self._append_event(ev)
        cfg = self._read_config()
        return {
            "ok": True,
            "echo": cfg.get("echo_back", True),
            "event": ev if cfg.get("echo_back", True) else None,
        }

    @web_route("GET", "/api/gesture/events")
    async def api_events(self, request):
        try:
            limit = int(request.query_params.get("limit") or 50)
        except ValueError:
            limit = 50
        limit = max(1, min(500, limit))

        p = self._events_path()
        if not p.exists():
            return {"events": [], "total": 0}
        lines = p.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        tail = lines[-limit:]
        out = []
        for line in tail:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        out.reverse()
        return {"events": out, "total": total}
