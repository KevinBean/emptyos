"""Wellbeing dimensions — canonical 8-axis lens applied across apps.

Dimension is a tag, not a table. Apps add an optional `dimension` field
(habit schema, capture frontmatter, task #tags) and aggregate via this module.
"""

from __future__ import annotations

import re

DIMENSIONS: tuple[str, ...] = (
    "physical",
    "social",
    "intellectual",
    "emotional",
    "spiritual",
    "environmental",
    "financial",
    "occupational",
)

ICONS: dict[str, str] = {
    "physical": "🏃",
    "social": "👥",
    "intellectual": "📚",
    "emotional": "❤️",
    "spiritual": "🕯️",
    "environmental": "🏠",
    "financial": "💰",
    "occupational": "💼",
}

LABELS: dict[str, str] = {
    "physical": "Physical",
    "social": "Social",
    "intellectual": "Intellectual",
    "emotional": "Emotional",
    "spiritual": "Spiritual",
    "environmental": "Environmental",
    "financial": "Financial",
    "occupational": "Occupational",
}

TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "physical": (
        "body",
        "exercise",
        "workout",
        "zumba",
        "health",
        "fitness",
        "run",
        "ran",
        "running",
        "walk",
        "walked",
        "walking",
        "yoga",
        "sleep",
        "slept",
        "gym",
        "swim",
        "swam",
        "hike",
        "hiked",
        "bike",
        "biked",
        "stretch",
        "cardio",
        "strength",
        "rest",
        "tired",
        "bondi",
    ),
    "social": (
        "friends",
        "family",
        "people",
        "church",
        "connect",
        "call",
        "called",
        "chat",
        "chatted",
        "dinner",
        "lunch",
        "meeting",
        "hangout",
        "party",
        "birthday",
        "mom",
        "dad",
        "brother",
        "sister",
        "wife",
        "husband",
        "daughter",
        "son",
        "coffee",
        "visit",
        "visited",
    ),
    "intellectual": (
        "learn",
        "learned",
        "learning",
        "english",
        "study",
        "studied",
        "read",
        "reading",
        "book",
        "lesson",
        "shadow",
        "shadowing",
        "article",
        "podcast",
        "course",
        "language",
        "practice",
        "practiced",
        "vocabulary",
        "knowledge",
    ),
    "emotional": (
        "feel",
        "felt",
        "feeling",
        "mood",
        "healing",
        "heal",
        "cry",
        "cried",
        "anger",
        "angry",
        "grief",
        "sad",
        "happy",
        "anxious",
        "anxiety",
        "lonely",
        "love",
        "afraid",
        "overwhelmed",
        "triggered",
        "insight",
    ),
    "spiritual": (
        "faith",
        "meditate",
        "meditation",
        "meditated",
        "nature",
        "reflect",
        "reflected",
        "pray",
        "prayed",
        "sabbath",
        "gratitude",
        "grateful",
        "quiet",
        "stillness",
        "purpose",
    ),
    "environmental": (
        "home",
        "place",
        "space",
        "room",
        "clean",
        "cleaned",
        "declutter",
        "decluttered",
        "organize",
        "organized",
        "house",
        "garden",
        "tidy",
        "moved",
    ),
    "financial": (
        "money",
        "finance",
        "expense",
        "spent",
        "bought",
        "net",
        "buy",
        "sell",
        "sold",
        "invest",
        "invested",
        "budget",
        "save",
        "saved",
        "income",
        "bank",
        "transfer",
        "bill",
    ),
    "occupational": (
        "work",
        "job",
        "career",
        "dev",
        "build",
        "built",
        "code",
        "coded",
        "ship",
        "shipped",
        "project",
        "deadline",
        "launch",
        "launched",
        "deploy",
        "deployed",
        "boss",
        "client",
        "task",
        "meeting",
    ),
}

