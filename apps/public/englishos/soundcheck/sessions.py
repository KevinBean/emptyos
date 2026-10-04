"""soundcheck — session persistence and the HTTP surface.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the per-session JSON store, the write lock around every
read-modify-write, and the seven ``/api`` routes the page drives. Source of
truth for *session state on disk*.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``engine`` for every decision, ``summary`` for the
end-of-session view, ``bank_loader`` for the item lookup, ``audio`` for clip
URLs, and ``dictionary`` (fail-soft) for the telemetry write-back.
Do not import from ``.app`` (it imports us, which would cycle).

## Two things here are load-bearing

**Every path goes through ``require_path_segment``.** ``sid`` arrives from a
path parameter and the loader gates on ``Path.exists()`` rather than an index
lookup, which is exactly the shape that let a traversal survive in
``shadowing/sessions.py``. Copying that app's structure without copying its bug
is the point.

**State is persisted per answer, not per session.** A closed tab, a crash or a
daemon restart loses at most the round in flight, and the summary stays
recomputable from what landed. Buffering a whole session in memory would make
the confusion matrix — the one artifact worth trusting — the first casualty of
any interruption.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.utils import require_path_segment

from . import bank_loader, engine, shared, summary
from .accent import review_pairs

if TYPE_CHECKING:
    from .app import SoundcheckApp  # noqa: F401 — for type hints only


# ─── Bind to SoundcheckApp class as ────────────────────────────────
#   _session_path        = _sessions._session_path
#   _load_session        = _sessions._load_session
#   _save_session        = _sessions._save_session
#   _list_sessions       = _sessions._list_sessions
#   _load_state          = _sessions._load_state
#   _save_state          = _sessions._save_state
#   _telemetry           = _sessions._telemetry
#   _write_back          = _sessions._write_back
#   _serve_round         = _sessions._serve_round
#   _fill_audio          = _sessions._fill_audio
#   api_start            = _sessions.api_start
#   api_answer           = _sessions.api_answer
#   api_session          = _sessions.api_session
#   api_complete         = _sessions.api_complete
#   api_abandon          = _sessions.api_abandon
#   api_history          = _sessions.api_history
#   api_review           = _sessions.api_review
#   api_second_pass      = _sessions.api_second_pass
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# ── Paths + the store ─────────────────────────────────────────────

def _session_path(self, sid: str) -> Path:
    """Runtime path for one session.

    ``sid`` comes from a path param, so it is validated as a single path
    segment before it is ever joined — the loader's existence check is not a
    containment check.
    """
    safe = require_path_segment(sid, "session id")
    return self.data_subdir("sessions") / f"{safe}.json"


def _load_session(self, sid: str) -> dict | None:
    """Read one session, or ``None`` if there isn't one under that id.

    A malformed id is *also* ``None`` rather than an exception: it arrives from
    a path param, so a client can send anything, and letting the path guard's
    ValueError escape turns a rejected request into a 500 and a syslog entry
    that reads like a server fault. Nothing is served either way — this only
    decides whether the refusal is honest about whose mistake it was.
    """
    try:
        path = self._session_path(sid)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_session(self, session: dict) -> None:
    path = self._session_path(session["sid"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def _list_sessions(self, limit: int = 30) -> list[dict]:
    root = self.data_subdir("sessions")
    if not root.is_dir():
        return []
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime,
                   reverse=True)[:limit]
    out = []
    for path in files:
        try:
            s = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "sid": s.get("sid", ""),
            "mode": s.get("mode", ""),
            "started": s.get("started", ""),
            "completed": bool(s.get("completed")),
            "abandoned": bool(s.get("abandoned")),
            **engine.progress(s),
        })
    return out


def _load_state(self, name: str, default):
    path = self.data_dir / f"{name}.json"
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _save_state(self, name: str, value) -> None:
    self.data_dir.mkdir(parents=True, exist_ok=True)
    (self.data_dir / f"{name}.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


# ── Telemetry in, telemetry out ───────────────────────────────────

async def _telemetry(self) -> dict:
    """The learner's existing pronunciation evidence, and whether we got it.

    ``dictionary`` is optional, so a missing one degrades to a cold start —
    which is fully supported and needs no announcement. But an *error* is not
    the same thing as an absence, and this call previously could not tell them
    apart: it asked for a ``@web_route`` handler, which ``call_app`` invokes
    directly and which therefore raised TypeError for a missing ``request``,
    and a bare ``except`` turned that into the same empty result a fresh
    install produces. The app ran with no telemetry at all and looked healthy.

    So the reason travels with the result. A surface that says "nothing
    recorded yet" when the truth is "the read is broken" is worse than an
    error, because nobody goes looking.
    """
    try:
        snap = await self.call_app("dictionary", "pronounce_snapshot")
    except AttributeError:
        return {"weak": {}, "pairs": {}, "available": False,
                "error": "this dictionary is older than pronounce_snapshot"}
    except Exception as exc:                      # noqa: BLE001 — reported, not hidden
        return {"weak": {}, "pairs": {}, "available": False,
                "error": f"{type(exc).__name__}: {exc}"}

    if not isinstance(snap, dict):
        return {"weak": {}, "pairs": {}, "available": False,
                "error": f"unexpected response: {type(snap).__name__}"}

    return {
        "weak": snap.get("weak") or {},
        "pairs": snap.get("pairs") or {},
        "available": True,
        "error": "",
    }


async def _write_back(self, events: list[dict], *, word: str = "") -> None:
    """Send perception misses to the same store production misses go to.

    Capped per phone per session (``MAX_EVENTS_PER_PHONE``) because selecting
    on weak phones and then writing errors back to weak phones is a loop that
    amplifies whatever it started with.

    The ``channel`` kwarg keeps the two kinds of evidence apart at the sink. If
    the installed dictionary predates it the call still lands — its counters
    simply merge, which is the pre-existing behaviour and not a regression.
    """
    if not events:
        return
    try:
        await self.call_app(
            "dictionary", "log_pronounce_events",
            events=events, source=shared.SOURCE_TAG, sentence=word,
            channel="perception",
        )
    except TypeError:
        try:
            await self.call_app("dictionary", "log_pronounce_events",
                                events=events, source=shared.SOURCE_TAG,
                                sentence=word)
        except Exception:
            pass
    except Exception:
        pass


# ── Serving a round ───────────────────────────────────────────────

async def _serve_round(self, session: dict) -> dict | None:
    """Draw, build and prewarm — the one place a round is produced."""
    tele = await self._telemetry()
    heat = self._load_state("heat", {})
    srs = self._load_state("contrast-srs", {})
    perception = self._load_state("perception", {})

    audio = {}
    if session["plan"]["audio_ok"]:
        audio = await self._audio_for_next(session)

    payload = engine.next_round(
        session, self._bank,
        weak=tele["weak"], pairs=tele["pairs"], perception=perception,
        srs=srs, heat=heat, audio=audio,
    )
    if payload is not None and session["plan"]["audio_ok"]:
        await self._fill_audio(session, payload)
    return payload


async def _fill_audio(self, session: dict, payload: dict) -> None:
    """Give the round the clips for the words it actually drew.

    The prewarm can only guess: it warms a slice of the eligible pool before
    selection has run, and the item that wins is often not in it. Guessing wrong
    is silent — the round renders with an empty audio URL and the page shows
    "no clip", which reads as a broken voice engine rather than a cache miss.
    Measured 2026-08-15: one clip across six rounds.

    So the round is built first and its clips resolved after, by looking the
    drawn item back up. A hit costs a dict lookup; a miss costs the one
    synthesis that round needed anyway.
    """
    stimulus = payload.get("stimulus") or {}
    if stimulus.get("mode") not in ("audio", "both"):
        return
    row = (session.get("rounds") or [{}])[-1]
    item = (self._bank.get("by_id") or {}).get(row.get("item_id", ""))
    words = [w.get("text", "") for w in (item or {}).get("words") or []]
    if not words:
        return

    items = stimulus.get("items") or []
    # One stimulus means the first word; several means one word each, in order.
    for index, entry in enumerate(items):
        if entry.get("audio"):
            continue
        text = words[index] if len(items) > 1 and index < len(words) else words[0]
        url = await self._synthesise(text)
        if url:
            entry["audio"] = url


# ── Routes ────────────────────────────────────────────────────────

@web_route("POST", "/api/session/start")
async def api_start(self, request):
    body = await request.json() if request.method == "POST" else {}
    mode = str(body.get("mode") or "quick")
    dims = tuple(d for d in (body.get("dimensions") or [])
                 if d in shared.DIMENSION_IDS)

    audio_ok = await self._probe_audio()
    sid = "sc_" + secrets.token_hex(6)

    # The eye-then-ear diagnostic needs its questions decided up front, because
    # both passes must ask the same ones. Dark by default.
    dual = bool(body.get("dual")) and bool(
        self.app_config("feature.dual-pass.enabled", False))
    modality = "eye" if dual else str(body.get("modality") or "ear")
    order: list[str] = []
    if dual:
        tele0 = await self._telemetry()
        order = bank_loader.plan_order(
            self._bank,
            int(body.get("length") or shared.DIAGNOSTIC_LENGTH),
            weak=tele0["weak"], pairs=tele0["pairs"],
            perception=self._load_state("perception", {}),
            srs=self._load_state("contrast-srs", {}),
            # Both halves of the filter matter. Only shapes that play BOTH
            # ways can be compared across passes — and only dimensions that
            # mean something by eye. A reduction item read as text answers
            # itself: "gonna" beside "going to" is not a perception question,
            # so the eye pass would score full marks and the comparison would
            # report a listening gap that is really a reading freebie.
            dimensions=tuple(dims or shared.EYE_PLAYABLE),
            shapes=tuple(_dual_shapes(audio_ok)),
        )

    session = engine.new_session(
        sid,
        mode=mode,
        dimensions=dims,
        length=int(body.get("length") or 0),
        lives=int(body.get("lives", self.setting_or_config(
            "soundcheck.lives", shared.DEFAULT_LIVES))),
        modality=modality,
        audio_ok=audio_ok,
        deadline_ms=int(self.setting_or_config(
            "soundcheck.deadline_s", 12)) * 1000,
        pass_of=body.get("pass_of"),
        item_order=order,
    )

    async with self.write_lock(f"soundcheck:{sid}"):
        round_payload = await self._serve_round(session)
        self._save_session(session)

    await self.emit("soundcheck:session_started",
                    {"sid": sid, "mode": mode, "audio_ok": audio_ok})

    return {
        "ok": True,
        "sid": sid,
        "plan": session["plan"],
        "round": round_payload,
        "progress": engine.progress(session),
        "audio_ok": audio_ok,
        "dual": bool(order),
        "note": "" if audio_ok else (
            "No voice engine right now — running the spelling, stress and "
            "syllable half of the gym."
        ),
    }


@web_route("POST", "/api/session/{sid}/answer")
async def api_answer(self, request):
    sid = request.path_params.get("sid", "")
    body = await request.json()

    async with self.write_lock(f"soundcheck:{sid}"):
        session = self._load_session(sid)
        if session is None:
            return {"ok": False, "error": "no such session"}

        rid = int(body.get("rid") or 0)
        row = next((r for r in session["rounds"] if r.get("rid") == rid), None)
        item = self._bank["by_id"].get(row["item_id"]) if row else None

        verdict = engine.grade(
            session, rid,
            [str(c) for c in (body.get("choice") or [])],
            item=item,
            elapsed_ms=int(body.get("elapsed_ms") or 0),
            replays=int(body.get("replays") or 0),
            timeout=bool(body.get("timeout")),
        )
        if verdict.get("error"):
            return {"ok": False, **verdict}

        self._record_outcome(session, verdict, item)

        nxt = None
        if not engine.is_done(session):
            nxt = await self._serve_round(session)
        else:
            engine.complete(session)

        self._save_session(session)

    await self._write_back(verdict.pop("events", []),
                           word=(item or {}).get("words", [{}])[0].get("text", ""))

    if engine.is_done(session):
        await self.emit("soundcheck:session_completed",
                        {"sid": sid, **engine.progress(session)})

    return {
        "ok": True,
        **verdict,
        "next": nxt,
        "progress": engine.progress(session),
        "summary": await _summarise(self, session) if engine.is_done(session) else None,
    }


async def _summarise(self, session: dict) -> dict:
    """Summarise, joining the first pass when this session is a second one.

    Also rolls the worst-contrast list forward. ``cleared`` compares against the
    list from *before* this session, so the celebration means "you beat
    something you were previously failing" rather than "you did well today" —
    which is the only version of it that is earned.
    """
    other = None
    if session.get("pass_of"):
        other = self._load_session(session["pass_of"])
    previous = self._load_state("last-worst", [])
    out = summary.summarise(session, other=other, previous_worst=previous)

    if out.get("worst"):
        self._save_state("last-worst", out["worst"])
    if out.get("cleared"):
        await self.emit("soundcheck:contrast_cleared",
                        {"sid": session.get("sid", ""), "cleared": out["cleared"]})
    return out


@web_route("GET", "/api/session/{sid}")
async def api_session(self, request):
    session = self._load_session(request.path_params.get("sid", ""))
    if session is None:
        return {"ok": False, "error": "no such session"}
    return {"ok": True, "session": {k: v for k, v in session.items()
                                    if k != "rounds"},
            "progress": engine.progress(session),
            "done": engine.is_done(session)}


@web_route("POST", "/api/session/{sid}/complete")
async def api_complete(self, request):
    sid = request.path_params.get("sid", "")
    async with self.write_lock(f"soundcheck:{sid}"):
        session = self._load_session(sid)
        if session is None:
            return {"ok": False, "error": "no such session"}
        engine.complete(session)
        self._save_session(session)
    return {"ok": True, "summary": await _summarise(self, session)}


@web_route("POST", "/api/session/{sid}/abandon")
async def api_abandon(self, request):
    """A partial run must not touch the streak — it was never finished."""
    sid = request.path_params.get("sid", "")
    async with self.write_lock(f"soundcheck:{sid}"):
        session = self._load_session(sid)
        if session is None:
            return {"ok": False, "error": "no such session"}
        engine.abandon(session)
        self._save_session(session)
    return {"ok": True}


@web_route("POST", "/api/session/{sid}/second-pass")
async def api_second_pass(self, request):
    """Run the same questions again, by ear this time.

    Two linked sessions rather than one split session: an abandoned half must
    not corrupt the comparison, and the second pass should be startable hours
    later without holding the first one open.
    """
    first_sid = request.path_params.get("sid", "")
    first = self._load_session(first_sid)
    if first is None:
        return {"ok": False, "error": "no such session"}
    order = first.get("item_order") or []
    if not order:
        return {"ok": False, "error": "that session was not a diagnostic"}
    if not first.get("completed"):
        return {"ok": False, "error": "finish the first pass first"}

    audio_ok = await self._probe_audio()
    if not audio_ok:
        return {"ok": False,
                "error": "the second pass is the listening one — no voice "
                         "engine is reachable right now"}

    sid = "sc_" + secrets.token_hex(6)
    session = engine.new_session(
        sid,
        mode=first.get("mode", "diagnostic"),
        dimensions=tuple(first["plan"]["dimensions"]),
        length=len(order),
        lives=0,                      # a diagnostic measures; it does not judge
        modality="ear",
        audio_ok=True,
        deadline_ms=first["plan"]["deadline_ms"],
        pass_of=first_sid,
        item_order=order,
    )

    async with self.write_lock(f"soundcheck:{sid}"):
        round_payload = await self._serve_round(session)
        self._save_session(session)

    return {"ok": True, "sid": sid, "plan": session["plan"],
            "round": round_payload, "progress": engine.progress(session),
            "pass_of": first_sid}


def _dual_shapes(audio_ok: bool) -> list[str]:
    """Shapes a learner can meet by eye AND by ear.

    A minimal-pair round has no eye form — shown as text it answers itself — so
    including it would make half the comparison undefined.
    """
    from .shapes import SHAPES
    return [s.id for s in SHAPES.values()
            if "ear" in s.modalities and "eye" in s.modalities
            and not (s.needs_audio and not audio_ok)]


@web_route("GET", "/api/history")
async def api_history(self, request):
    limit = int(request.query_params.get("limit") or 30)
    rows = self._list_sessions(limit)
    done = [r for r in rows if r["completed"] and not r["abandoned"]]
    return {
        "ok": True,
        "sessions": rows,
        "played": len(done),
        "heatmap": _heatmap(rows),
    }


@web_route("GET", "/api/review")
async def api_review(self, request):
    """Which of the recorded slips are not actually mistakes.

    The first thing this app can usefully say. It reads the learner's existing
    pronunciation telemetry and separates real weaknesses from scorer artifacts
    and legitimate Australian-English features — before any drilling happens.
    """
    tele = await self._telemetry()
    report = review_pairs(tele["pairs"])
    return {"ok": True, **report,
            "telemetry_available": tele["available"],
            "telemetry_error": tele["error"]}


def _heatmap(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        day = (r.get("started") or "")[:10]
        if day:
            out[day] = out.get(day, 0) + 1
    return out
