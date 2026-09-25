"""App Analytics — productive/neutral/personal classification of apps.

Closes the gap "no productive/distracting classification per app": every
time-tracking competitor (RescueTime, Rize, Timing) leads with a
productivity split, and EmptyOS already had the per-app usage counters to
compute one but no notion of what any app is *for*.

Pure stdlib on purpose — no `emptyos` import, no `self`, no I/O — so the
whole classification + roll-up is unit-testable without booting a daemon.
The app binds these from `app.py`; the state read/write and the manifest
walk stay there.

Three classes, and the vocabulary is deliberate:

    productive   work: engineering, code, writing, planning, study
    neutral      upkeep with no work/leisure valence: settings, store, hub
    personal     life outside work: garden, vlog, countdown, weather

"distracting" (RescueTime's third bucket) is rejected. Every app here was
installed or written by the user on purpose; labelling one "distracting"
in their own system is a judgement the data cannot support, and the
question the user actually asks is "how much of today was work?".

Precedence, highest first:

  1. a user override  — the user reclassifies an app from the UI. This is
     the feature the competitors actually sell; inference is only the
     starting point. Overrides live in app state (``data/``), never in a
     manifest, because a manifest is shared community code and the
     classification is per-machine taste (CLAUDE.md rule 15).
  2. an explicit manifest key ``[app] productivity = "..."`` — optional,
     read defensively; no app declares it today and none has to.
  3. inference from ``store_category``, refined by ``dimensions``.

On the wellbeing wheel (CLAUDE.md rule 16): ``dimensions`` is read here as
a *silent* refiner and never reaches a surface. The output vocabulary is
the three classes above — no dimension name, icon, or wheel is exposed,
and none may be added downstream. The wheel shapes the answer; it is not
the answer.
"""

from __future__ import annotations

CLASSES: tuple[str, ...] = ("productive", "neutral", "personal")

DEFAULT_CLASS = "neutral"

LABELS: dict[str, str] = {
    "productive": "Productive",
    "neutral": "Neutral",
    "personal": "Personal",
}

# ── Inference, tier A: the category decides on its own ────────────────────
# These four say what the app is for plainly enough that a declared
# dimension should not be able to overturn them.
DECISIVE_CATEGORY: dict[str, str] = {
    "engineering": "productive",
    "dev": "productive",
    "productivity": "productive",
    "personal": "personal",
}

# ── Inference, tier B: ambiguous categories, refined by dimensions ────────
# `core` holds both real work (task, note, search) and pure chrome
# (settings, store, hub); `meta` and `ai` are similar. The category only
# supplies the fallback when an app declares no dimensions at all.
AMBIGUOUS_CATEGORY_DEFAULT: dict[str, str] = {
    "core": "neutral",
    "meta": "neutral",
    "ai": "productive",
    "creative": "personal",
    "other": "neutral",
}

# Wellbeing dimensions -> lean. Internal only (see module docstring).
# `intellectual` leans productive: reading and study are work here.
DIMENSION_LEAN: dict[str, str] = {
    "occupational": "productive",
    "intellectual": "productive",
    "financial": "productive",
    "physical": "personal",
    "social": "personal",
    "emotional": "personal",
    "spiritual": "personal",
    "environmental": "personal",
}


