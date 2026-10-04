"""soundcheck — the hub panel, the voice/assistant verbs, and bank introspection.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the glanceable hub row, the three declared verbs, and the two
read-only routes the page uses to render its home view. Source of truth for
*how soundcheck appears outside its own page*.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``summary`` for contrast ranking, ``sessions``'
state loaders. Do not import from ``.app`` (it imports us, which would cycle).

The panel returns ``None`` until there is something true to say. A hub row
reading "0 drills done" is a guilt prompt, not information — and an app that
nags from the home screen is one the learner learns to skim past.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from . import shared
from .contrasts import CONTRAST_INDEX, ipa_for, spoken_key_for

if TYPE_CHECKING:
    from .app import SoundcheckApp  # noqa: F401 — for type hints only


# ─── Bind to SoundcheckApp class as ────────────────────────────────
#   _worst_contrasts   = _panels._worst_contrasts
#   _bank_pending_path = _panels._bank_pending_path
#   panel_drill        = _panels.panel_drill
#   voice_start      = _panels.voice_start
#   voice_weak       = _panels.voice_weak
#   voice_progress   = _panels.voice_progress
#   api_bank         = _panels.api_bank
#   api_dimensions   = _panels.api_dimensions
#   srs_due          = _panels.srs_due
#   srs_grade        = _panels.srs_grade
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _label(cid: str, *, phone_fmt=None) -> str:
    """A contrast id as something a human would say out loud.

    ``phone_fmt`` formats each individual phone code in a sub/del/ins
    contrast — default is the bare ARPABET code (the original behaviour, kept
    byte-identical for any caller that passes nothing). Pass
    ``contrasts.ipa_for`` for a visual label ("/ɪ/ vs /i/") or
    ``contrasts.spoken_key_for`` for a TTS-safe one ("kit vs fleece") —
    soundcheck-ipa-never-shown: the IPA glyph was already computed in
    contrasts.py's VOWEL_GEOMETRY and never reached a learner-facing label.

    The fallback matters as much as the table: a review card whose front reads
    "ea" asks the learner nothing, and grapheme and stress contrasts are not in
    CONTRAST_INDEX because they are generated per spelling and per word rather
    than declared.
    """
    fmt = phone_fmt or (lambda p: p)
    spec = CONTRAST_INDEX.get(cid)
    if spec:
        if spec["kind"] == "sub":
            return f"{fmt(spec['a'])} vs {fmt(spec['b'])}"
        if spec["kind"] == "del":
            return f"dropped {fmt(spec['a'])}"
        if spec["kind"] == "ins":
            return f"extra {fmt(spec['a'])}"

    kind, _, rest = cid.partition(":")
    if not rest:
        return cid
    if kind == "grapheme":
        return f"⟨{rest.replace('_', '…')}⟩ — which sound?"
    if kind == "stress":
        return f"{rest} — which syllable?"
    if kind == "sub":
        a, sep, b = rest.partition("/")
        return f"{fmt(a)} vs {fmt(b)}" if sep else rest
    if kind == "del":
        return f"dropped {fmt(rest)}"
    if kind == "ins":
        return f"extra {fmt(rest)}"
    return rest


def _visual_phone(phone: str) -> str:
    return f"/{ipa_for(phone)}/"


def _worst_contrasts(self, limit: int = 3) -> list[dict]:
    """Weakest contrasts by this app's own perception history.

    Reads only soundcheck's own store, not the shared weak-phone counts: those
    mix production evidence in, and a hub row about ear training should be
    about the ear.

    Each row carries two labels for the same contrast: ``label`` (IPA glyphs
    — "/ɪ/ vs /i/" — for the hub panel and any other visual surface) and
    ``spoken`` (lexical-set keywords — "kit vs fleece" — for TTS, which
    cannot pronounce IPA glyphs). See soundcheck-ipa-never-shown.
    """
    perception = self._load_state("perception", {})
    srs = self._load_state("contrast-srs", {})
    rows = []
    for cid, row in perception.items():
        misses = int(row.get("misses") or 0)
        trials = int((srs.get(cid) or {}).get("trials") or 0)
        if misses <= 0 or trials <= 0:
            continue
        rows.append({
            "contrast": cid,
            "label": _label(cid, phone_fmt=_visual_phone),
            "spoken": _label(cid, phone_fmt=spoken_key_for),
            "misses": misses,
            "trials": trials,
            "rate": round(misses / trials, 3),
        })
    rows.sort(key=lambda r: (-r["rate"], -r["misses"]))
    return rows[:limit]


# ── Hub panel ─────────────────────────────────────────────────────

async def panel_drill(self) -> list[dict] | None:
    """Glanceable row. ``None`` when there is nothing worth saying yet."""
    if not self._bank.get("count"):
        return None

    worst = self._worst_contrasts(3)
    if not worst:
        sessions = self._list_sessions(1)
        if sessions:
            return None      # played, nothing weak yet — silence is the truth
        return [{
            "title": "Find out which sounds you mix up",
            "subtitle": f"{self._bank['count']} items · 2 minutes",
            "href": "/soundcheck/",
            "icon": "👂",
        }]

    return [
        {
            "title": r["label"],
            "subtitle": f"missed {r['misses']} of {r['trials']}",
            "href": "/soundcheck/",
            "icon": "👂",
        }
        for r in worst
    ]


# ── Verbs ─────────────────────────────────────────────────────────

async def voice_start(self, dimension: str = "", length: int = 0) -> dict:
    """Say what a set would drill, and link into it."""
    if not self._bank.get("count"):
        return {"say": "The sound bank is empty — nothing to drill yet."}

    dim = dimension if dimension in shared.DIMENSION_IDS else ""
    worst = self._worst_contrasts(1)
    focus = worst[0]["label"] if worst else "a spread of sounds"
    where = shared.DIMENSION_INDEX.get(dim, {}).get("label", "everything")

    return {
        "say": f"Starting a set on {where}. Leading with {focus}.",
        "card": {"renderer": "entity-card",
                 "data": {"title": "Sound Check",
                          "subtitle": where,
                          "fields": [{"label": "Leading with", "value": focus}]}},
        "link": {"text": "Open Sound Check", "href": "/soundcheck/"},
    }


async def voice_weak(self, limit: int = 3) -> dict:
    """Name the sound pairs the learner actually confuses."""
    worst = self._worst_contrasts(int(limit or 3))
    if not worst:
        return {"say": "No perception data yet — play a set and I'll know."}
    # TTS reads its own IPA glyphs poorly (spelled out or garbled) — the
    # spoken line uses real lexical-set words, the on-screen card the glyphs.
    spoken = ", ".join(r["spoken"] for r in worst)
    return {
        "say": f"The sounds you mix up most: {spoken}.",
        "card": {"renderer": "stat-tile",
                 "data": [{"label": r["label"], "value": f"{r['misses']}/{r['trials']}"}
                          for r in worst]},
        "link": {"text": "Drill these", "href": "/soundcheck/"},
    }


async def voice_progress(self, days: int = 30) -> dict:
    """Report how the ear training is going."""
    rows = self._list_sessions(60)
    done = [r for r in rows if r["completed"] and not r["abandoned"]]
    if not done:
        return {"say": "You haven't finished a Sound Check set yet."}
    asked = sum(r["asked"] for r in done)
    right = sum(r["right"] for r in done)
    pct = shared.pct(right, asked)
    best = max((r["best_streak"] for r in done), default=0)
    return {
        "say": f"{len(done)} sets, {pct}% right, best streak {best}.",
        "card": {"renderer": "stat-tile",
                 "data": [{"label": "Sets", "value": len(done)},
                          {"label": "Accuracy", "value": f"{pct}%"},
                          {"label": "Best streak", "value": best}]},
        "link": {"text": "Open Sound Check", "href": "/soundcheck/"},
    }


# ── Read-only routes for the home view ────────────────────────────

@web_route("GET", "/api/bank")
async def api_bank(self, request):
    """What is actually loadable, and what is still awaiting review."""
    bank = self._bank
    pending = 0
    path = self._bank_pending_path()
    if path.is_file():
        try:
            pending = sum(1 for line in path.open("r", encoding="utf-8")
                          if line.strip())
        except OSError:
            pending = 0
    return {
        "ok": True,
        "count": bank.get("count", 0),
        "pending_review": pending,
        "dimensions": {d: len(v) for d, v in
                       sorted((bank.get("by_dimension") or {}).items())},
        "contrasts": len(bank.get("contrasts") or []),
    }


@web_route("GET", "/api/dimensions")
async def api_dimensions(self, request):
    """The six axes, with how much of each is playable and how you're doing."""
    bank = self._bank
    by_dim = bank.get("by_dimension") or {}
    perception = self._load_state("perception", {})
    srs = self._load_state("contrast-srs", {})

    seen: dict[str, dict] = {}
    for item in bank.get("items") or []:
        cid = item.get("contrast_id", "")
        d = item.get("dimension", "")
        row = seen.setdefault(d, {"trials": 0, "misses": 0})
        row["trials"] += int((srs.get(cid) or {}).get("trials") or 0)
        row["misses"] += int((perception.get(cid) or {}).get("misses") or 0)

    out = []
    for d in shared.DIMENSIONS:
        stat = seen.get(d["id"], {"trials": 0, "misses": 0})
        trials = stat["trials"]
        out.append({
            **d,
            "items": len(by_dim.get(d["id"]) or []),
            "trials": trials,
            "accuracy": round((trials - stat["misses"]) / trials, 3) if trials else None,
        })
    return {"ok": True, "dimensions": out}


