"""soundcheck — what a finished session actually tells you.

Pure, stdlib-only. Owns: the confusion matrix, the worst-contrast ranking, the
eye-versus-ear comparison, and the one-sentence headline. Source of truth for
*what the learner reads at the end*.

No ``self``, no I/O — recomputed from the session's rounds every time rather
than accumulated as the session runs. Recomputable means repairable: a bug in
this file is fixed by deploying a fix, not by discarding everyone's history.

## The matrix is the point

A score says you got 12 of 20. A confusion matrix says *which wrong bucket you
fell into* — that FLEECE came out as KIT four times and never the reverse. The
first number is a grade; the second is a lesson, and it is the computed form of
the sorting grid a paper worksheet asks you to fill in by hand.

**Timeouts are excluded from the matrix.** A round that ran out of clock has no
picked bucket, so scoring it as a confusion would invent evidence about a
decision the learner never made. It still counts against the tally — it was
still a miss — it just cannot say *what* was confused.
"""

from __future__ import annotations

from . import shared


def _graded(session: dict) -> list[dict]:
    return [r for r in session.get("rounds", []) if r.get("correct") is not None]


# ── The confusion matrix ──────────────────────────────────────────

def confusion_matrix(session: dict) -> dict:
    """Cells keyed ``"expected|picked"``. The diagonal is correct.

    Only rounds that produced a real choice appear. Rounds whose expected or
    picked label is missing are skipped rather than bucketed under an empty
    string, which would render as a mysterious unnamed row.
    """
    cells: dict[str, int] = {}
    labels: list[str] = []
    seen: set[str] = set()

    for row in _graded(session):
        if row.get("timeout"):
            continue
        expected = (row.get("expected") or "").strip()
        picked = (row.get("picked_label") or "").strip()
        if not expected or not picked:
            continue
        for label in (expected, picked):
            if label not in seen:
                seen.add(label)
                labels.append(label)
        key = f"{expected}|{picked}"
        cells[key] = cells.get(key, 0) + 1

    return {
        "labels": sorted(labels),
        "cells": cells,
        "total": sum(cells.values()),
        "worst": worst_cells(cells),
    }


def worst_cells(cells: dict[str, int], limit: int = 3) -> list[dict]:
    """Off-diagonal cells, ranked by how often that specific swap happened.

    Ranked by count and then by rate within the expected row, so a confusion
    seen twice out of two attempts outranks one seen twice out of nine.
    """
    per_expected: dict[str, int] = {}
    for key, n in cells.items():
        expected = key.split("|", 1)[0]
        per_expected[expected] = per_expected.get(expected, 0) + n

    out = []
    for key, n in cells.items():
        expected, _, picked = key.partition("|")
        if expected == picked:
            continue
        total = per_expected.get(expected, 0) or 1
        out.append({"expected": expected, "picked": picked, "n": n,
                    "rate": round(n / total, 3)})

    out.sort(key=lambda c: (-c["n"], -c["rate"]))
    return out[:limit]


# ── Worst contrasts ───────────────────────────────────────────────

def contrast_scores(session: dict) -> list[dict]:
    """Per-contrast accuracy for this session, weakest first."""
    stats: dict[str, dict] = {}
    for row in _graded(session):
        cid = row.get("contrast_id") or ""
        if not cid:
            continue
        s = stats.setdefault(cid, {"contrast": cid, "asked": 0, "right": 0,
                                   "dimension": row.get("dimension", "")})
        s["asked"] += 1
        if row.get("correct"):
            s["right"] += 1

    out = list(stats.values())
    for s in out:
        s["accuracy"] = round(s["right"] / s["asked"], 3) if s["asked"] else 0.0
    out.sort(key=lambda s: (s["accuracy"], -s["asked"]))
    return out


def cleared_contrasts(session: dict, previous_worst: list[str]) -> list[str]:
    """Contrasts that were on the last worst-list and came back perfect.

    The only celebration in this app that is earned rather than granted for
    turning up, so it is computed against real prior evidence — not against
    "did well today".
    """
    if not previous_worst:
        return []
    lookup = {s["contrast"]: s for s in contrast_scores(session)}
    return [
        cid for cid in previous_worst
        if cid in lookup and lookup[cid]["asked"] >= 2
        and lookup[cid]["accuracy"] >= 1.0
    ]


# ── Eye versus ear ────────────────────────────────────────────────

