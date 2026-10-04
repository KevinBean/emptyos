"""Evidence sources — the raw materials the digest cross-checks with captures.

Each reader returns ``Evidence`` spans over a time window, all read locally:

    source_sessions        AI-coding trail (Claude Code + Codex JSONL runs)
    source_browser_history Chrome visit history (sqlite copy-read)

Both degrade silently to ``[]`` when their files don't exist or can't be read —
a source is never a hard dependency. Session scanning reuses the pure primitives
in ``scripts/footprint_common.py`` (same run-grouping the footprint skill uses,
so hour spans stay consistent) rather than duplicating them.

Privacy: these are the most sensitive local data in the system. They are read
transiently per digest run, distilled into short cluster summaries, and never
copied wholesale into the queue. Drafting pins ``strict_provider`` to a local
model while ``local_only`` is on (the default) — see ``digest._draft_cluster``.
Turning that setting off routes the same summaries down the normal (cloud-first)
chain, so the promise this paragraph makes lives in that flag, not in prose.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _default_chrome_history() -> Path:
    """Per-OS Chrome ``History`` sqlite location."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/Google Chrome/Default/History"
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(home / "AppData/Local")
        return Path(base) / "Google/Chrome/User Data/Default/History"
    return home / ".config/google-chrome/Default/History"  # linux


_DEFAULT_CHROME_HISTORY = _default_chrome_history()


@dataclass
class Evidence:
    start: datetime
    end: datetime
    source: str            # "sessions" | "browser"
    kind: str              # "claude" | "codex" | "visit"
    summary: str           # one short human line for the LLM context + UI chip
    ref: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "kind": self.kind,
            "summary": self.summary,
            "span": [self.start.isoformat(), self.end.isoformat()],
            "ref": self.ref,
        }


# ── footprint_common loader (scripts/ isn't a package the daemon imports) ──────
_FOOTPRINT = None


def _load_footprint():
    global _FOOTPRINT
    if _FOOTPRINT is not None:
        return _FOOTPRINT
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "scripts" / "footprint_common.py"
        if cand.exists():
            try:
                if str(cand.parent) not in sys.path:
                    sys.path.insert(0, str(cand.parent))
                spec = importlib.util.spec_from_file_location("footprint_common", cand)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _FOOTPRINT = mod
                return mod
            except Exception:
                break
    _FOOTPRINT = False  # sentinel: tried and failed
    return None


def _clean_slug(name: str) -> str:
    """Claude project dir slug (``-Users-kb-EmptyOS`` / ``D--emptyos``) → last part."""
    parts = [p for p in re.split(r"[-/\\]+", name or "") if p]
    return parts[-1] if parts else ""


# ── AI-session trail ──────────────────────────────────────────────────────────
def source_sessions(window_start: datetime, window_end: datetime, *,
                    idle_min: int = 15, tail_min: float = 3.0,
                    claude_project: str | None = None,
                    footprint=None) -> list[Evidence]:
    fp = footprint if footprint is not None else _load_footprint()
    if not fp:
        return []
    tz = window_start.tzinfo or timezone.utc
    match_all = re.compile("")  # every "timestamp"-bearing line
    idle, tail = timedelta(minutes=idle_min), timedelta(minutes=tail_min)
    out: list[Evidence] = []
    try:
        files = fp.iter_session_files(claude_project)
    except Exception:
        return []
    for tool, path in files:
        try:
            events = [e for e in fp.on_topic_events(path, match_all, tz)
                      if window_start <= e <= window_end]
        except Exception:
            continue
        if not events:
            continue
        hint = _clean_slug(path.parent.name) if tool == "claude" else ""
        for s, e in fp.runs_from(events, idle, tail):
            if e < window_start or s > window_end:
                continue
            where = f" on {hint}" if hint else ""
            out.append(Evidence(
                start=s, end=e, source="sessions", kind=tool,
                summary=f"{tool} session{where} {s:%H:%M}–{e:%H:%M}",
                ref={"hint": hint, "session": path.stem[:28]},
            ))
    return out


# ── browser history ───────────────────────────────────────────────────────────
def chrome_to_dt(chrome_time: int) -> datetime:
    return _CHROME_EPOCH + timedelta(microseconds=int(chrome_time or 0))


def dt_to_chrome(dt: datetime) -> int:
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return int((dt.astimezone(timezone.utc) - _CHROME_EPOCH).total_seconds() * 1_000_000)


