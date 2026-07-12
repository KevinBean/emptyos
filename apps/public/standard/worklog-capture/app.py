"""Worklog Capture — friction-free capture source feeding the worklog app.

Two in-the-moment triggers (a watched screenshot folder; a hotkey/tray "live
scrape" that grabs the screen + app/window/browser context) plus two passive
evidence sources (AI-session trail, browser history) are correlated and drafted
LOCALLY into worklog entries for batch review. Nothing touches the vault until
Apply, which routes through ``worklog.log_work`` (propose→preview→confirm).

macOS-first: OS primitives live in ``capture_mac.py`` (future plugin boundary).
Queue + pipeline: ``store.py`` / ``digest.py`` / ``sources.py``. See INTENT.md.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from emptyos.sdk import BaseApp, on_event, scheduled, web_route

from . import digest, store

_APPLY_ALL_CAP = 50  # never apply more than this in one batch; surfaced to the UI


class WorklogCaptureApp(BaseApp):

    # ── config / paths ────────────────────────────────────────────────────────
    def cfg(self, suffix: str, default: Any = None) -> Any:
        """Setting (UI) → emptyos.toml [apps.worklog-capture] → default."""
        v = self.setting(f"worklog-capture.{suffix}", None)
        if v is not None:
            return v
        v = self.app_config(suffix, None)
        if v is not None:
            return v
        return default

    def queue_dir(self) -> Path:
        return self.data_dir / "queue"

    def watch_dir(self) -> Path | None:
        raw = str(self.cfg("watch_dir", "~/Pictures/WorklogCaptures") or "").strip()
        return Path(raw).expanduser() if raw else None

    async def setup(self):
        await super().setup()
        self.queue_dir().mkdir(parents=True, exist_ok=True)

    # ── capture (live scrape) ─────────────────────────────────────────────────
    async def _capture(self, source: str, note: str = "") -> dict:
        from . import capture as cap  # lazy; dispatches mac/win by OS
        queue = self.queue_dir()
        now = datetime.now().astimezone()
        meta = store.create_entry(queue, source, now=now, note=(note or "").strip())
        cid = meta["id"]
        try:
            ok = await cap.grab_screen(store.shot_path(queue, cid))
        except Exception:
            ok = False
        meta["has_image"] = bool(ok)
        app_name = await cap.frontmost_app()
        meta["app_name"] = app_name
        meta["window_title"] = await cap.window_title()
        if self.cfg("include_browser", True):
            bc = await cap.browser_context(app_name)
            meta["browser_url"] = bc.get("url", "")
            meta["browser_title"] = bc.get("title", "")
        if self.cfg("include_selection", False):
            meta["selection"] = await cap.selected_text()
        store.write_meta(queue, cid, meta)
        await self.emit("worklog-capture:captured",
                        {"id": cid, "source": source, "has_image": bool(ok),
                         "app": app_name})
        return {"ok": True, "id": cid, "has_image": bool(ok), "app": app_name}

    @web_route("POST", "/api/capture")
    async def api_capture(self, request):
        data = await self.read_json(request)
        source = str(data.get("source") or "ui").strip() or "ui"
        return await self._capture(source, str(data.get("note") or ""))

    @on_event("tray:capture_clicked")
    async def on_tray_capture(self, event):
        await self._capture("tray")

    # ── digest ────────────────────────────────────────────────────────────────
    @web_route("POST", "/api/digest")
    async def api_digest(self, request):
        data = await self.read_json(request)
        draft = bool(data.get("draft", True))
        return await digest.run_digest(self, draft=draft)

    @scheduled("0 * * * *", id="worklog-capture-digest")
    async def _scheduled_digest(self):
        await digest.run_digest(self, draft=True)

    # ── review queue ──────────────────────────────────────────────────────────
    def _card(self, m: dict) -> dict:
        cid = m["id"]
        ocr = store.read_ocr(self.queue_dir(), cid)
        return {
            "id": cid,
            "source": m.get("source", ""),
            "created": m.get("created", ""),
            "app_name": m.get("app_name", ""),
            "window_title": m.get("window_title", ""),
            "browser_url": m.get("browser_url", ""),
            "browser_title": m.get("browser_title", ""),
            "has_image": bool(m.get("has_image")),
            "evidence_only": bool(m.get("evidence_only")),
            "member_count": len(m.get("members", [])),
            "ocr_excerpt": (ocr[:400] + "…") if len(ocr) > 400 else ocr,
            "evidence": [{"summary": e.get("summary", "")} for e in m.get("evidence", [])],
            "draft": m.get("draft") or {},
        }

    @web_route("GET", "/api/queue")
    async def api_queue(self, request):
        metas = store.iter_metas(self.queue_dir())
        counts: dict[str, int] = {}
        for m in metas:
            counts[m.get("status", "?")] = counts.get(m.get("status", "?"), 0) + 1
        drafted = [m for m in metas if m.get("status") == store.STATUS_DRAFTED]
        flagged = [self._card(m) for m in drafted if not m.get("evidence_only")]
        unflagged = [self._card(m) for m in drafted if m.get("evidence_only")]
        return {
            "flagged": flagged,
            "unflagged": unflagged,
            "counts": counts,
            "new": counts.get(store.STATUS_NEW, 0),
            "last_digest": digest._read_last_digest(self).isoformat()
            if digest._read_last_digest(self) else "",
        }

    @web_route("GET", "/api/thumb/{id}")
    async def api_thumb(self, request):
        from starlette.responses import Response, FileResponse
        cid = request.path_params.get("id", "")
        p = store.shot_path(self.queue_dir(), cid)
        if not cid or not p.exists():
            return Response(status_code=404)
        return FileResponse(str(p), media_type="image/png")

    # ── apply / dismiss ───────────────────────────────────────────────────────
    async def _apply(self, cid: str, fields: dict) -> dict:
        queue = self.queue_dir()
        m = store.read_meta(queue, cid)
        if not m:
            return {"error": "not found"}
        if m.get("status") != store.STATUS_DRAFTED:
            return {"error": f"not reviewable (status: {m.get('status')})"}
        d = m.get("draft") or {}
        text = str(fields.get("text") or d.get("text") or "").strip()
        if not text:
            return {"error": "text required"}
        payload = {
            "date_s": str(fields.get("date") or d.get("date") or ""),
            "project": str(fields.get("project") or d.get("project") or "General"),
            "text": text,
            "status": str(fields.get("status") or d.get("status") or "in-progress"),
            "employer": str(fields.get("employer") or d.get("employer") or ""),
        }
        # Double-apply guard: flip status BEFORE dispatch; roll back on failure.
        m["status"] = store.STATUS_APPLIED
        m["applied"] = payload
        store.write_meta(queue, cid, m)
        try:
            res = await self.call_app("worklog", "log_work", **payload)
        except Exception as e:
            m["status"] = store.STATUS_DRAFTED
            m["applied"] = None
            m["error"] = str(e)[:300]
            store.write_meta(queue, cid, m)
            return {"error": str(e)}
        if isinstance(res, dict) and res.get("error"):
            m["status"] = store.STATUS_DRAFTED
            m["applied"] = None
            store.write_meta(queue, cid, m)
            return res
        await self.emit("worklog-capture:applied",
                        {"id": cid, "project": payload["project"]})
        return {"ok": True, "worklog": res}

    @web_route("POST", "/api/apply/{id}")
    async def api_apply(self, request):
        cid = request.path_params.get("id", "")
        return await self._apply(cid, await self.read_json(request))

    @web_route("POST", "/api/apply-all")
    async def api_apply_all(self, request):
        data = await self.read_json(request)
        items = data.get("items") or []
        capped = len(items) > _APPLY_ALL_CAP
        results = []
        for it in items[:_APPLY_ALL_CAP]:
            cid = str(it.get("id") or "")
            results.append({"id": cid, **(await self._apply(cid, it))})
        applied = sum(1 for r in results if r.get("ok"))
        return {"ok": True, "applied": applied, "results": results,
                "capped": capped, "cap": _APPLY_ALL_CAP}

    @web_route("POST", "/api/dismiss/{id}")
    async def api_dismiss(self, request):
        cid = request.path_params.get("id", "")
        queue = self.queue_dir()
        m = store.read_meta(queue, cid)
        if not m:
            return {"error": "not found"}
        m["status"] = store.STATUS_DISMISSED
        store.write_meta(queue, cid, m)
        await self.emit("worklog-capture:dismissed", {"id": cid})
        return {"ok": True}

    # ── config / setup status ─────────────────────────────────────────────────
    @web_route("GET", "/api/config")
    async def api_config(self, request):
        watch = self.watch_dir()
        metas = store.iter_metas(self.queue_dir())
        counts: dict[str, int] = {}
        for m in metas:
            counts[m.get("status", "?")] = counts.get(m.get("status", "?"), 0) + 1
        try:
            import ocrmac  # noqa: F401
            ocr_backend = "apple-vision"
        except Exception:
            ocr_backend = "ocr-plugin" if self.kernel.services.get_optional("ocr") else "none"
        from . import capture as cap
        last = digest._read_last_digest(self)
        return {
            "watch_dir": str(watch) if watch else "",
            "watch_dir_exists": bool(watch and watch.exists()),
            "ocr_backend": ocr_backend,
            "capture_backend": cap.BACKEND_NAME,
            "include_browser": bool(self.cfg("include_browser", True)),
            "include_selection": bool(self.cfg("include_selection", False)),
            "source_sessions": bool(self.cfg("source_sessions", True)),
            "source_browser": bool(self.cfg("source_browser", True)),
            "source_calendar": bool(self.cfg("source_calendar", True)),
            "group_window_min": int(self.cfg("group_window_min", 10) or 10),
            "counts": counts,
            "last_digest": last.isoformat() if last else "",
        }

    # ── voice verb ────────────────────────────────────────────────────────────
    async def voice_capture_now(self, note: str = "") -> dict:
        res = await self._capture("ui", note)
        if res.get("error"):
            return {"say": "I couldn't capture the screen — check Screen Recording permission."}
        return {"say": "Captured. I'll draft it into your worklog for review.",
                "link": {"text": "Review captures", "href": "/worklog-capture/"}}

    # ── hub panel ─────────────────────────────────────────────────────────────
    async def panel_pending(self):
        drafted = [m for m in store.iter_metas(self.queue_dir())
                   if m.get("status") == store.STATUS_DRAFTED]
        if not drafted:
            return None
        rows = []
        for m in drafted[:6]:
            d = m.get("draft") or {}
            proj = d.get("project") or "General"
            rows.append({
                "title": f"{proj}: {(d.get('text') or '')[:60]}",
                "subtitle": f"{m.get('app_name') or m.get('source', '')} · {d.get('confidence', '')}",
                "href": "/worklog-capture/",
                "icon": "📸",
            })
        return rows
