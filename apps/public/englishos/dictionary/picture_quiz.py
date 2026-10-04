"""Picture Dictionary — quiz round construction and grading.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md rule 4).
Owns: building a quiz session's rounds (including distractor choice), holding the
answer key server-side, and grading an answer. Reads the catalog indexes and the
progress store; writes nothing itself except the session file.

``build_rounds`` is PURE and seeded, so a page reload replays the same quiz and
``tests/test_unit_picture_dict.py`` can assert the distractor rules with no daemon.

Cross-module callers reach the routes here via ``self.X`` after re-binding.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import random
import time
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.utils import safe_path_segment

# picture_catalog is the pure leaf every picture module may import (the shared.py
# exception in .claude/rules/multi-module-apps.md) — no self, no kernel, no cycle.
from . import picture_catalog

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only


# ─── Bind to DictionaryApp class as ─────────────────────────────────
#   api_picture_quiz_start   = _quiz.api_quiz_start
#   api_picture_quiz_answer  = _quiz.api_quiz_answer
#   api_picture_quiz_finish  = _quiz.api_quiz_finish
#   _session_path    = _quiz._session_path
#   _prune_sessions  = _quiz._prune_sessions
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

OPTIONS_PER_ROUND = 4
MAX_SESSION_AGE_S = 24 * 3600


def _scope_key(item: dict, pack: str) -> str:
    """The qualified category key for this item *inside the pack being quizzed*.

    An object belongs to many packs — ``carrot`` is ``food:vegetables`` and
    ``kitchen:on-the-counter``. Reading ``item["category"]`` gives the object's
    PRIMARY pack, which is not necessarily the one the learner opened, so a
    kitchen quiz would draw its decoys from Food. Falls back to the primary only
    when no pack is in play (an all-packs quiz).
    """
    if pack and pack in (item.get("categories") or {}):
        return f"{pack}:{item['categories'][pack]}"
    return item.get("category_key", "")


def build_rounds(
    items: dict,
    by_category: dict,
    *,
    category: str = "",
    pack: str = "",
    length: int = 10,
    seed: str = "",
    progress: dict | None = None,
    with_image: set[str] | None = None,
    by_pack: dict | None = None,
) -> list[dict]:
    """Build quiz rounds. Pure, deterministic for a given ``seed``.

    Rules, in order of importance:

    1. Distractors come from the **answer's own category, within the pack being
       quizzed** — a leopard's decoys should be other big cats, otherwise the
       quiz tests category recognition rather than the name. Topped up from the
       quizzed pack only when the category cannot supply three; never from the
       whole catalog, which would put a whale beside a bus.
    2. Up to two distractors are drawn from the learner's own recorded
       confusions for that animal, so the quiz sharpens the distinction it has
       actually seen them get wrong.
    3. No slug is the answer twice in one session.
    """
    progress = progress or {}
    rng = random.Random(seed or "picture-dict")

    by_pack = by_pack or {}
    # The universe every decoy is drawn from: one group, one pack, or everything.
    if category:
        universe = list(by_category.get(category, []))
    elif pack:
        universe = list(by_pack.get(pack, []))
    else:
        universe = [s for s in items]
    pool = universe
    if with_image is not None:
        withimg = [s for s in pool if s in with_image]
        # Only demand photos if enough of them exist to fill a round; a fresh
        # install with nothing downloaded must still be able to quiz on names.
        if len(withimg) >= OPTIONS_PER_ROUND:
            pool = withimg
    if len(pool) < OPTIONS_PER_ROUND:
        return []

    answers = pool[:]
    rng.shuffle(answers)
    answers = answers[: max(1, int(length or 10))]

    rounds = []
    for idx, ans in enumerate(answers):
        item = items[ans]
        key = category or _scope_key(item, pack)
        same_cat = [s for s in by_category.get(key, []) if s != ans]

        picks: list[str] = []
        confusions = (progress.get(ans, {}) or {}).get("confusions", {}) or {}
        for slug, _n in sorted(confusions.items(), key=lambda kv: -kv[1]):
            if len(picks) >= 2:
                break
            # `in universe`, not `in items`: a confusion recorded while quizzing
            # Animals must not inject a mammal into a Transport round.
            if slug in universe and slug != ans:
                picks.append(slug)

        rest = [s for s in same_cat if s not in picks]
        rng.shuffle(rest)
        picks.extend(rest[: OPTIONS_PER_ROUND - 1 - len(picks)])

        if len(picks) < OPTIONS_PER_ROUND - 1:
            spare = [s for s in universe if s != ans and s not in picks]
            rng.shuffle(spare)
            picks.extend(spare[: OPTIONS_PER_ROUND - 1 - len(picks)])

        options = picks[: OPTIONS_PER_ROUND - 1] + [ans]
        rng.shuffle(options)
        rounds.append({"index": idx, "answer": ans, "options": options})
    return rounds


def public_round(rnd: dict, items: dict, image_url) -> dict:
    """The round as the browser may see it — **without the answer**."""
    return {
        "index": rnd["index"],
        "answer_image": image_url(rnd["answer"]),
        "answer_emoji": items[rnd["answer"]]["emoji"],
        "options": [
            {
                "slug": s,
                "name": items[s]["name"],
                "chinese": items[s]["chinese"],
                "emoji": items[s]["emoji"],
                "image": image_url(s),
            }
            for s in rnd["options"]
        ],
    }


def _session_path(self, sid: str):
    seg = safe_path_segment(sid)
    return self.data_subdir("sessions") / f"{seg}.json"


def _prune_sessions(self) -> None:
    """Drop session files older than a day. Cheap, and keeps a long-running
    daemon from accumulating one file per abandoned quiz."""
    now = time.time()
    try:
        for f in self.data_subdir("sessions").glob("*.json"):
            try:
                if now - f.stat().st_mtime > MAX_SESSION_AGE_S:
                    f.unlink(missing_ok=True)
            except OSError:
                continue
    except Exception:
        pass


@web_route("POST", "/api/picture/quiz/start")
async def api_picture_quiz_start(self, request):
    body = await request.json() if await request.body() else {}
    category = str(body.get("category") or "").strip()
    try:
        length = int(body.get("length") or self.setting_or_config("picture-dict.quiz_length", 10))
    except (TypeError, ValueError):
        length = 10
    length = max(1, min(50, length))
    mode = str(body.get("mode") or self.setting_or_config("picture-dict.quiz_mode", "photo-to-name"))

    pack = str(body.get("pack") or "").strip()
    # Accepts "animals:sea" and the bare "sea" a pre-split session carries.
    key = picture_catalog.resolve_category(self.by_category, category)
    if category and not key:
        return {"error": f"unknown category '{category}'"}
    if pack and pack not in self.by_pack:
        return {"error": f"unknown pack '{pack}'"}

    self._prune_sessions()
    sid = f"q{int(time.time() * 1000):x}{random.randint(0x100, 0xfff):x}"
    rounds = build_rounds(
        self.items,
        self.by_category,
        category=key,
        pack=pack,
        length=length,
        seed=sid,
        progress=self.load_picture_progress(),
        with_image=self.slugs_with_image(),
        by_pack=self.by_pack,
    )
    if not rounds:
        return {"error": "not enough pictures in that group to build a quiz"}

    # Store the RESOLVED key, so a session outlives the bare-name compat path.
    self._session_path(sid).write_text(
        json.dumps({"sid": sid, "mode": mode, "category": key, "pack": pack,
                    "rounds": rounds, "answered": {}, "created": time.time()}),
        encoding="utf-8",
    )
    return {
        "session": sid,
        "mode": mode,
        "total": len(rounds),
        "rounds": [public_round(r, self.items, self.image_url) for r in rounds],
    }


@web_route("POST", "/api/picture/quiz/answer")
async def api_picture_quiz_answer(self, request):
    body = await request.json() if await request.body() else {}
    sid = str(body.get("session") or "")
    choice = str(body.get("choice") or "")
    try:
        index = int(body.get("round"))
    except (TypeError, ValueError):
        return {"error": "round must be a number"}

    path = self._session_path(sid)
    if not path.exists():
        return {"error": "that quiz has expired — start a new one"}
    sess = json.loads(path.read_text(encoding="utf-8"))
    rnd = next((r for r in sess["rounds"] if r["index"] == index), None)
    if rnd is None:
        return {"error": "no such round"}

    answer = rnd["answer"]
    correct = choice == answer
    sess["answered"][str(index)] = {"choice": choice, "correct": correct}
    path.write_text(json.dumps(sess), encoding="utf-8")

    await self.picture_record_answer(answer, correct=correct, chose=choice)

    return {
        "correct": correct,
        "answer": answer,
        "answer_name": self.items[answer]["name"],
        "answer_chinese": self.items[answer]["chinese"],
        "answer_hint": self.items[answer].get("hint", ""),
        "score": sum(1 for a in sess["answered"].values() if a["correct"]),
        "answered": len(sess["answered"]),
        "total": len(sess["rounds"]),
    }


@web_route("POST", "/api/picture/quiz/finish")
async def api_picture_quiz_finish(self, request):
    body = await request.json() if await request.body() else {}
    sid = str(body.get("session") or "")
    path = self._session_path(sid)
    if not path.exists():
        return {"error": "that quiz has expired"}
    sess = json.loads(path.read_text(encoding="utf-8"))

    missed = []
    for r in sess["rounds"]:
        a = sess["answered"].get(str(r["index"]))
        if a and not a["correct"]:
            it = self.items.get(r["answer"], {})
            missed.append({"slug": r["answer"], "name": it.get("name", ""),
                           "chinese": it.get("chinese", ""), "emoji": it.get("emoji", ""),
                           "image": self.image_url(r["answer"])})

    score = sum(1 for a in sess["answered"].values() if a["correct"])
    total = len(sess["rounds"])
    path.unlink(missing_ok=True)

    # Emit outside any lock so handlers can recurse through call_app freely.
    self.spawn_background(
        self.emit("dictionary:picture_quiz_completed",
                  {"score": score, "total": total, "category": sess.get("category", "")}),
        label="dictionary picture quiz emit",
    )
    return {"score": score, "total": total, "missed": missed,
            "enrolled": [m["slug"] for m in missed]}