def normalize_class(value) -> str | None:
    """Coerce a caller-supplied class to a known one, else ``None``.

    The write-boundary guard for the override endpoint: anything not in
    ``CLASSES`` is refused rather than stored, so a typo can never become
    a fourth silent bucket that the roll-up then has to tolerate.
    ``None``/""/"auto" all read as "clear the override".
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    return s if s in CLASSES else None


def infer_class(category, dimensions) -> str:
    """Classify from manifest metadata alone. Never raises.

    `category` is ``[app] store_category``; `dimensions` is
    ``[app] dimensions`` (a list, possibly absent). Tier A categories win
    outright; tier B ones are refined by any declared dimension, with a
    productive lean beating a personal one when an app declares both
    (a work tool that also touches wellbeing is still a work tool).
    """
    cat = str(category or "").strip().lower()
    if cat in DECISIVE_CATEGORY:
        return DECISIVE_CATEGORY[cat]

    leans = set()
    if isinstance(dimensions, (list, tuple, set)):
        for d in dimensions:
            lean = DIMENSION_LEAN.get(str(d or "").strip().lower())
            if lean:
                leans.add(lean)
    if "productive" in leans:
        return "productive"
    if "personal" in leans:
        return "personal"
    return AMBIGUOUS_CATEGORY_DEFAULT.get(cat, DEFAULT_CLASS)


def classify_app(app_block, override=None) -> tuple[str, str]:
    """Classify one app from its raw ``[app]`` manifest block.

    Returns ``(class, source)`` where source is ``override`` /
    ``manifest`` / ``inferred`` — the UI shows it so the user can tell a
    guess from a decision they made, and knows which rows are worth
    correcting.
    """
    ov = normalize_class(override)
    if ov:
        return ov, "override"
    block = app_block if isinstance(app_block, dict) else {}
    declared = normalize_class(block.get("productivity"))
    if declared:
        return declared, "manifest"
    return infer_class(block.get("store_category"), block.get("dimensions")), "inferred"


def classify_all(app_blocks, overrides=None) -> dict[str, dict]:
    """Classify every app. ``app_blocks`` maps app_id -> raw ``[app]`` dict."""
    overrides = overrides or {}
    out: dict[str, dict] = {}
    for app_id, block in (app_blocks or {}).items():
        cls, source = classify_app(block, overrides.get(app_id))
        out[app_id] = {"class": cls, "source": source}
    return out


def summarize_usage(rows, classes, *, default: str = DEFAULT_CLASS) -> dict:
    """Roll daily usage rows up into a productive-vs-other split.

    `rows` are ``TimeSeriesCounter.range()`` rows — dicts carrying
    ``bucket``, ``app`` and ``count``. `classes` is the map from
    ``classify_all``. An app with no entry (a retired app still in the
    counter, a plugin id, ``_unknown``) falls to `default` rather than
    being dropped: silently discarding usage would make the percentages
    lie, and a total that does not match the views elsewhere on the page
    reads as a bug.

    ``productive_pct`` is a share of *classified activity*, so it is 0
    when nothing was used — never a division by zero.
    """
    totals = dict.fromkeys(CLASSES, 0)
    per_day: dict[str, dict[str, int]] = {}
    per_app: dict[str, int] = {}

    for row in rows or []:
        try:
            count = int(row.get("count") or 0)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        app_id = str(row.get("app") or "")
        bucket = str(row.get("bucket") or "")
        cls = (classes.get(app_id) or {}).get("class")
        if cls not in CLASSES:
            cls = default if default in CLASSES else DEFAULT_CLASS
        totals[cls] += count
        if bucket:
            day = per_day.setdefault(bucket, dict.fromkeys(CLASSES, 0))
            day[cls] += count
        if app_id:
            per_app[app_id] = per_app.get(app_id, 0) + count

    total = sum(totals.values())
    apps = [
        {
            "app": app_id,
            "views": views,
            "class": (classes.get(app_id) or {}).get("class", default),
            "source": (classes.get(app_id) or {}).get("source", "inferred"),
        }
        for app_id, views in per_app.items()
    ]
    apps.sort(key=lambda r: (-r["views"], r["app"]))

    return {
        "totals": totals,
        "total": total,
        "productive_pct": round(totals["productive"] / total * 100, 1) if total else 0.0,
        "personal_pct": round(totals["personal"] / total * 100, 1) if total else 0.0,
        "daily": [
            {"date": day, **counts} for day, counts in sorted(per_day.items())
        ],
        "apps": apps,
    }
