"""soundcheck — the round loop: draw, build, grade, tally, adapt.

Pure, stdlib-only. Owns: session construction, the draw→build step, grading,
the running tally, and the heat dial. Source of truth for *how a session
progresses*.

**This module must never mention a shape name.** Every shape-specific decision
lives in ``shapes.py`` behind ``build_round``, and every item-specific decision
lives in ``bank_loader.py`` behind ``select_next``. If a branch on
``shape == "..."`` ever appears here, the abstraction has failed and the fix
belongs in the table, not in this file. ``tests/test_unit_soundcheck_engine.py``
asserts this by reading the source.

No ``self``, no I/O — ``sessions.py`` owns persistence, locking and HTTP, and
calls in here for every decision. Keeping the loop pure is what lets the whole
game be tested without a daemon, a browser, or a voice engine.

## Grading is set equality, deliberately

A verdict is ``picked == answer`` as sets. Not "contains", not "close enough",
not per-option partial credit. Partial credit on a perception test invents a
middle ground that does not exist — you either heard the contrast or you did
not — and it would make the confusion matrix uninterpretable, since a cell
would no longer mean "chose this wrong bucket".
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from . import shared
from .bank_loader import events_for_answer, select_next
from .shapes import BuildContext, build_round, shapes_for


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Session construction ──────────────────────────────────────────

def new_session(
    sid: str,
    *,
    mode: str = "quick",
    dimensions: tuple[str, ...] = (),
    length: int = 0,
    lives: int = shared.DEFAULT_LIVES,
    modality: str = "ear",
    audio_ok: bool = True,
    deadline_ms: int = shared.DEFAULT_DEADLINE_MS,
    pass_of: str | None = None,
    item_order: list[str] | None = None,
    seed: int | None = None,
) -> dict:
    """Build a session record. ``mode`` picks the default length only.

    ``audio_ok`` is resolved by the caller *before* the first round is served,
    never discovered mid-session. A session that starts silent stays silent and
    plans around it; discovering it at round seven would mean rewriting a plan
    the learner is already halfway through.

    ``item_order`` freezes which items are served and in what sequence, which is
    what makes the eye/ear comparison a comparison: pass B must ask the same
    questions as pass A, or the disagreement between them measures the sampling
    rather than the learner. When empty, items are selected adaptively per round.
    """
    default_length = {
        "quick": shared.QUICK_LENGTH,
        "diagnostic": shared.DIAGNOSTIC_LENGTH,
        "calibration": shared.CALIBRATION_LENGTH,
    }.get(mode, shared.QUICK_LENGTH)

    if mode in ("diagnostic", "calibration"):
        lives = 0

    playable = shapes_for(audio_ok=audio_ok, modality=modality)
    dims = tuple(dimensions) or _dimensions_for(audio_ok)

    return {
        "sid": sid,
        "started": _now(),
        "mode": mode,
        "plan": {
            "dimensions": list(dims),
            "shapes": playable,
            "length": int(length or default_length),
            "lives": int(lives),
            "modality": modality,
            "audio_ok": bool(audio_ok),
            "deadline_ms": int(deadline_ms),
        },
        "pass_of": pass_of,
        "item_order": list(item_order or []),
        "seed": seed if seed is not None else random.randrange(1 << 30),
        "rounds": [],
        "tally": {"asked": 0, "right": 0, "streak": 0, "best_streak": 0,
                  "lives_left": int(lives)},
        "target_difficulty": 2.0,
        "heat_updates": {},
        "completed": False,
        "completed_at": "",
        "abandoned": False,
    }


def _dimensions_for(audio_ok: bool) -> tuple[str, ...]:
    """With no voice engine, keep only what genuinely plays by eye.

    Not a degraded mode so much as a smaller one: three of the six dimensions
    never needed audio, so a silent install still gets a real game rather than a
    row of dead play buttons.
    """
    if audio_ok:
        return tuple(shared.DIMENSION_IDS)
    return tuple(d for d in shared.DIMENSION_IDS if d in shared.EYE_PLAYABLE)


# ── The draw ──────────────────────────────────────────────────────

def next_round(
    session: dict,
    bank: dict,
    *,
    weak: dict | None = None,
    pairs: dict | None = None,
    perception: dict | None = None,
    srs: dict | None = None,
    heat: dict | None = None,
    audio: dict[str, str] | None = None,
    forced_item: dict | None = None,
) -> dict | None:
    """Select an item, build its round, record it, return the page payload.

    Mutates ``session`` — it appends the round record carrying the answer. The
    returned payload is what may cross to the browser and never contains it.
    """
    if is_done(session):
        return None

    plan = session["plan"]
    rid = len(session["rounds"]) + 1
    rng = random.Random(session["seed"] + rid)

    item = forced_item or _from_order(session, bank, rid) or select_next(
        bank,
        weak=weak, pairs=pairs, perception=perception, srs=srs,
        recent_contrasts=[r.get("contrast_id", "") for r in session["rounds"]],
        recent_stems=tuple(r.get("stem_id", "") for r in session["rounds"]),
        seen_items={r.get("item_id", "") for r in session["rounds"]},
        dimensions=tuple(plan["dimensions"]),
        shapes=tuple(plan["shapes"]),
        target_difficulty=session.get("target_difficulty", 2.0),
        rng=rng,
    )
    if item is None:
        return None

    cid = item.get("contrast_id", "")
    ctx = BuildContext(
        rid=rid,
        modality=plan["modality"] if plan["modality"] in ("ear", "eye") else "ear",
        heat=int((heat or {}).get(cid, shared.HEAT_DEFAULT)),
        deadline_ms=plan["deadline_ms"],
        audio=audio or {},
        rng=rng,
    )
    built = build_round(item, ctx)

    session["rounds"].append({
        "rid": rid,
        "item_id": item.get("id", ""),
        "contrast_id": cid,
        "stem_id": item.get("stem_id", ""),
        "dimension": item.get("dimension", ""),
        "shape": item.get("shape", ""),
        "modality": ctx.modality,
        "heat": ctx.heat,
        "word": (item.get("words") or [{}])[0].get("text", ""),
        "answer": built.answer,
        "expected": _label_for(item, built.answer),
        "picked": None,
        "correct": None,
        "timeout": False,
        "elapsed_ms": 0,
        "replays": 0,
        "ts": _now(),
    })
    return built.payload


def _from_order(session: dict, bank: dict, rid: int) -> dict | None:
    """The next item from a frozen order, if this session has one.

    Returns ``None`` when the session is adaptive or the order has run out, so
    the caller falls through to selection. An id that is no longer in the bank
    is skipped rather than ending the pass — a regenerated bank must not strand
    a second pass halfway through.
    """
    order = session.get("item_order") or []
    if not order:
        return None
    by_id = bank.get("by_id") or {}
    for candidate in order[rid - 1:]:
        item = by_id.get(candidate)
        if item is not None:
            return item
    return None


def _label_for(item: dict, keys: list[str]) -> str:
    """The human-readable name of an answer — the confusion matrix axis label."""
    by_id = {o.get("id"): o for o in (item.get("options") or [])}
    labels = [str(by_id.get(k, {}).get("label") or k) for k in keys]
    return " + ".join(labels)


# ── Grading ───────────────────────────────────────────────────────

def grade(
    session: dict,
    rid: int,
    picked: list[str],
    *,
    item: dict | None = None,
    elapsed_ms: int = 0,
    replays: int = 0,
    timeout: bool = False,
) -> dict:
    """Score one answer, update the tally and heat, return the verdict.

    ``item`` is the bank row, needed only to project a miss into telemetry
    events and to label the picked bucket. Absent, the round still grades — the
    verdict never depends on it.
    """
    row = _round(session, rid)
    if row is None:
        return {"error": "unknown round"}
    if row.get("picked") is not None:
        return {"error": "already answered"}

    chosen = [] if timeout else [p for p in picked if p]
    correct = (not timeout) and set(chosen) == set(row["answer"])

    row["picked"] = chosen
    row["correct"] = correct
    row["timeout"] = bool(timeout)
    row["elapsed_ms"] = int(elapsed_ms)
    row["replays"] = int(replays)
    if item is not None:
        row["picked_label"] = _label_for(item, chosen)

    _tally(session, correct)
    fast = bool(elapsed_ms) and elapsed_ms < row_deadline(session, row) * 0.5
    new_heat = next_heat(row["heat"], correct=correct, fast=fast)
    session["heat_updates"][row["contrast_id"]] = new_heat
    _retarget(session)

    events = events_for_answer(item, chosen, correct=correct) if item else []

    return {
        "rid": rid,
        "correct": correct,
        "picked": chosen,
        "answer": row["answer"],
        "timeout": bool(timeout),
        "explain": (item or {}).get("explain", ""),
        "accent": (item or {}).get("accent"),
        "contrast": row["contrast_id"],
        "heat": new_heat,
        "tally": dict(session["tally"]),
        "events": events,
        "done": is_done(session),
        "celebrate": _celebration(session, correct),
    }


def row_deadline(session: dict, row: dict) -> int:
    return int(session["plan"].get("deadline_ms") or shared.DEFAULT_DEADLINE_MS)


def _round(session: dict, rid: int) -> dict | None:
    for row in session.get("rounds", []):
        if row.get("rid") == rid:
            return row
    return None


def _tally(session: dict, correct: bool) -> None:
    t = session["tally"]
    t["asked"] += 1
    if correct:
        t["right"] += 1
        t["streak"] += 1
        t["best_streak"] = max(t["best_streak"], t["streak"])
    else:
        t["streak"] = 0
        if t["lives_left"] > 0:
            t["lives_left"] -= 1


def _retarget(session: dict) -> None:
    """Nudge the difficulty target toward roughly 80% accuracy.

    Reads only the last five answers, so the aim tracks the session the learner
    is actually having rather than an average dragged around by its opening.
    """
    recent = [r for r in session["rounds"] if r.get("correct") is not None][-5:]
    if len(recent) < 5:
        return
    hit = sum(1 for r in recent if r["correct"]) / len(recent)
    target = session.get("target_difficulty", 2.0)
    if hit > 0.85:
        target += 0.25
    elif hit < 0.60:
        target -= 0.25
    session["target_difficulty"] = shared.clamp(target, 1.0, 5.0)


def next_heat(heat: int, *, correct: bool, fast: bool) -> int:
    """The one adaptive dial.

    Asymmetric on purpose: up one for a clean answer, down two for a miss. A
    symmetric dial lets a contrast the learner keeps failing drift back up to
    hard between misses, so the drill never settles where the difficulty is.
    """
    step = shared.HEAT_UP if (correct and fast) else 0 if correct else shared.HEAT_DOWN
    return int(shared.clamp(heat + step, shared.HEAT_MIN, shared.HEAT_MAX))


def _celebration(session: dict, correct: bool) -> str:
    """Three moments, and only the third is earned rather than participation."""
    t = session["tally"]
    if not correct:
        return ""
    if t["asked"] >= session["plan"]["length"] and t["right"] == t["asked"]:
        return "perfect"
    if t["streak"] and t["streak"] % shared.STREAK_CELEBRATE == 0:
        return "streak"
    return ""


# ── End conditions ────────────────────────────────────────────────

def is_done(session: dict) -> bool:
    if session.get("completed") or session.get("abandoned"):
        return True
    t = session["tally"]
    if t["asked"] >= session["plan"]["length"]:
        return True
    order = session.get("item_order") or []
    if order and t["asked"] >= len(order):
        return True
    lives = session["plan"].get("lives") or 0
    return bool(lives) and t["lives_left"] <= 0


def complete(session: dict) -> dict:
    """Idempotent close."""
    if not session.get("completed"):
        session["completed"] = True
        session["completed_at"] = _now()
    return session


def abandon(session: dict) -> dict:
    """A partial session must not touch the streak — it was never finished."""
    session["abandoned"] = True
    session["completed_at"] = _now()
    return session


def answered(session: dict) -> list[dict]:
    return [r for r in session.get("rounds", []) if r.get("correct") is not None]


def progress(session: dict) -> dict:
    t = session["tally"]
    length = session["plan"]["length"] or 1
    return {
        "asked": t["asked"],
        "length": session["plan"]["length"],
        "right": t["right"],
        "accuracy": round(t["right"] / t["asked"], 3) if t["asked"] else 0.0,
        "streak": t["streak"],
        "best_streak": t["best_streak"],
        "lives_left": t["lives_left"],
        "percent": shared.pct(t["asked"], length),
    }