def read_browser_sqlite(db_path: str | Path, window_start: datetime,
                        window_end: datetime) -> list[dict]:
    """Read visits in the window from a Chrome-shaped History sqlite. Pure read."""
    rows: list[dict] = []
    lo, hi = dt_to_chrome(window_start), dt_to_chrome(window_end)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception:
        try:
            conn = sqlite3.connect(str(db_path))
        except Exception:
            return []
    try:
        cur = conn.execute(
            "SELECT u.url, u.title, v.visit_time "
            "FROM visits v JOIN urls u ON u.id = v.url "
            "WHERE v.visit_time BETWEEN ? AND ? ORDER BY v.visit_time",
            (lo, hi),
        )
        for url, title, vt in cur.fetchall():
            rows.append({"url": url or "", "title": title or "", "ts": chrome_to_dt(vt)})
    except Exception:
        return []
    finally:
        conn.close()
    return rows


_VISIT_GAP = timedelta(minutes=30)


def _aggregate_visits(rows: list[dict], tz: timezone,
                      *, gap: timedelta = _VISIT_GAP) -> list[Evidence]:
    """Group visits into one Evidence span per domain per *sitting*.

    Splitting on a ``gap`` of silence is load-bearing, not cosmetic. Spanning a
    domain from its first to its last visit in the window turns two visits eight
    hours apart into one eight-hour span — and since ``correlate`` attaches any
    overlapping evidence, that span then attaches to every cluster in the day.
    One over-long span poisons every draft, not just its own.
    """
    by_domain: dict[str, list[dict]] = {}
    for r in rows:
        dom = (urlparse(r["url"]).netloc or "").replace("www.", "")
        if not dom:
            continue
        by_domain.setdefault(dom, []).append(
            {"ts": r["ts"].astimezone(tz), "title": (r.get("title") or "")[:60]})

    out: list[Evidence] = []
    for dom, visits in by_domain.items():
        visits.sort(key=lambda v: v["ts"])
        sittings: list[list[dict]] = []
        for v in visits:
            if sittings and v["ts"] - sittings[-1][-1]["ts"] <= gap:
                sittings[-1].append(v)
            else:
                sittings.append([v])
        for s in sittings:
            titles = sorted({v["title"] for v in s if v["title"]})
            out.append(Evidence(
                start=s[0]["ts"], end=s[-1]["ts"], source="browser", kind="visit",
                summary=f"{len(s)} visit(s) to {dom} — {titles[0] if titles else dom}",
                ref={"domain": dom, "count": len(s), "titles": titles[:5]},
            ))
    out.sort(key=lambda e: e.start)
    return out


_CAL_DEFAULT_MIN = 45


def build_calendar_evidence(days: dict, tz: timezone, *,
                            default_min: int = _CAL_DEFAULT_MIN) -> list[Evidence]:
    """Timed calendar events → Evidence. Pure: takes ``{date_iso: [agenda_items]}``.

    The digest fetches agenda via ``call_app("calendar", "get_agenda")`` and hands
    it here, keeping this reader daemon-free/testable (like the browser reader
    takes a db path). Only *timed events* count — tasks are intentions, not proof
    of work, and all-day items carry no time signal. Events have no end time, so
    each gets a nominal ``default_min`` span for correlation.
    """
    out: list[Evidence] = []
    for ds, items in (days or {}).items():
        try:
            d = date.fromisoformat(str(ds)[:10])
        except Exception:
            continue
        for it in items or []:
            if str(it.get("type", "")).lower() == "task":
                continue
            t = str(it.get("time", "")).strip()
            if not t or t.lower() == "all day":
                continue
            try:
                hh, mm = t.split(":")[:2]
                start = datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=tz)
            except Exception:
                continue
            end = start + timedelta(minutes=default_min)
            title = str(it.get("title", "")).strip() or "event"
            out.append(Evidence(
                start=start, end=end, source="calendar", kind="event",
                summary=f"Calendar: {title} ({start:%H:%M})",
                ref={"title": title, "cal_source": it.get("source", "")},
            ))
    return out


def source_browser_history(window_start: datetime, window_end: datetime, *,
                           history_path: str | Path | None = None,
                           gap: timedelta = _VISIT_GAP) -> list[Evidence]:
    src = Path(history_path) if history_path else _DEFAULT_CHROME_HISTORY
    if not src.exists():
        return []
    tz = window_start.tzinfo or timezone.utc
    tmp = None
    try:
        # Chrome locks its live DB — copy first, read the copy.
        fd, tmp = tempfile.mkstemp(suffix=".sqlite", prefix="wlcap-hist-")
        import os
        os.close(fd)
        shutil.copy2(src, tmp)
        rows = read_browser_sqlite(tmp, window_start, window_end)
        return _aggregate_visits(rows, tz, gap=gap)
    except Exception:
        return []
    finally:
        if tmp:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:
                pass
