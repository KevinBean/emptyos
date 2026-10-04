"""Dictionary (pictures) — the picture learner's progress store and schedule.

Absorbed from the retired ``picture-dict`` app 2026-08-19. Owns
``data/apps/dictionary/picture-progress.json`` — every counter, every star, and
the FSRS schedule for the picture cards the learner has actually enrolled.

It deliberately keeps its **own** store rather than merging into ``srs.json``:
a pack slug and a saved word can be the same string (``tiger``), so one flat
dict would let them overwrite each other. Dictionary already runs a second
schedule store for phones (``weak-phones.json``), so this is the established
shape, not a new one.

``picture_due`` / ``picture_grade`` are the picture halves of the ``learn``
contract; the unified ``srs_due`` / ``srs_grade`` in ``srs.py`` call them and
own the ``pic:`` id prefix that tells the two kinds apart.

Scheduling is delegated to ``emptyos/sdk/srs.py`` — there is no private
interval ladder here, deliberately.

Cross-module callers reach these via ``self.X`` after re-binding.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.srs import RATINGS, fsrs_schedule, repair_legacy_schedule

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only


# ─── Bind to DictionaryApp class as ─────────────────────────────────
#   load_picture_progress    = _pic_progress.load_picture_progress
#   save_picture_progress    = _pic_progress.save_picture_progress
#   picture_record_answer    = _pic_progress.picture_record_answer
#   picture_enroll           = _pic_progress.picture_enroll
#   picture_due              = _pic_progress.picture_due     # learn contract half
#   picture_grade            = _pic_progress.picture_grade   # learn contract half
#   picture_stats            = _pic_progress.picture_stats
#   picture_scene_outcome    = _pic_progress.picture_scene_outcome
#   api_picture_srs_due      = _pic_progress.api_picture_srs_due
#   api_picture_srs_grade    = _pic_progress.api_picture_srs_grade
#   api_picture_srs_enroll   = _pic_progress.api_picture_srs_enroll
#   api_picture_srs_unenroll = _pic_progress.api_picture_srs_unenroll
#   panel_picture_due        = _pic_progress.panel_picture_due
#   voice_picture_due        = _pic_progress.voice_picture_due
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_picture_progress(self) -> dict:
    """Read the progress store. Only slugs the learner has actually touched
    appear — a fresh install is ``{}``, not 130 empty rows."""
    p = self.data_dir / "picture-progress.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    # Cheap insurance against a hand-edited or stranded schedule.
    repair_legacy_schedule([e["srs"] for e in data.values()
                            if isinstance(e, dict) and isinstance(e.get("srs"), dict)])
    return data


def save_picture_progress(self, prog: dict) -> None:
    p = self.data_dir / "picture-progress.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(prog, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


async def picture_record_answer(self, slug: str, *, correct: bool, chose: str = "") -> None:
    """Record one quiz answer. A miss enrols the card and remembers *what it was
    confused with*, which is what lets the next quiz pick sharper distractors."""
    async with self._prog_lock:
        prog = self.load_picture_progress()
        entry = prog.setdefault(slug, {})
        entry["seen"] = int(entry.get("seen", 0)) + 1
        entry["last_seen"] = _now_iso()
        if correct:
            entry["quiz_right"] = int(entry.get("quiz_right", 0)) + 1
        else:
            entry["quiz_wrong"] = int(entry.get("quiz_wrong", 0)) + 1
            if chose and chose != slug:
                conf = entry.setdefault("confusions", {})
                conf[chose] = int(conf.get(chose, 0)) + 1
            entry.setdefault("srs", {})  # a miss enrols the card
        self.save_picture_progress(prog)


async def picture_enroll(self, slug: str, *, on: bool = True) -> dict:
    if slug not in self.items:
        return {"error": f"unknown animal '{slug}'"}
    async with self._prog_lock:
        prog = self.load_picture_progress()
        entry = prog.setdefault(slug, {})
        was_enrolled = isinstance(entry.get("srs"), dict)
        if on:
            entry.setdefault("srs", {})
        else:
            entry.pop("srs", None)  # drop the schedule, keep the stats
        self.save_picture_progress(prog)
    # `added` tells a caller whether this call put the card into review, so a
    # "N words added" message never counts words that were already there.
    return {"ok": True, "slug": slug, "enrolled": bool(on),
            "added": bool(on) and not was_enrolled}


# ─── The learn contract ──────────────────────────────────────────────


async def picture_due(self, limit: int = 20) -> dict:
    """Cards due today. ``limit <= 0`` returns the count only, doing no item
    lookups — that is the shape ``learn``'s due-count path and the hub panel
    call, and it must stay cheap."""
    prog = self.load_picture_progress()
    today = date.today().isoformat()
    due = [
        (s, e) for s, e in prog.items()
        if isinstance(e, dict) and isinstance(e.get("srs"), dict)
        and (e["srs"].get("next_review") or today) <= today
    ]
    due.sort(key=lambda kv: kv[1]["srs"].get("next_review") or "")

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    if limit <= 0:
        return {"cards": [], "due_count": len(due)}

    cards = []
    for slug, entry in due[:limit]:
        item = self.items.get(slug)
        if not item:
            continue  # the pack shrank; stale progress is not an error
        cards.append({
            "slug": slug,
            "name": item["name"],
            "chinese": item["chinese"],
            "pinyin": item.get("pinyin", ""),
            "hint": item.get("hint", ""),
            "category": item["category"],
            "emoji": item["emoji"],
            "image": self.image_url(slug),
            "next_review": entry["srs"].get("next_review", today),
            "review_count": int(entry["srs"].get("review_count", 0)),
        })
    return {"cards": cards, "due_count": len(due)}


async def picture_grade(self, slug: str, rating: str = "good") -> dict:
    """Grade one card. Accepts the ``again|hard|good|easy`` vocabulary directly —
    this app is new, so it has no legacy numeric ladder to honour."""
    slug = (slug or "").strip()
    if not slug:
        return {"error": "slug required"}
    if slug not in self.items:
        return {"error": f"unknown animal '{slug}'"}
    rating = str(rating or "good").lower()
    if rating not in RATINGS:
        return {"error": f"rating must be one of {', '.join(RATINGS)}"}

    async with self._prog_lock:
        prog = self.load_picture_progress()
        entry = prog.setdefault(slug, {})
        srs = entry.setdefault("srs", {})
        fsrs_schedule(srs, rating)
        entry["last_seen"] = _now_iso()
        self.save_picture_progress(prog)
    return {"ok": True, "slug": slug,
            "next_review": srs.get("next_review", ""),
            "review_count": int(srs.get("review_count", 0))}


async def picture_scene_outcome(self, used: list, source: str = "") -> list[str]:
    """Record words the learner produced in a conversation; return the slugs
    whose review this counted as.

    Saying a word unprompted in a scene is at least as strong as naming its
    picture on a card, so a card that is **due today** is graded ``good`` —
    the scene was its review. A card not yet due is left alone: grading early
    would stretch its interval on evidence the schedule did not ask for.
    Every word said is counted under ``scene`` either way, which is what the
    card shows as "said in a conversation". One read-modify-write, so a
    concurrent grade cannot be lost between the count and the schedule.

    "Due" is ``picture_due``'s rule, so an enrolled card never yet reviewed
    (``srs == {}``) counts: saying it unprompted is a fair first review.
    The grade mirrors ``picture_grade`` (``fsrs_schedule`` + ``last_seen``)
    inside this one lock — change the two together.
    """
    today = date.today().isoformat()
    reviewed = []
    async with self._prog_lock:
        prog = self.load_picture_progress()
        for slug in dict.fromkeys(used):   # one utterance counts once
            if slug not in self.items:
                continue
            entry = prog.setdefault(slug, {})
            scene = entry.setdefault("scene", {})
            scene["said"] = int(scene.get("said", 0)) + 1
            scene["last"] = today
            scene["last_source"] = str(source or "")
            srs = entry.get("srs")
            if isinstance(srs, dict) and (srs.get("next_review") or today) <= today:
                fsrs_schedule(srs, "good")
                entry["last_seen"] = _now_iso()
                reviewed.append(slug)
        self.save_picture_progress(prog)
    return reviewed


def picture_stats(self) -> dict:
    prog = self.load_picture_progress()
    today = date.today().isoformat()
    learning = sum(1 for e in prog.values()
                   if isinstance(e, dict) and isinstance(e.get("srs"), dict))
    due = sum(1 for e in prog.values()
              if isinstance(e, dict) and isinstance(e.get("srs"), dict)
              and (e["srs"].get("next_review") or today) <= today)
    starred = sum(1 for e in prog.values() if isinstance(e, dict) and e.get("saved"))
    spoken = sum(1 for e in prog.values() if isinstance(e, dict)
                 and isinstance(e.get("scene"), dict) and e["scene"].get("said"))
    return {"total": len(self.items), "learning": learning, "due": due, "starred": starred,
            "said_in_scenes": spoken}


# ─── Routes ──────────────────────────────────────────────────────────


@web_route("GET", "/api/picture/srs/due")
async def api_picture_srs_due(self, request):
    try:
        limit = int(request.query_params.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    return await self.picture_due(limit=limit)


@web_route("POST", "/api/picture/srs/grade")
async def api_picture_srs_grade(self, request):
    body = await request.json() if await request.body() else {}
    return await self.picture_grade(str(body.get("slug") or ""), str(body.get("rating") or "good"))


@web_route("POST", "/api/picture/srs/enroll")
async def api_picture_srs_enroll(self, request):
    body = await request.json() if await request.body() else {}
    return await self.picture_enroll(str(body.get("slug") or ""), on=True)


@web_route("POST", "/api/picture/srs/unenroll")
async def api_picture_srs_unenroll(self, request):
    body = await request.json() if await request.body() else {}
    return await self.picture_enroll(str(body.get("slug") or ""), on=False)


# ─── Contributions ───────────────────────────────────────────────────


async def panel_picture_due(self) -> dict | None:
    """Hub tile. Returns None — and so renders nothing — until the learner has
    actually enrolled a card, so an untouched install shows no clutter."""
    st = self.picture_stats()
    if not st["learning"]:
        return None
    return {"label": "Pictures due", "value": st["due"],
            "href": "/dictionary/#pictures"}


async def voice_picture_due(self) -> dict:
    st = self.picture_stats()
    if not st["learning"]:
        return {"say": "You have not started any picture cards yet."}
    if not st["due"]:
        return {"say": f"Nothing due. You are learning {st['learning']} pictures."}
    return {
        "say": f"{st['due']} picture card{'s' if st['due'] != 1 else ''} due for review.",
        "link": {"text": "Open review", "href": "/dictionary/#pictures"},
    }