_ALIAS_INDEX: dict[str, str] = {d: d for d in DIMENSIONS}
for _dim, _aliases in TAG_ALIASES.items():
    for _a in _aliases:
        _ALIAS_INDEX[_a.lower()] = _dim

_TAG_RE = re.compile(r"#([A-Za-z][A-Za-z0-9_-]*)")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")


def resolve(tag: str) -> str | None:
    """Map a tag (with or without leading #) to a canonical dimension, or None."""
    if not tag:
        return None
    t = tag.lstrip("#").strip().lower()
    return _ALIAS_INDEX.get(t)


def extract(text: str) -> list[str]:
    """Return unique canonical dimensions mentioned as #tags in text, in first-seen order."""
    if not text:
        return []
    seen: list[str] = []
    for m in _TAG_RE.finditer(text):
        d = resolve(m.group(1))
        if d and d not in seen:
            seen.append(d)
    return seen


def empty_counts() -> dict[str, int]:
    """Zero-initialized {dimension: 0} for aggregation."""
    return {d: 0 for d in DIMENSIONS}


def scan_text(text: str) -> dict[str, int]:
    """Count dimension mentions in natural-language prose (no #tags needed).

    Passive inference: matches alias vocabulary on word boundaries. Lower
    signal per hit than an explicit #tag, but catches "went for a run this
    morning" without requiring the user to type #physical. Use for behavioral
    coverage readings, not for routing decisions (false positives possible).
    """
    counts = empty_counts()
    if not text:
        return counts
    for m in _WORD_RE.finditer(text.lower()):
        d = _ALIAS_INDEX.get(m.group(0))
        if d:
            counts[d] += 1
    return counts


def infer(source_app: str = "", text: str = "", manifest_map: dict | None = None) -> list[str]:
    """Infer dimensions without user tagging.

    Precedence:
      1. If source_app has declared `dimensions` in its manifest → use those.
      2. Else fall back to inline #tag extraction from text.
      3. Else empty list (caller decides how to handle unclassified).

    `manifest_map` is {app_id: manifest_dict_or_obj}. Pass the kernel's
    AppLoader.manifests or an equivalent dict keyed by app id.
    """
    if source_app and manifest_map:
        entry = manifest_map.get(source_app)
        if entry is not None:
            declared: list = []
            if isinstance(entry, dict):
                app_block = entry.get("app") or entry.get("data", {}).get("app") or {}
                declared = list(app_block.get("dimensions") or [])
            else:
                app_block = getattr(entry, "app", None) or {}
                declared = list(
                    (
                        app_block.get("dimensions")
                        if isinstance(app_block, dict)
                        else getattr(app_block, "dimensions", None)
                    )
                    or []
                )
            canon = [d for d in declared if d in DIMENSIONS]
            if canon:
                return canon
    return extract(text) if text else []


def balance_score(signals: dict[str, int]) -> dict:
    """Given {dimension: signal_count}, return a balance reading."""
    counts = {d: int(signals.get(d, 0)) for d in DIMENSIONS}
    values = list(counts.values())
    total = sum(values)
    mean = total / len(DIMENSIONS) if total else 0
    thin = [d for d, v in counts.items() if v < mean and mean > 0]
    dominant = [d for d, v in counts.items() if mean > 0 and v > 2 * mean]
    non_zero = [v for v in values if v > 0]
    ratio = max(values) / max(1, min(values)) if len(non_zero) == len(DIMENSIONS) else None
    if ratio is None:
        grade = "red"
    elif ratio < 3:
        grade = "green"
    elif ratio < 6:
        grade = "yellow"
    else:
        grade = "red"
    return {
        "counts": counts,
        "total": total,
        "mean": mean,
        "thin": thin,
        "dominant": dominant,
        "ratio": ratio,
        "grade": grade,
    }


def icon(dim: str) -> str:
    return ICONS.get(dim, "·")


def label(dim: str) -> str:
    return LABELS.get(dim, dim.title())