def eye_ear_split(session: dict, other: dict | None = None) -> dict:
    """Where the spelling instinct and the ear disagree.

    Twins are matched on ``stem_id``, which is why the generator emits eye and
    ear variants of one item sharing that key. Works within a single session or
    across a linked pair of them — the dual-pass diagnostic runs the same frozen
    item order twice, and passing ``other`` compares the two.

    The asymmetry matters more than the agreement rate. Right-by-eye and
    wrong-by-ear means the spelling is carrying a word the ears cannot yet hold.
    The reverse means the sound is known and the spelling is lying about it,
    which is the crosswalk's territory rather than this app's.
    """
    by_stem: dict[str, dict[str, dict]] = {}
    for src in (session, other):
        if not src:
            continue
        for row in _graded(src):
            stem = row.get("stem_id")
            if not stem or row.get("timeout"):
                continue
            by_stem.setdefault(stem, {})[row.get("modality", "ear")] = row

    eye_right_ear_wrong: list[dict] = []
    ear_right_eye_wrong: list[dict] = []
    both_wrong: list[dict] = []
    agree = 0
    compared = 0

    for stem, pair in sorted(by_stem.items()):
        eye, ear = pair.get("eye"), pair.get("ear")
        if not eye or not ear:
            continue
        compared += 1
        record = {"stem": stem, "word": _word_of(eye) or _word_of(ear),
                  "contrast": ear.get("contrast_id", "")}
        if eye["correct"] == ear["correct"]:
            agree += 1
            if not eye["correct"]:
                both_wrong.append(record)
        elif eye["correct"]:
            eye_right_ear_wrong.append({**record,
                                        "expected": ear.get("expected", ""),
                                        "picked": ear.get("picked_label", "")})
        else:
            ear_right_eye_wrong.append({**record,
                                        "expected": eye.get("expected", ""),
                                        "picked": eye.get("picked_label", "")})

    return {
        "compared": compared,
        "agreement": round(agree / compared, 3) if compared else 0.0,
        "eye_right_ear_wrong": eye_right_ear_wrong,
        "ear_right_eye_wrong": ear_right_eye_wrong,
        "both_wrong": both_wrong,
        "headline": _eye_ear_headline(eye_right_ear_wrong, ear_right_eye_wrong),
    }


def _word_of(row: dict) -> str:
    """The word this round was about — never the item id.

    An id in a learner-facing sentence ("you missed gs-oo-foot-sort") is worse
    than saying nothing, so the fallback is empty and the renderer omits it.
    """
    return str(row.get("word") or "")


def _eye_ear_headline(eye_right: list[dict], ear_right: list[dict]) -> str:
    """One sentence, or nothing. A summary that always speaks stops being read."""
    if not eye_right and not ear_right:
        return ""
    parts = []
    if eye_right:
        words = ", ".join(w["word"] for w in eye_right[:3] if w["word"])
        parts.append(
            f"{len(eye_right)} word{'' if len(eye_right) == 1 else 's'} you read "
            f"right and heard wrong" + (f" — {words}" if words else "")
        )
    if ear_right:
        words = ", ".join(w["word"] for w in ear_right[:3] if w["word"])
        parts.append(
            f"{len(ear_right)} the other way round" + (f" — {words}" if words else "")
        )
    lead = "Your spelling instinct is covering for your ears: " if eye_right else ""
    return (lead + "; and ".join(parts) + ".").strip()


# ── The whole summary ─────────────────────────────────────────────

def summarise(session: dict, *, other: dict | None = None,
              previous_worst: list[str] | None = None) -> dict:
    """Everything the end-of-session view needs, in one recomputable object."""
    graded = _graded(session)
    right = sum(1 for r in graded if r.get("correct"))
    timeouts = sum(1 for r in graded if r.get("timeout"))
    matrix = confusion_matrix(session)
    contrasts = contrast_scores(session)
    split = eye_ear_split(session, other)

    order = session.get("item_order") or []
    return {
        "sid": session.get("sid", ""),
        "mode": session.get("mode", ""),
        "pass_of": session.get("pass_of"),
        # A first pass with a frozen order can be run again by ear. The second
        # pass is where the diagnostic actually lives — one pass alone only says
        # how you did, never where eye and ear disagree.
        "dual_ready": bool(order) and not session.get("pass_of"),
        "started": session.get("started", ""),
        "completed_at": session.get("completed_at", ""),
        "abandoned": bool(session.get("abandoned")),
        "asked": len(graded),
        "right": right,
        "accuracy": round(right / len(graded), 3) if graded else 0.0,
        "timeouts": timeouts,
        "best_streak": session.get("tally", {}).get("best_streak", 0),
        "matrix": matrix,
        "contrasts": contrasts,
        "worst": [c["contrast"] for c in contrasts if c["accuracy"] < 1.0][:3],
        "cleared": cleared_contrasts(session, previous_worst or []),
        "eye_ear": split,
        "headline": _headline(session, graded, right, matrix, split),
    }


def _headline(session: dict, graded: list[dict], right: int,
              matrix: dict, split: dict) -> str:
    """Lead with whichever finding is most actionable, not with the score."""
    if not graded:
        return ""
    if split["headline"]:
        return split["headline"]
    worst = matrix.get("worst") or []
    if worst:
        top = worst[0]
        return (
            f"{top['expected']} came out as {top['picked']} "
            f"{top['n']}× — that swap is the one to work on."
        )
    if right == len(graded):
        return f"Clean sweep — {right} of {len(graded)}."
    return f"{right} of {len(graded)}, and no single sound stood out."


def heat_summary(heat_updates: dict[str, int]) -> dict:
    """Which contrasts got harder and which got easier this session."""
    up = [c for c, h in heat_updates.items() if h >= shared.HEAT_MAX]
    down = [c for c, h in heat_updates.items() if h <= shared.HEAT_MIN]
    return {"maxed": sorted(up), "bottomed": sorted(down)}
