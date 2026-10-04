"""soundcheck — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (shapes, engine, sessions, summary,
bank_loader, panels) can import these directly without cycling through the
spine ``.app`` module.

Pure functions only — no ``self``, no kernel access, no I/O.
"""

from __future__ import annotations

# ── Dimensions ────────────────────────────────────────────────────
# Six axes of English pronunciation. ``eye_playable`` is load-bearing rather
# than descriptive: when no voice engine is reachable the session planner keeps
# only these, which is what leaves a real game instead of a dead screen.

DIMENSIONS: list[dict] = [
    {"id": "vowel", "label": "Vowel sounds", "icon": "◐",
     "blurb": "The sixteen vowels of General American, and the pairs that blur.",
     "eye_playable": False},
    {"id": "consonant", "label": "Consonants", "icon": "◆",
     "blurb": "θ/ð, l/r, v/w, and the endings that go quiet.",
     "eye_playable": False},
    {"id": "syllable", "label": "Syllable shape", "icon": "▣",
     "blurb": "Dropped endings, simplified clusters, extra vowels.",
     "eye_playable": True},
    {"id": "stress", "label": "Word stress", "icon": "▲",
     "blurb": "Which beat carries the word. REcord or reCORD.",
     "eye_playable": True},
    {"id": "connected", "label": "Connected speech", "icon": "~",
     "blurb": "Linking, tapping, and the words that melt together.",
     "eye_playable": False},
    {"id": "spelling", "label": "Spelling to sound", "icon": "Aa",
     "blurb": "One spelling, several sounds. ⟨ea⟩ is three of them.",
     "eye_playable": True},
]

DIMENSION_IDS: tuple[str, ...] = tuple(d["id"] for d in DIMENSIONS)
DIMENSION_INDEX: dict[str, dict] = {d["id"]: d for d in DIMENSIONS}

EYE_PLAYABLE: frozenset[str] = frozenset(
    d["id"] for d in DIMENSIONS if d["eye_playable"]
)

# ── Shapes ────────────────────────────────────────────────────────
# The round forms. Declared here as ids only; their specs live in shapes.py so
# that engine.py can stay ignorant of every one of them.

SHAPE_IDS: tuple[str, ...] = (
    "minimal_pair",
    "sort",
    "odd_one_out",
    "stress",
    "count",
    "presence",
    "produce",
)

MODALITIES: tuple[str, ...] = ("ear", "eye")

# The answer key for "nothing here differs". Its whole job is to make
# answer-by-elimination fail, so it is a real option id, never a UI flag.
SAME_KEY = "same"

# ── Session defaults ──────────────────────────────────────────────

QUICK_LENGTH = 12          # a 2-4 minute drill
DIAGNOSTIC_LENGTH = 40     # one dimension swept
CALIBRATION_LENGTH = 20    # the fixed first-session placement set

DEFAULT_LIVES = 3          # 0 means unlimited — practice, not a run
DEFAULT_DEADLINE_MS = 12000

# Heat is the one adaptive dial: 0-4 per contrast, driving distractor count,
# whether the "same" option appears, clip speed and the deadline.
HEAT_MIN, HEAT_MAX = 0, 4
HEAT_DEFAULT = 1           # not 0 — round one should not be insulting
HEAT_UP = 1                # correct and fast
HEAT_DOWN = -2             # asymmetric, so a single miss re-drills

# Fraction of odd-one-out items that legitimately have no odd item. Enforced by
# the generator and asserted by a unit test — without it, elimination beats
# listening and the shape stops measuring anything.
SAME_RATE = 0.20

# How far apart the eye and ear twins of one stem must sit inside a session,
# so the second pass is a perception test rather than a memory test.
TWIN_GAP = 6

STREAK_CELEBRATE = 5

# ── Selection weights ─────────────────────────────────────────────
# See the plan: score = ErrorPull + DueBoost + CoverageGap + fit − recency.

W_ERROR_PULL = 0.40
W_DUE = 0.25
W_COVERAGE = 0.20
W_DIFFICULTY_FIT = 0.15
W_RECENT = 0.30

# Perception evidence is deliberately worth less than production evidence.
# Recognising a sound and producing it are different skills, and weighting them
# equally is how the selector starts confirming its own priors.
PROD_WEIGHT = 1.0
PHONE_WEIGHT = 0.5
PERCEPTION_WEIGHT = 0.35

RECENCY_HALFLIFE_DAYS = 30.0

# Anti-repeat: a contrast may not appear in the last N consecutive slots, nor
# more than M times in the last K. A filter, never a penalty term — a penalty
# can be out-voted by a large error weight, which is how you get twenty
# identical rounds in a row.
NO_REPEAT_SLOTS = 2
MAX_IN_WINDOW = 4
REPEAT_WINDOW = 10

SOFTMAX_TOP_N = 12
SOFTMAX_TEMPERATURE = 0.7

# Aim for roughly this accuracy: high enough to stay worth doing, low enough
# that something is being learned.
TARGET_ACCURACY = 0.80

# ── Write-back ────────────────────────────────────────────────────

SOURCE_TAG = "soundcheck"

# Per-session cap on logged events per phone. Without it, selecting on weak
# phones and then writing errors back to weak phones is a loop that amplifies
# its own starting point.
MAX_EVENTS_PER_PHONE = 3

# How sure a miss is about which sound was actually confused. A two-option
# minimal pair is unambiguous; a five-option odd-one-out is much less so.
SHAPE_CONFIDENCE: dict[str, float] = {
    "minimal_pair": 1.0,
    "sort": 0.9,
    "odd_one_out": 0.6,
    "stress": 0.8,
    "count": 0.5,
    "presence": 0.5,
    "produce": 0.0,     # self-report — never evidence about a produced sound
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def slug(text: str) -> str:
    """Lowercase, alphanumerics and hyphens only — for building item ids."""
    out = []
    for ch in (text or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def pct(part: float, whole: float) -> int:
    return int(round(100 * part / whole)) if whole else 0
