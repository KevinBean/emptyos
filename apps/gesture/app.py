"""Gesture — real-time hand-gesture demo.

The browser does all the work: `getUserMedia()` + MediaPipe Tasks Vision
`GestureRecognizer` runs in-page, camera bytes never leave the device.
This backend is a thin ledger — it receives *labels* the browser detected,
appends them to a rolling history, emits `gesture:detected` on the bus, and
serves a user-editable gesture→action label map.

Seven gesture classes come out of MediaPipe's default model:
`Open_Palm`, `Closed_Fist`, `Thumb_Up`, `Thumb_Down`, `Victory`,
`Pointing_Up`, `ILoveYou`. Apps that want to react to a specific gesture
subscribe to `gesture:detected` and match on `data["gesture"]`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from emptyos.sdk import BaseApp, HistoryStore, cli_command, load_json, save_json, web_route


GESTURE_CLASSES = [
    "Open_Palm",
    "Closed_Fist",
    "Thumb_Up",
    "Thumb_Down",
    "Victory",
    "Pointing_Up",
    "ILoveYou",
]

DEFAULT_ACTIONS = {
    "Open_Palm": "Hello / Stop",
    "Closed_Fist": "Pause",
    "Thumb_Up": "Approve",
    "Thumb_Down": "Reject",
    "Victory": "Capture",
    "Pointing_Up": "Next",
    "ILoveYou": "Favourite",
}

HISTORY_LIMIT = 200


class GestureApp(BaseApp):

    def _history(self) -> HistoryStore:
        return HistoryStore(self.data_dir / "history.json", max_entries=HISTORY_LIMIT)

    def _actions_path(self):
        return self.data_dir / "actions.json"

    def _load_history(self) -> list[dict]:
        return self._history().load()

    def _load_actions(self) -> dict:
        return load_json(self._actions_path(), dict(DEFAULT_ACTIONS))

    @cli_command("gesture", help="Show gesture-detection stats")
    async def cli_gesture(self):
        hist = self._load_history()
        if not hist:
            return "No gestures detected yet. Open http://localhost:9000/gesture/ and allow camera access."
        counts: dict[str, int] = {}
        for h in hist:
            g = h.get("gesture", "?")
            counts[g] = counts.get(g, 0) + 1
        lines = [f"{len(hist)} detections across {len(counts)} classes:"]
        for g, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {g:<14} {n}")
        lines.append(f"Last: {hist[-1].get('gesture')} at {hist[-1].get('ts')}")
        return "\n".join(lines)

    @web_route("GET", "/api/ping")
    async def api_ping(self, request):
        return {"ok": True, "classes": GESTURE_CLASSES}

    @web_route("POST", "/api/detected")
    async def api_detected(self, request):
        data = await request.json()
        gesture = (data.get("gesture") or "").strip()
        if gesture not in GESTURE_CLASSES:
            return {"ok": False, "error": f"unknown gesture: {gesture!r}"}
        try:
            confidence = float(data.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        entry = {
            "gesture": gesture,
            "confidence": round(confidence, 4),
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._history().append(entry)

        action = self._load_actions().get(gesture, "")
        await self.emit("gesture:detected", {
            "gesture": gesture,
            "confidence": entry["confidence"],
            "action": action,
        })
        return {"ok": True, "entry": entry, "action": action}

    @web_route("GET", "/api/history")
    async def api_history(self, request):
        limit_raw = request.query_params.get("limit") or "20"
        try:
            limit = max(1, min(HISTORY_LIMIT, int(limit_raw)))
        except ValueError:
            limit = 20
        history = self._load_history()
        return {"history": history[-limit:][::-1], "total": len(history)}

    @web_route("POST", "/api/history/clear")
    async def api_history_clear(self, request):
        self._history().save([])
        return {"ok": True}

    @web_route("GET", "/api/actions")
    async def api_actions(self, request):
        return {"actions": self._load_actions(), "classes": GESTURE_CLASSES}

    async def panel_gesture_stats(self) -> dict | None:
        hist = self._load_history()
        if not hist:
            return None
        counts: dict[str, int] = {}
        for h in hist:
            counts[h.get("gesture", "?")] = counts.get(h.get("gesture", "?"), 0) + 1
        top = max(counts, key=lambda k: counts[k])
        last = hist[-1]
        return {
            "tiles": [
                {"label": "Total", "value": len(hist), "detail": "detections"},
                {"label": "Gestures", "value": len(counts), "detail": "unique"},
                {"label": "Top", "value": top.replace("_", " "), "detail": f"{counts[top]}×"},
                {"label": "Last", "value": last.get("gesture", "?").replace("_", " "),
                 "detail": (last.get("ts") or "")[-8:-3]},
            ]
        }

    @web_route("POST", "/api/actions")
    async def api_actions_set(self, request):
        data = await request.json()
        incoming = data.get("actions") or {}
        if not isinstance(incoming, dict):
            return {"ok": False, "error": "actions must be a dict"}
        current = self._load_actions()
        for k, v in incoming.items():
            if k in GESTURE_CLASSES and isinstance(v, str):
                current[k] = v.strip()[:80]
        save_json(self._actions_path(), current)
        return {"ok": True, "actions": current}