def _bank_pending_path(self):
    from pathlib import Path
    return Path(__file__).parent / "bank" / "_pending.jsonl"


# ── Cross-app review queue ────────────────────────────────────────
# The `srs_due` / `srs_grade` pair `learn/review_all.py` calls on every
# optional source. Cards are *contrasts*, not items: with ~400 items nothing
# would ever come due at item level, and the thing being remembered is "can I
# hear FLEECE against KIT" rather than one particular word pair.

async def srs_due(self, limit: int = 20) -> dict:
    """Contrasts due for review, weakest first.

    ``limit <= 0`` means count-only — the hub tile and voice count path want a
    number and should not pay to build cards for it.
    """
    from datetime import date

    srs = self._load_state("contrast-srs", {})
    today = date.today().isoformat()
    due = [
        row for cid, row in srs.items()
        if str(row.get("next_review") or "") <= today
        and int(row.get("trials") or 0) > 0
    ]
    due.sort(key=lambda r: (str(r.get("next_review") or ""), -int(r.get("trials") or 0)))

    if limit <= 0:
        return {"due_count": len(due), "cards": []}

    perception = self._load_state("perception", {})
    by_contrast = self._bank.get("by_contrast") or {}
    cards = []
    for row in due[:limit]:
        cid = row.get("contrast") or ""
        spec = CONTRAST_INDEX.get(cid) or {}
        examples = [
            " / ".join(w.get("text", "") for w in (it.get("words") or [])[:2])
            for it in (by_contrast.get(cid) or [])[:2]
        ]
        cards.append({
            "contrast": cid,
            "label": _label(cid),
            "note": spec.get("note", ""),
            "examples": [e for e in examples if e.strip(" /")],
            "misses": int((perception.get(cid) or {}).get("misses") or 0),
            "trials": int(row.get("trials") or 0),
            "next_review": row.get("next_review", ""),
        })
    return {"due_count": len(due), "cards": cards}


async def srs_grade(self, contrast: str = "", quality: int = 3) -> dict:
    """Grade one contrast. quality: 1=forgot, 2=hard, 3=good, 4=easy.

    Reviewing here advances **soundcheck's own** schedule only. It deliberately
    does not touch ``dictionary``'s weak-phone entries: those drive production
    drills, and letting a recognition answer push their next_review out would
    quietly stop asking the learner to *say* a sound they can now merely
    recognise.
    """
    from emptyos.sdk.srs import fsrs_schedule, quality_to_rating

    contrast = (contrast or "").strip()
    quality = int(quality)
    if not contrast:
        return {"error": "contrast required"}
    if quality < 1 or quality > 4:
        return {"error": "quality must be 1-4"}

    srs = self._load_state("contrast-srs", {})
    row = srs.get(contrast)
    if row is None:
        return {"error": f"unknown contrast '{contrast}'"}

    fsrs_schedule(row, quality_to_rating(quality))
    row["trials"] = int(row.get("trials") or 0) + 1
    srs[contrast] = row
    self._save_state("contrast-srs", srs)
    return {"ok": True, "contrast": contrast,
            "next_review": row.get("next_review", "")}
