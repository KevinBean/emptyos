"""BookMe — a self-hosted scheduling page (Calendly-shaped).

Two faces:

* **Owner side (auth-gated)** — define event types, weekly availability, and
  review/cancel bookings.
* **Public side (`public_routes`)** — strangers with no login open
  `/bookme/b/<event-type>`, see only open slots, and book. The auth bypass is
  declared in the manifest; this code only ever exposes open slots + accepts a
  booking on the public endpoints.

Storage split (CLAUDE.md § Storage):
* Config (event types + availability) → `data/apps/bookme/config.json` —
  operational settings.
* Bookings → vault notes (`tags: [booking]`) under
  `30_Resources/EmptyOS/bookme/` — human-relevant + recovery-critical + feed
  the calendar agenda.

Outbound confirmation email goes through the `send` capability (email-smtp
plugin); it degrades gracefully (on-screen confirmation + .ics) when no send
provider is configured.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import secrets
from pathlib import Path

from emptyos.sdk import BaseApp, web_route

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9
    ZoneInfo = None  # type: ignore

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULT_CONFIG = {
    "owner_name": "",
    "timezone": "UTC",
    "event_types": [
        {
            "id": "intro-30",
            "name": "Intro call",
            "duration_min": 30,
            "buffer_min": 10,
            "description": "A quick 30-minute introduction.",
            "active": True,
        }
    ],
    # Mon–Fri 09:00–17:00 by default; weekend empty.
    "availability": {
        "mon": [["09:00", "17:00"]],
        "tue": [["09:00", "17:00"]],
        "wed": [["09:00", "17:00"]],
        "thu": [["09:00", "17:00"]],
        "fri": [["09:00", "17:00"]],
        "sat": [],
        "sun": [],
    },
    # Owner-editable email templates. Blank subject/body = use the built-in
    # default (see DEFAULT_EMAIL_TEMPLATES). Variables substituted with
    # str.format-style {name}, {event_name}, {when}, {tz}, {owner_name},
    # {date}, {time}, {duration_min}.
    "email_templates": {
        "confirmed": {"subject": "", "body": ""},
        "cancelled": {"subject": "", "body": ""},
    },
}

DEFAULT_EMAIL_TEMPLATES = {
    "confirmed": {
        "subject": "Booking confirmed · {event_name} · {when}",
        "body": (
            "Hi {name},\n\n"
            "Your {event_name} is confirmed for {when} ({tz}).\n\n"
            "If you need to reschedule or cancel, just reply to this email.\n\n"
            "{sign_off}"
        ),
    },
    "cancelled": {
        "subject": "Booking cancelled · {event_name} · {when}",
        "body": (
            "Hi {name},\n\n"
            "Your {event_name} on {when} ({tz}) has been cancelled.\n\n"
            "Reply to this email if you'd like to book a different time.\n\n"
            "{sign_off}"
        ),
    },
}

VAULT_FOLDER_DEFAULT = "30_Resources/EmptyOS/bookme"


class BookMeApp(BaseApp):
    async def setup(self):
        self._book_lock = asyncio.Lock()

    # ── config ──────────────────────────────────────────────────────────
    @property
    def _config_path(self) -> Path:
        return self.data_dir / "config.json"

    def _load_config(self) -> dict:
        try:
            cfg = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return json.loads(json.dumps(DEFAULT_CONFIG))
        # Backfill any missing top-level keys.
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, json.loads(json.dumps(v)))
        return cfg

    def _save_config(self, cfg: dict) -> None:
        self._config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def _event_type(self, cfg: dict, type_id: str) -> dict | None:
        for et in cfg.get("event_types", []):
            if et.get("id") == type_id:
                return et
        return None

    def _tz(self, cfg: dict):
        name = cfg.get("timezone") or "UTC"
        if ZoneInfo is None:
            return None
        try:
            return ZoneInfo(name)
        except Exception:
            return ZoneInfo("UTC")

    def _now_local(self, cfg: dict) -> _dt.datetime:
        tz = self._tz(cfg)
        now = _dt.datetime.now(tz) if tz else _dt.datetime.now()
        return now.replace(tzinfo=None, microsecond=0)

    # ── bookings (vault) ────────────────────────────────────────────────
    def _list_bookings(self, *, status: str | None = "confirmed") -> list[dict]:
        rows = self.vault_query(tags=["booking"]) or []
        out = []
        for r in rows:
            p = _normalize_booking(r.get("properties", {}) or {})
            if status and p["status"] != status:
                continue
            p["_vault_path"] = r.get("path", "")
            out.append(p)
        return out

    def _taken_intervals(self, date_str: str) -> list[tuple[_dt.datetime, _dt.datetime]]:
        intervals = []
        for b in self._list_bookings(status="confirmed"):
            start = b.get("start", "")
            end = b.get("end", "")
            if not start.startswith(date_str):
                continue
            try:
                intervals.append((_parse_iso(start), _parse_iso(end)))
            except ValueError:
                continue
        return intervals

    # ── slot computation ────────────────────────────────────────────────
    def _slots_for(self, cfg: dict, et: dict, date_str: str) -> list[str]:
        try:
            date_obj = _dt.date.fromisoformat(date_str)
        except ValueError:
            return []
        windows = (cfg.get("availability") or {}).get(WEEKDAYS[date_obj.weekday()], []) or []
        if not windows:
            return []
        duration = int(et.get("duration_min", 30) or 30)
        buffer = int(et.get("buffer_min", 0) or 0)
        step = duration  # grid stride = the slot length
        now_local = self._now_local(cfg)
        taken = self._taken_intervals(date_str)

        slots: list[str] = []
        for win in windows:
            if not (isinstance(win, (list, tuple)) and len(win) == 2):
                continue
            try:
                w_start = _combine(date_obj, win[0])
                w_end = _combine(date_obj, win[1])
            except ValueError:
                continue
            cur = w_start
            while cur + _dt.timedelta(minutes=duration) <= w_end:
                s_start = cur
                s_end = cur + _dt.timedelta(minutes=duration)
                cur += _dt.timedelta(minutes=step)
                if s_start <= now_local:
                    continue
                if _overlaps_any(s_start, s_end, taken, buffer):
                    continue
                slots.append(s_start.strftime("%H:%M"))
        return slots

    # ── owner API (auth-gated) ──────────────────────────────────────────
    @web_route("GET", "/api/config")
    async def api_get_config(self, request):
        return self._load_config()

    @web_route("POST", "/api/config")
    async def api_save_config(self, request):
        body = await self.read_json(request)
        cfg = self._load_config()
        for key in ("owner_name", "timezone", "event_types", "availability", "email_templates"):
            if key in body:
                cfg[key] = body[key]
        self._save_config(cfg)
        return {"ok": True, "config": cfg}

    @web_route("GET", "/api/email-defaults")
    async def api_email_defaults(self, request):
        """Return the built-in template defaults so the Emails editor can
        pre-fill empty fields and offer a "reset" path."""
        return {"defaults": DEFAULT_EMAIL_TEMPLATES}

    @web_route("GET", "/api/bookings")
    async def api_bookings(self, request):
        rows = self._list_bookings(status=None)
        rows.sort(key=lambda b: b.get("start", ""))
        return {"bookings": rows}

    @web_route("POST", "/api/bookings/{booking_id}/cancel")
    async def api_cancel(self, request):
        bid = request.path_params.get("booking_id", "")
        for b in self._list_bookings(status=None):
            if b.get("id") == bid and b.get("_vault_path"):
                self.vault_update(b["_vault_path"], {"status": "cancelled"})
                cfg = self._load_config()
                # Notify booker + emit event without blocking the owner's UI.
                asyncio.create_task(self._send_booking_email(b, cfg, kind="cancelled"))
                asyncio.create_task(
                    self.emit("bookme:cancelled", {"id": bid, "email": b.get("email", "")})
                )
                return {"ok": True}
        return {"error": "booking not found"}

    @web_route("DELETE", "/api/bookings/{booking_id}")
    async def api_delete(self, request):
        bid = request.path_params.get("booking_id", "")
        for b in self._list_bookings(status=None):
            if b.get("id") == bid and b.get("_vault_path"):
                rel = b["_vault_path"]
                try:
                    (self.vault_root / rel).unlink(missing_ok=True)
                except Exception:
                    pass
                self.vault_force_index(rel)
                return {"ok": True}
        return {"error": "booking not found"}

    # ── public API (auth-exempt via public_routes) ──────────────────────
    @web_route("GET", "/api/public/event/{type_id}")
    async def api_public_event(self, request):
        cfg = self._load_config()
        et = self._event_type(cfg, request.path_params.get("type_id", ""))
        if not et or not et.get("active", True):
            return {"error": "not found"}
        return {
            "id": et["id"],
            "name": et.get("name", "Meeting"),
            "duration_min": int(et.get("duration_min", 30) or 30),
            "description": et.get("description", ""),
            "owner_name": cfg.get("owner_name", ""),
            "timezone": cfg.get("timezone", "UTC"),
        }

    @web_route("GET", "/api/public/slots/{type_id}")
    async def api_public_slots(self, request):
        cfg = self._load_config()
        et = self._event_type(cfg, request.path_params.get("type_id", ""))
        if not et or not et.get("active", True):
            return {"error": "not found"}
        date_str = (request.query_params.get("date") or "").strip()
        return {"date": date_str, "slots": self._slots_for(cfg, et, date_str)}

    @web_route("POST", "/api/public/book")
    async def api_public_book(self, request):
        body = await self.read_json(request)
        type_id = (body.get("type") or "").strip()
        date_str = (body.get("date") or "").strip()
        time_str = (body.get("time") or "").strip()
        name = (body.get("name") or "").strip()
        email = (body.get("email") or "").strip()
        note = (body.get("note") or "").strip()

        if not (type_id and date_str and time_str and name and email):
            return {"error": "name, email, date, and time are required"}
        if "@" not in email or len(email) > 254:
            return {"error": "a valid email is required"}
        if len(name) > 120 or len(note) > 2000:
            return {"error": "input too long"}
        # This endpoint is auth-exempt (public_routes), so `name`/`email` are
        # anonymous input flowing into line-oriented sinks (vault frontmatter,
        # .ics). The serializer and _ics_text() both escape now; rejecting
        # control characters at the boundary keeps a single obvious invariant.
        if any(ch in name or ch in email for ch in ("\n", "\r", "\x00")):
            return {"error": "a valid name and email are required"}

        cfg = self._load_config()
        et = self._event_type(cfg, type_id)
        if not et or not et.get("active", True):
            return {"error": "this event type is not available"}

        duration = int(et.get("duration_min", 30) or 30)

        # Re-validate availability inside the lock so two simultaneous bookers
        # can't grab the same slot (read-modify-write race, CLAUDE.md gotcha).
        async with self._book_lock:
            if time_str not in self._slots_for(cfg, et, date_str):
                return {"error": "that slot is no longer available"}
            try:
                start = _combine(_dt.date.fromisoformat(date_str), time_str)
            except ValueError:
                return {"error": "invalid date or time"}
            end = start + _dt.timedelta(minutes=duration)
            bid = "bk-" + secrets.token_hex(5)
            start_iso = start.strftime("%Y-%m-%dT%H:%M")
            end_iso = end.strftime("%Y-%m-%dT%H:%M")
            tz = cfg.get("timezone", "UTC")
            vault_folder = self.vault_config("bookings_dir", VAULT_FOLDER_DEFAULT)
            self.vault_create_note(
                f"{vault_folder}/{bid}.md",
                {
                    "tags": ["booking"],
                    "id": bid,
                    "event_type": type_id,
                    "event_name": et.get("name", "Meeting"),
                    "name": name,
                    "email": email,
                    "start": start_iso,
                    "end": end_iso,
                    "duration_min": duration,
                    "timezone": tz,
                    "status": "confirmed",
                    "created": self._now_local(cfg).strftime("%Y-%m-%dT%H:%M"),
                },
                body=(f"## Note\n\n{note}\n" if note else ""),
            )

        booking = {
            "id": bid,
            "event_name": et.get("name", "Meeting"),
            "name": name,
            "email": email,
            "start": start_iso,
            "end": end_iso,
            "timezone": tz,
            "note": note,
            "owner_name": cfg.get("owner_name", ""),
        }
        # Notify owner + email the booker without blocking the response.
        asyncio.create_task(self._after_book(booking))
        return {
            "ok": True,
            "booking_id": bid,
            "ics_url": f"/bookme/api/public/ics/{bid}",
            "start": start_iso,
            "start_pretty": _format_when(start_iso),
            "timezone": tz,
        }

    @web_route("GET", "/api/public/ics/{booking_id}")
    async def api_public_ics(self, request):
        from fastapi.responses import Response

        bid = request.path_params.get("booking_id", "")
        for b in self._list_bookings(status=None):
            if b.get("id") == bid:
                ics = _build_ics(b)
                return Response(
                    content=ics,
                    media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{bid}.ics"'},
                )
        return {"error": "not found"}

    # ── post-booking side effects ───────────────────────────────────────
    async def _after_book(self, b: dict) -> None:
        try:
            notif = self.kernel.services.get_optional("notifications")
            if notif:
                await notif.send(
                    f"New booking: {b['name']} — {b['event_name']} on {_format_when(b.get('start', ''))}",
                    priority="success",
                    source="bookme",
                )
        except Exception:
            pass

        cfg = self._load_config()
        await self._send_booking_email(b, cfg, kind="confirmed")
        await self.emit(
            "bookme:created",
            {"id": b["id"], "name": b["name"], "email": b["email"], "start": b["start"]},
        )

    async def _send_booking_email(self, b: dict, cfg: dict, *, kind: str) -> None:
        """Email the booker — used for both confirmation and cancellation.

        Templates come from cfg.email_templates[kind]; blank fields fall back
        to DEFAULT_EMAIL_TEMPLATES. Variables: {name}, {event_name}, {when},
        {tz}, {owner_name}, {date}, {time}, {duration_min}, {sign_off}.
        Degrades gracefully if no `send` provider is wired or the call raises.
        """
        if kind not in ("confirmed", "cancelled"):
            return
        owner = (b.get("owner_name") or cfg.get("owner_name") or "").strip()
        start = b.get("start", "")
        vars_ = {
            "name": b.get("name", ""),
            "email": b.get("email", ""),
            "event_name": b.get("event_name", "Meeting"),
            "when": _format_when(start),
            "tz": b.get("timezone", "UTC"),
            "owner_name": owner,
            "date": start.split("T")[0] if "T" in start else start,
            "time": start.split("T")[1] if "T" in start else "",
            "duration_min": str(b.get("duration_min", "")),
            "sign_off": f"— {owner}" if owner else "",
        }
        tmpls = (cfg.get("email_templates") or {}).get(kind) or {}
        default = DEFAULT_EMAIL_TEMPLATES[kind]
        subject_tmpl = (tmpls.get("subject") or "").strip() or default["subject"]
        body_tmpl = (tmpls.get("body") or "").strip() or default["body"]
        subject = _safe_format(subject_tmpl, vars_)
        body = _safe_format(body_tmpl, vars_)

        try:
            res = await self.send(to=b["email"], subject=subject, body=body)
            self.kernel.syslog.info(
                "bookme",
                f"booking {kind} email sent to {b['email']} via {res.get('provider', '?')}",
                data={"booking_id": b.get("id"), "kind": kind, "provider": res.get("provider")},
            )
        except Exception as e:
            self.kernel.syslog.warn(
                "bookme",
                f"booking {kind} email FAILED for {b['email']}: {type(e).__name__}: {e}",
                data={"booking_id": b.get("id"), "kind": kind, "error": str(e), "exc": type(e).__name__},
            )

    # ── calendar integration ────────────────────────────────────────────
    async def list_day(self, target_date: str) -> list[dict]:
        """Agenda items for a date — called by the calendar app."""
        out = []
        for b in self._list_bookings(status="confirmed"):
            if (b.get("start", "")).startswith(target_date):
                t = b["start"].split("T")[-1] if "T" in b.get("start", "") else "All Day"
                out.append(
                    {
                        "time": t,
                        "title": f"{b.get('event_name', 'Meeting')} — {b.get('name', '')}",
                        "type": "booking",
                        "source": "bookme",
                    }
                )
        return out

    # ── hub panel ───────────────────────────────────────────────────────
    async def panel_upcoming(self) -> list[dict] | None:
        cfg = self._load_config()
        now = self._now_local(cfg).strftime("%Y-%m-%dT%H:%M")
        rows = [b for b in self._list_bookings(status="confirmed") if b.get("start", "") >= now]
        rows.sort(key=lambda b: b.get("start", ""))
        if not rows:
            return None
        out = []
        for b in rows[:5]:
            out.append(
                {
                    "text": f"{b.get('event_name', 'Meeting')} — {b.get('name', '')}",
                    "tag": b.get("start", "").replace("T", " "),
                    "tag_tone": "blue",
                    "href": "/bookme/",
                }
            )
        return out


# ── module helpers ──────────────────────────────────────────────────────
def _normalize_booking(p: dict) -> dict:
    """Coerce a vault-frontmatter booking dict to safe string defaults.

    YAML ``key:`` (empty) parses to ``None``, not absent — ``.get(k, "")``
    returns ``None`` in that case and downstream string methods crash. One
    pass here keeps every consumer (slot computation, agenda, panel,
    cancellation email) safe without sprinkling ``(x or "")`` everywhere.
    """
    return {
        "id": (p.get("id") or ""),
        "event_type": (p.get("event_type") or ""),
        "event_name": (p.get("event_name") or "Meeting"),
        "name": (p.get("name") or ""),
        "email": (p.get("email") or ""),
        "start": (p.get("start") or ""),
        "end": (p.get("end") or ""),
        "duration_min": p.get("duration_min") or 0,
        "timezone": (p.get("timezone") or "UTC"),
        "status": (p.get("status") or "confirmed"),
        "created": (p.get("created") or ""),
    }


def _parse_iso(s: str) -> _dt.datetime:
    return _dt.datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")


def _format_when(iso: str) -> str:
    """Format '2026-05-26T10:00' → 'Tue 26 May 2026 at 10:00'.

    Falls back to the raw string if it can't be parsed.
    """
    try:
        d = _parse_iso(iso)
    except (ValueError, TypeError):
        return iso or ""
    return d.strftime("%a %d %b %Y at %H:%M")


def _safe_format(template: str, vars_: dict) -> str:
    """str.format with unknown placeholders left as-is (no KeyError).

    Booker-controlled fields never reach here, so injection isn't a risk —
    but typos in templates shouldn't crash the email send. Unknown
    ``{whatever}`` placeholders survive in the output, making them obvious
    to spot when previewing.
    """
    import string

    class _Safe(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    try:
        return string.Formatter().vformat(template, (), _Safe(vars_))
    except Exception:
        return template


def _combine(date_obj: _dt.date, hhmm: str) -> _dt.datetime:
    h, m = hhmm.split(":")
    return _dt.datetime(date_obj.year, date_obj.month, date_obj.day, int(h), int(m))


def _overlaps_any(
    s_start: _dt.datetime,
    s_end: _dt.datetime,
    taken: list[tuple[_dt.datetime, _dt.datetime]],
    buffer_min: int,
) -> bool:
    buf = _dt.timedelta(minutes=buffer_min)
    for t_start, t_end in taken:
        if s_start < (t_end + buf) and (t_start - buf) < s_end:
            return True
    return False


def _ics_text(s: str) -> str:
    """Escape a TEXT value per RFC 5545 §3.3.11.

    `name` and `email` reach here straight from the anonymous booking form, and
    the .ics is CRLF-delimited: an unescaped newline would let a booker inject
    calendar properties into the file the owner imports. Newlines collapse to
    the literal `\\n` escape; `\\ ; ,` are escaped; other control chars dropped.
    """
    s = str(s).replace("\\", "\\\\")
    s = s.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
    s = s.replace(";", "\\;").replace(",", "\\,")
    return "".join(ch for ch in s if ch >= " " or ch == "\t")


def _build_ics(b: dict) -> str:
    def _ics_dt(s: str) -> str:
        return _parse_iso(s).strftime("%Y%m%dT%H%M%S")

    uid = _ics_text(f"{b.get('id', 'booking')}@emptyos")
    summary = _ics_text(b.get("event_name", "Meeting"))
    description = _ics_text(f"Booked by {b.get('name', '')} ({b.get('email', '')})")
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//EmptyOS//BookMe//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTART:{_ics_dt(b.get('start', ''))}\r\n"
        f"DTEND:{_ics_dt(b.get('end', ''))}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"DESCRIPTION:{description}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
