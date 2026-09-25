"""Shared utilities for EmptyOS apps."""

import json
import math
import numbers
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Re-exported, not defined here. These lived in this module and in
# `runtime/vault_index.py` as near-verbatim copies, and drifted three separate
# ways before anyone compared them; the single implementation is top-level so
# both the SDK and the runtime can reach it without an import cycle. Kept
# exported from here because 184 call sites import them from `emptyos.sdk`.
from emptyos.frontmatter import (  # noqa: F401
    fm_end,
    parse_frontmatter,
    set_frontmatter_field,
    strip_frontmatter,
)


def load_json(path: Path, default: Any) -> Any:
    """Read JSON from ``path``; return ``default`` if the file doesn't exist.

    Use for app data files that may be absent on first run. Caller passes the
    shape-appropriate default (``[]`` for list-backed stores, ``{}`` for dict).
    Reads as UTF-8.

    Not for: streaming parses, fallback-on-corruption (let bad JSON raise —
    that's a bug, not a normal state).
    """
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data: Any, *, indent: int = 2, mkdir: bool = False) -> None:
    """Write ``data`` as UTF-8 JSON to ``path``.

    ``default=str`` so datetime/Path values serialise without crashing.
    ``ensure_ascii=False`` preserves non-ASCII characters readably (e.g. note
    titles in other scripts) instead of escaping to ``\\uXXXX``.

    Pass ``mkdir=True`` to ensure the parent directory exists — useful for
    sidecar JSON files in vault folders that may not yet be present on first
    run. Idempotent on existing dirs.
    """
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=indent, default=str, ensure_ascii=False),
        encoding="utf-8",
    )


def ensure_column(db, table: str, column: str, decl: str) -> bool:
    """Add a column to an existing SQLite table if it's missing (ADD COLUMN).

    The in-place migration shape for app-owned ``data/`` databases whose
    schema grows after deployment — PRAGMA-check first so it's idempotent.
    Returns True iff the column was added. Caller owns the commit. Pure
    sqlite3, kernel-free. Consumers: billing (``cached_tokens``), assistant
    sessions (``project_id``).
    """
    cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column in cols:
        return False
    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


def json_safe(obj: Any) -> Any:
    """Replace non-finite floats (inf/-inf/nan) with None recursively, so a value
    that legitimately uses ``inf`` (a straight cable route's binding radius, an empty
    run's separation) survives JSON serialisation at the HTTP boundary instead of
    raising ``ValueError: Out of range float values``. Sanitise at the boundary, not
    by weakening the Python-side contract. Used by the CAD object-type
    generate/compute endpoints (corridor / overhead-line / earthing)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return obj


def contribution_id(contrib: dict) -> str:
    """The id a `[[contributes.<target>.<slot>]]` entry resolves to.

    One definition because a host that *filters* contributions by id must agree
    exactly with the id it later *emits* them under. Hub carried two copies of
    this expression and grew a third when single-panel refresh learned to narrow
    the list before calling anything; a copy that drifted would silently match
    nothing and report "not found" for a panel that plainly exists.

    Mirrors the `_app_id` stamped by ``AppLoader.get_contributions``.
    """
    return contrib.get("id") or f"{contrib.get('_app_id')}:{contrib.get('method')}"


def new_id(prefix: str, n: int = 8) -> str:
    """Generate a short opaque ID like ``"out-3a7c91f2"``.

    Used across apps for ephemeral record IDs (learnings, outreach, stories,
    canvas cards, etc.). The prefix carries the type; the suffix carries
    enough entropy for collision avoidance within a single store.
    """
    return f"{prefix}-{uuid.uuid4().hex[:n]}"


_TRUTHY_STRINGS = ("1", "true", "yes", "on")


def is_truthy(value: Any) -> bool:
    """Read a config value or a stored flag as on/off.

    A string is on only when it says so ("1", "true", "yes", "on", any case),
    so ``"false"`` — a TOML typo or an env override — reads as off rather than
    as a non-empty string. A number (bool, int, float, Decimal, numpy
    scalars) is on when non-zero, negatives included; NaN is off. Anything
    else (None, a list, a dict, a date) is off.

    Not for a switch whose OFF side exposes something: that reader must fail
    closed and treat an unrecognised value as ON.
    """
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    if isinstance(value, numbers.Number):
        return value == value and bool(value)      # NaN != NaN
    return False


# --- Task parsing constants ---

TASK_RE = re.compile(
    r"- \[([ xX])\] (.+?)(?:\s*📅\s*(\d{4}-\d{2}-\d{2}))?(?:\s*✅\s*(\d{4}-\d{2}-\d{2}))?\s*$"
)
DUE_PATTERN = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
# Fallback inline syntax: `due:YYYY-MM-DD` (real users type this even though
# 📅 is canonical). Only used when no 📅 marker is present.
DUE_INLINE_PATTERN = re.compile(r"\bdue:\s*(\d{4}-\d{2}-\d{2})")
DONE_PATTERN = re.compile(r"✅\s*(\d{4}-\d{2}-\d{2})")


def extract_due(text: str) -> str:
    """Return the due date from a task line: prefers 📅 marker, falls back
    to inline ``due:YYYY-MM-DD``. Empty string if neither matches."""
    m = DUE_PATTERN.search(text or "")
    if m:
        return m.group(1)
    m = DUE_INLINE_PATTERN.search(text or "")
    return m.group(1) if m else ""


def normalize_relative_date(s: str) -> str:
    """ISO date or relative tokens (today/tonight/tomorrow) → ISO date string.
    Empty string when unparseable. Shared by voice-intent due parsers
    (task.voice_add_task, reminders.voice_add_reminder)."""
    s = (s or "").strip().lower()
    if not s:
        return ""
    if s in ("today", "tonight"):
        return date.today().isoformat()
    if s == "tomorrow":
        return (date.today() + timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except (ValueError, TypeError):
        return ""


_CLOCK_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$")


def normalize_clock_time(s: str) -> str:
    """Accept '9', '9am', '9:30 pm', '17:00' → 'HH:MM' (24h). Empty string
    when unparseable. Shared by voice-intent time parsers
    (reminders.voice_add_reminder)."""
    s = (s or "").strip().lower().replace(" ", "")
    if not s:
        return ""
    m = _CLOCK_TIME_RE.match(s)
    if not m:
        return ""
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"
# Room pointer on a task line: 🗨️ <room-id>. Captures the id (kebab-case
# slug of letters, digits, dashes, underscores). One room per task; first
# match wins. Followed by a word boundary so "abc-12345" stops cleanly
# without eating trailing markdown.
ROOM_PATTERN = re.compile(r"🗨️\s*([A-Za-z0-9_\-]+)")

CAPTURE_LINE_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — (.+?)(?:\s+#(\S+))?$")


def parse_captures(content: str, limit: int | None = None) -> list[dict]:
    """Parse the shared captures markdown file into entries (newest first).

    Shared between the capture app (owner) and readers that need to aggregate
    capture data without taking a call_app edge on it (e.g. journal dimension
    signals). The parse format is the canonical capture line: see CAPTURE_LINE_RE.
    """
    entries = []
    for line in (content or "").split("\n"):
        m = CAPTURE_LINE_RE.match(line.strip())
        if m:
            entries.append(
                {
                    "timestamp": m.group(1),
                    "text": m.group(2).strip(),
                    "tag": m.group(3) or "",
                }
            )
    entries.reverse()
    return entries[:limit] if limit else entries


_TIER_THRESHOLDS = [(90, "zombie"), (30, "stale"), (7, "aging")]


def task_tier(days_overdue: int) -> str:
    """Classify task staleness: fresh / aging / stale / zombie."""
    for threshold, tier in _TIER_THRESHOLDS:
        if days_overdue > threshold:
            return tier
    return "fresh"


def compute_task_decay(due_str: str, today: date) -> tuple[int, str]:
    """Compute overdue days and tier from a due date string.

    Returns (overdue_days, tier). overdue_days is 0 if not overdue or invalid.
    """
    if not due_str:
        return 0, "fresh"
    try:
        overdue = (today - date.fromisoformat(due_str[:10])).days
        if overdue < 0:
            overdue = 0
        return overdue, task_tier(overdue)
    except (ValueError, TypeError):
        return 0, "fresh"


def ebbinghaus_decay(age_days: float, half_life_days: float = 30.0) -> float:
    """Recency weight on [0, 1] following an Ebbinghaus forgetting curve.

    ``0.5 ** (age_days / half_life_days)``: 1.0 at age 0, 0.5 at one
    half-life, asymptotic to 0. Future/negative ages clamp to 1.0.

    Used to blend recency into recall ranking (``BaseApp.recall``). Borrowed
    from moeru-ai/airi's memory scorer (30-day half-life default).
    """
    if half_life_days <= 0 or age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


_MOOD_SALIENCE = {"great": 10.0, "good": 5.0, "okay": 0.0, "low": -5.0, "bad": -10.0}


def salience_from_mood(mood: str | None) -> float:
    """Map a journal mood label to a salience score on [-10, 10].

    great=+10 … okay=0 … bad=-10. Unknown / empty → 0 (neutral). Recentres
    journal's ``MOOD_SCORE`` (1..5) onto the airi emotion range used by the
    salience term in ``BaseApp.recall``.
    """
    return _MOOD_SALIENCE.get((mood or "").strip().lower(), 0.0)


def iso_age_days(iso_str: str, now: datetime | None = None) -> float | None:
    """Age in days of an ISO timestamp/date string, or None if unparseable.

    Tolerates ``YYYY-MM-DD``, full ISO with offset, and a trailing ``Z``.
    Naive timestamps are treated as UTC. Future dates yield a negative age
    so callers decide how to clamp (``ebbinghaus_decay`` clamps to 1.0).
    """
    s = (iso_str or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = None
    for candidate in (s, s[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (ref - dt).total_seconds() / 86400.0


def is_past_ttl(
    born_iso: str,
    ttl_seconds: float,
    now: datetime | str | None = None,
) -> bool:
    """Has `born_iso` aged past `ttl_seconds`?

    The "birth timestamp + TTL" expiry shape, extracted on its second consumer
    (`emptyos/context/store.py` originals, `plugins/telegram/bridge.py` Apply
    cards). Built on `iso_age_days`, so both inherit its tolerance: date-only
    strings, a trailing `Z`, and **naive timestamps treated as UTC** — that last
    one matters, because comparing a naive stored `ts` against an aware `now`
    raises TypeError, which a local try/except quietly turns into "never
    expires". Several writers in this repo emit naive `datetime.now().isoformat()`.

    Two deliberate fail-open cases, both returning False:
      * `ttl_seconds <= 0` — the caller's way of disabling expiry.
      * an unparseable timestamp — refusing a real item is worse than allowing
        one, since expiry is a convenience gate and the real guard is downstream.

    Boundary is `>=`: at exactly the TTL, treat it as expired. NOT for absolute
    deadlines — an `expires_at` field is a plain comparison, not TTL arithmetic
    (see `autopilot._is_expired`), and folding the two would be one function
    pretending to be two.
    """
    if ttl_seconds <= 0:
        return False
    if isinstance(now, str):
        age_now = iso_age_days(now)
        # A now-string we can't parse means we have no reference clock; fall
        # back to the real one rather than guessing.
        now = None if age_now is None else datetime.fromisoformat(
            now.strip().replace("Z", "+00:00")
        )
    age_days = iso_age_days(born_iso, now)
    if age_days is None:
        return False
    return age_days * 86400.0 >= float(ttl_seconds)


def _balanced_json_spans(text: str) -> list[tuple[int, int]]:
    """Every balanced `{...}` / `[...]` span in `text`, outermost and nested alike.

    String-aware: a brace or bracket inside a JSON string literal is content, not
    structure, so `{"a": "has } brace"}` yields one span rather than a truncated
    one. Escapes are honoured, so `"he said \\"hi\\""` does not end the string early.

    Unbalanced input degrades instead of corrupting: a stray closer with nothing
    open is skipped (the previous depth counter went negative here and silently
    discarded every later structure), and a mismatched pair abandons the open
    stack rather than pairing `{` with `]`.
    """
    spans: list[tuple[int, int]] = []
    stack: list[tuple[str, int]] = []
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append((ch, i))
        elif ch in "}]":
            if not stack:
                continue
            open_ch, start = stack.pop()
            if (open_ch == "{") != (ch == "}"):
                stack.clear()
                continue
            spans.append((start, i + 1))
    return spans


def parse_llm_json(text: str, fallback: dict | list | None = None) -> dict | list:
    """Extract JSON from LLM output — handles markdown fences, preamble, trailing prose.

    Supports both JSON objects ({}) and arrays ([]).

    Resolution order: whole-text parse, then a fenced block, then the **largest**
    balanced structure in the text (ties broken by position, earliest first).

    Largest — not first — is what makes this return the model's answer rather than
    a fragment of it. Any nested structure is by construction shorter than the one
    containing it, so an array of objects resolves to the array; and a stray valid
    token in the prose ("per the notes [1], here it is: {...}") loses to the real
    payload that follows it. The predecessor scanned for `{...}` everywhere before
    trying `[...]` at all, which meant an unfenced array with any preamble silently
    returned its FIRST ELEMENT as a dict — a caller asking for five picks got one.
    Found 2026-07-31 while grading a model-bench response; the docstring claimed
    "first" while the code did "objects, then arrays", and neither was right.

    Consequence worth knowing: where a response contains two sibling structures,
    the bigger wins rather than the earlier one. That case is ambiguous by nature
    and no caller can depend on it — a model asked for one JSON value that emits
    two has already failed the instruction.

    Args:
        text: Raw LLM response that may contain JSON wrapped in markdown code fences,
              surrounded by explanatory text, or with other formatting.
        fallback: Default value to return if parsing fails. If None, raises ValueError.

    Returns:
        Parsed dict or list from the JSON content.
    """
    text = text.strip()
    # Direct parse
    try:
        result = json.loads(text)
        if isinstance(result, (dict, list)):
            return result
    except json.JSONDecodeError:
        pass
    # Markdown code fence
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(1).strip())
            if isinstance(result, (dict, list)):
                return result
        except json.JSONDecodeError:
            pass
    # Largest balanced structure wins, earliest breaks ties. Nested spans are
    # kept as fallbacks: when the outermost one fails to parse (a trailing comma,
    # a truncated tail) a complete inner structure is still better than nothing.
    head = text[:10000]  # Cap at 10KB
    for start, end in sorted(_balanced_json_spans(head), key=lambda s: (s[0] - s[1], s[0])):
        try:
            result = json.loads(head[start:end])
        except json.JSONDecodeError:
            continue
        if isinstance(result, (dict, list)):
            return result
    if fallback is not None:
        return fallback
    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}")


_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.S)


def parse_json_fence(text: str) -> dict:
    """Read the machine payload out of an explicit ```json fence. Never raises.

    The strict sibling of :func:`parse_llm_json`. That one hunts for JSON
    anywhere in a model's prose; this one reads a *committed note format* —
    a note whose body carries its machine data in one fenced ```json block, so
    the YAML frontmatter never has to hold JSON and never has to survive a
    quoting round-trip. Anything unusable (no body, no fence, malformed JSON,
    a top-level array) degrades to ``{}``: the caller is parsing a
    hand-editable vault note, where a broken fence must not crash the reader.

    Use for: reading back a note your own renderer wrote with a ```json block
    (replay recipes, operate manuals). Not for: LLM output, where the fence is
    optional and best-effort recovery is wanted — use :func:`parse_llm_json`.
    """
    if not text:
        return {}
    m = _JSON_FENCE_RE.search(text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


# A horizontal rule: 3+ of the same marker, optionally spaced (`---`, `***`,
# `___`, `* * *`). Matched before stripping because `strip_markdown`'s
# bold/italic rule reaches `***` first and leaves a bare `*` behind, which no
# longer looks like a rule to anything downstream. Deliberately does not match
# a bullet (`* item`) — the second character must be the same marker.
_HR_LINE_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")


def parse_llm_svg(raw: str) -> str:
    """Pull the outermost ``<svg>…</svg>`` out of an LLM reply, or "" if absent.

    Sibling of `parse_llm_json` / `parse_json_fence`: models wrap the artifact in
    fences and preamble, and the caller wants only the artifact. Spans the first
    ``<svg`` to the last ``</svg>`` so nested elements survive.

    NOT `sdk.html_artifact.extract_svg`, which is a different job: that one
    lifts an SVG out of a rendered HTML page, inlining the page CSS the shapes
    depend on and returning None when there is none. This one is a naive span
    over a model reply and returns "".
    """
    text = str(raw or "")
    lo, hi = text.find("<svg"), text.rfind("</svg>")
    if lo < 0 or hi < lo:
        return ""
    return text[lo:hi + len("</svg>")].strip()


def first_prose_line(text: str, *, allow_heading: bool = False) -> str:
    """First line of real prose in a markdown body, cleaned for human display.

    For the recurring "I need one short line to represent this document" job —
    a notification, a description fallback, a preview. Skips what is structure
    rather than prose: blank lines, code fences (and their contents),
    blockquotes, table rows, horizontal rules. Emphasis, links and bullet
    markers are stripped via :func:`strip_markdown`.

    Headings are skipped by default. A document's title ("Today's brief",
    "# Overview") usually restates context the caller already has, so a heading
    is rarely the line you want. ``allow_heading=True`` falls back to the first
    heading's text when there is no prose at all — better than an empty string
    for a caller that must say *something*.

    Returns ``""`` when nothing usable is found. Callers do their own
    truncation; the limit and whether an ellipsis is appended are display
    decisions this can't make for them.
    """
    heading = ""
    in_fence = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line or line.startswith((">", "|")) or _HR_LINE_RE.match(line):
            continue
        # Read `#` before stripping — strip_markdown removes the marker, and
        # after that a heading is indistinguishable from a sentence.
        is_heading = line.startswith("#")
        line = strip_markdown(line)
        if not line:
            continue
        if is_heading:
            heading = heading or line
            continue
        return line
    return heading if allow_heading else ""


def strip_markdown(text: str) -> str:
    """Strip markdown formatting for plain-text output (TTS, summaries).

    Removes: headings, bold/italic markers, links, images, code fences,
    horizontal rules, list bullets, blockquotes. Preserves the text content.
    """
    # Code fences
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Images
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # Links
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Headings
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold/italic
    text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.+?)_{1,3}", r"\1", text)
    # Strikethrough
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    # Horizontal rules
    text = re.sub(r"^[\-\*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Blockquotes
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    # List bullets
    text = re.sub(r"^[\-\*\+]\s+", "", text, flags=re.MULTILINE)
    # Numbered lists
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    # Table pipes
    text = re.sub(r"\|", " ", text)
    # Table separator rows
    text = re.sub(r"^[\s\-|:]+$", "", text, flags=re.MULTILINE)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fm_str(fm: dict, *keys: str, default: str = "") -> str:
    """Get a frontmatter value as a string.

    Handles the unpredictable types from ``parse_frontmatter``: values can be
    ``str``, ``list[str]``, ``int``, or ``None``.  Tries each *key* in order
    (to support mixed naming like ``last_contact`` / ``last-contact``).

    Lists are joined with ``", "``; ``None`` and empty lists fall back to
    *default*.
    """
    for key in keys:
        val = fm.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            return ", ".join(str(v) for v in val) if val else default
        s = str(val).strip()
        if s:
            return s
    return default


def fm_list(fm: dict, *keys: str) -> list[str]:
    """Get a frontmatter value as a list of strings.

    Scalar strings are split on ``", "``; lists are returned as-is (stringified).
    """
    for key in keys:
        val = fm.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            return [str(v) for v in val]
        s = str(val).strip()
        if s:
            return [v.strip() for v in s.split(",") if v.strip()]
    return []


def fm_scalar(value) -> str:
    """Encode one value as a frontmatter scalar :func:`parse_frontmatter` reads
    back unchanged.

    The write-side inverse of that function's unquoting, and the YAML encoding
    :func:`set_frontmatter_field` leaves to its caller. Emits the value bare
    when it is unambiguous, else DOUBLE-quoted with ``\\``→``\\\\`` and
    ``"``→``\\"``.

    Never the single-quote form. YAML escapes an apostrophe inside ``'...'`` by
    doubling it, and :func:`parse_frontmatter` does not un-double, so ``it's``
    would round-trip as ``it''s``. Double quotes are the form both sides agree
    on.

    Use for: short prose scalars (name, description, a status word). Not for:
    lists (write ``- item`` lines or an inline ``[a, b]``), values containing a
    newline (not escaped here — they break the block), or machine JSON, whose
    contract is a fenced ```json block in the body (:func:`parse_json_fence`).
    """
    sv = str(value)
    if sv == "":
        return '""'
    if any(c in sv for c in ":#{}[]|>&*?!,\"'\\") or sv != sv.strip():
        return '"' + sv.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return sv


def slugify(text: str, max_len: int | None = 60, *, fallback: str = "") -> str:
    """Convert text to a URL/filesystem-safe slug.

    ``max_len=None`` disables truncation (slugs used as stable note ids must
    not silently change when a title grows past the cap). ``fallback`` is
    returned when the input slugifies to nothing — pass the app's word
    ("course", "deck", "untitled") instead of re-rolling ``slugify(x) or w``
    with a local regex. Truncation applies before the fallback check, so a
    fallback word is never truncated.

    Not for: id slugs that must preserve embedded dash runs or need a unique
    suffix on empty input — use :func:`unique_slug`.
    """
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:max_len]
    return s or fallback


_UNICODE_SLUG_STRIP_RE = re.compile(r"[^\w\s-]")
_UNICODE_SLUG_GAP_RE = re.compile(r"[\s_]+")


def slugify_unicode(text: str, max_len: int | None = None, *, fallback: str = "") -> str:
    """Kebab-case slug that KEEPS non-ASCII word characters.

    Same shape as :func:`slugify` — strip punctuation, lowercase, collapse
    whitespace/underscore runs to ``-`` — but the character class is unicode
    ``\\w``, so CJK and accented letters survive::

        slugify("机器学习", fallback="untitled")          # "untitled"  (!)
        slugify_unicode("机器学习", fallback="untitled")  # "机器学习"
        slugify_unicode("Café au Lait")                   # "café-au-lait"

    That difference is the whole reason this exists. In a bilingual vault,
    routing a Chinese title through :func:`slugify` collapses it to nothing
    and every such note lands on the fallback word — so a note id built that
    way is not merely ugly, it is *not unique*. Reach for this whenever the
    slug is derived from a user-authored title that may not be English.

    Two deliberate differences from :func:`slugify`:

    - ``max_len`` defaults to ``None`` (no truncation). Every caller derives a
      stable note id, and an id must not change when its title grows past a
      cap. Pass an explicit cap only for display-side slugs.
    - Embedded dash runs are preserved (``"a -- b"`` → ``"a---b"``), because
      ``-`` is inside the kept class. :func:`slugify` collapses them.

    Truncation applies before the fallback check, so a fallback word is never
    truncated (matching :func:`slugify`).

    Not for:
        - ASCII-only URL slugs where non-ASCII *should* be dropped — use
          :func:`slugify`.
        - Human-readable vault *filenames* with collision suffixes — use
          :func:`safe_note_filename`, which keeps case, spaces and punctuation.
        - Ids needing a unique suffix on empty input — use :func:`unique_slug`.
    """
    # No .strip() on the input: the gap-collapse turns any leading/trailing
    # whitespace into a dash, which .strip("-") then removes. Verified
    # equivalent across unicode whitespace (\xa0, U+3000, \x1c-\x1e).
    s = _UNICODE_SLUG_STRIP_RE.sub("", str(text or "").lower())
    s = _UNICODE_SLUG_GAP_RE.sub("-", s).strip("-")[:max_len]
    return s or fallback


def slug_from_path(path: str) -> str:
    """Filename stem of a vault path: ``.../foo/bar-baz.md`` → ``bar-baz``.

    Drops the directory and a trailing ``.md``. Windows-path-safe — normalises
    ``\\`` to ``/`` before splitting, so a ``D:\\Vault\\note.md`` style path
    resolves the same as a POSIX one (which ``pathlib.Path(path).name`` does NOT
    on POSIX hosts).

    Use for: deriving a stable note id/slug from a vault-relative or absolute
    note path. Not for: slugifying free-text titles (use :func:`slugify`) or
    generating unique ids (use :func:`unique_slug`).
    """
    base = (path or "").replace("\\", "/").split("/")[-1]
    return base[:-3] if base.endswith(".md") else base


_UNIQUE_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def is_archived(fm: dict) -> bool:
    """Is this note's frontmatter marked ``archived`` (soft-deleted)?

    The one reading of the flag every project/study list and
    ``vault_project_create`` must share. Frontmatter round-trips values as
    strings, so ``archived: false`` arrives as the truthy string ``"false"`` —
    a plain ``if fm.get("archived")`` hides that live note from a list while
    create still treats it as live, leaving a name nobody can see or reuse.
    """
    v = fm.get("archived")
    if isinstance(v, str):
        return v.strip().lower() in {"true", "yes", "1"}
    return bool(v)


def unique_slug(text: str, *, prefix: str) -> str:
    """Slug suitable for stable ids — always non-empty.

    Preserves dashes already present in the input (unlike :func:`slugify`,
    which collapses any run of non-alphanumerics into a single dash). When
    the input has no slugifiable characters, returns
    ``f"{prefix}-{8-char hex}"`` so callers can store the result as an id
    without an ``or ...`` branch at every call site.

    Use when:
        - You need a stable id derived from user-supplied free text and
          "" is not an acceptable result.
        - You want to preserve embedded dashes (dates, hyphenated names).

    Not for:
        - Canonical URL slugs — prefer :func:`slugify`, which collapses
          dash runs.
        - Cryptographic randomness in the fallback — use
          :mod:`secrets` directly.
    """
    s = _UNIQUE_SLUG_RE.sub("-", (text or "").lower()).strip("-")
    if s:
        return s
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


_ILLEGAL_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_note_filename(folder, title: str, *, fallback_prefix: str) -> str:
    """Filesystem-safe ``<stem>.md`` filename from a free-text (possibly CJK)
    title, deduped against ``folder``.

    Unlike :func:`slugify`, KEEPS non-ASCII characters — vault filenames are
    human-readable and Chinese titles should stay Chinese. Strips only
    Windows-illegal characters; empty results fall back to
    ``f"{fallback_prefix}-<8 hex>"``; collisions get ``-2``, ``-3``… suffixes.

    First consumers: writing-editor articles + jianpu songs (rule-9 extraction,
    2026-06-10).
    """
    folder = Path(folder)
    stem = _ILLEGAL_FILENAME_RE.sub("", (title or "").strip()).strip(". ")[:60]
    if not stem:
        stem = f"{fallback_prefix}-{uuid.uuid4().hex[:8]}"
    name, n = stem, 2
    while (folder / f"{name}.md").exists():
        name = f"{stem}-{n}"
        n += 1
    return f"{name}.md"


_SAFE_PATH_SEG_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Windows resolves these as character devices, not files — and does so for any
# extension, so "nul.md" is the NUL device, not a note. A write silently
# succeeds and discards the data; a read returns nothing. Refused on every OS
# rather than under sys.platform, so a vault authored on Linux stays openable
# on Windows (CLAUDE.md rule 20 — no host-specific behaviour in runtime paths).
_WIN_RESERVED_STEMS = (
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def contained_path(base: Path, candidate: Path) -> Path | None:
    """``candidate`` if it stays inside ``base``, else None.

    The sibling of :func:`safe_path_segment` for the case where the caller
    legitimately supplies a *nested* path (``"<id>/floorplan.json"``) and only
    escape must be refused. The check has to be on the **resolved** path,
    because the join is what escapes — ``base / "../x"`` is an ordinary Path
    right up until you resolve it, and on Windows ``base / "..\\x"`` escapes
    too (a backslash is a separator there, and route parameters allow it).

    Returns the ORIGINAL candidate, not the resolved form: resolution can
    change case and expand short names on Windows, and callers compare and
    display these paths. Contained-ness is the only thing being decided here.
    """
    try:
        if candidate.resolve().is_relative_to(base.resolve()):
            return candidate
    except (OSError, ValueError):
        pass
    return None


def safe_path_segment(raw: str) -> str:
    """Return ``raw`` if it's a plain slug, else ``""`` — a path-traversal guard.

    For caller-supplied ids (HTTP path params, agent ``call_app`` args) that
    flow into vault filesystem paths. Restricts to ``[A-Za-z0-9._-]`` so ``/``
    and ``\\`` can't escape the intended directory, with dots allowed only in
    the interior and never doubled — so ``..``, ``../etc`` and ``.hidden``
    are still refused.

    The dot is permitted because real-world ids legitimately carry one:
    manufacturer cable ids use U0/U voltage notation (``nexans_6.35-11kv_…``),
    and a stricter rule refused 82 of 110 catalogue rows, 500-ing the whole
    browse endpoint (2026-07-20). Filenames may contain dots; only traversal
    is dangerous, so guard traversal rather than the character. Trailing dots
    are refused too — Windows silently strips them, so ``foo.`` and ``foo``
    would resolve to the same file — as are the Windows reserved device stems
    (see :data:`_WIN_RESERVED_STEMS`).

    Unlike :func:`slugify` / :func:`unique_slug` (which *transform* text into a
    slug), this **rejects** — it returns ``""`` for anything unsafe so callers
    fail loudly (or fall back to a contained default) rather than silently
    coercing ``"../etc"`` into ``"etc"``.
    """
    raw = (raw or "").strip()
    if not _SAFE_PATH_SEG_RE.match(raw):
        return ""
    if ".." in raw or raw.startswith(".") or raw.endswith("."):
        return ""
    if raw.split(".")[0].lower() in _WIN_RESERVED_STEMS:
        return ""
    return raw


def require_path_segment(raw: str, label: str = "id") -> str:
    """:func:`safe_path_segment`, but raises instead of returning ``""``.

    The raising form is what a *path builder* wants: it is the single
    choke-point every call site funnels through, so failing there means no
    caller can forget the guard. ``label`` names the thing for the message
    ("cable id", "run id", "scenario id").

    Extracted at the eighth consumer (2026-07-19 traversal audit). Beyond
    deduplication it makes the refusal message uniform — hand-written guards
    had drifted, some naming the allowed characters and some not, so the same
    class of mistake got a helpful error in one app and a bare one in another.

    Use :func:`safe_path_segment` directly when the caller wants a soft miss
    (a lookup that should return None rather than raise).
    """
    safe = safe_path_segment(raw)
    if not safe:
        raise ValueError(path_segment_error(raw, label))
    return safe


def path_segment_error(raw: str, label: str = "id") -> str | None:
    """The refusal message for an unsafe path segment, or None if it's fine.

    Same rule as :func:`require_path_segment`, but returned rather than
    raised — for route handlers that answer with an in-band
    ``{"error": ...}`` instead of letting an exception become a 500.

    Exists so the two shapes cannot drift: a user who types a bad id should
    get the same explanation whether the app guards at the route or at the
    path builder.
    """
    if safe_path_segment(raw):
        return None
    return (
        f"invalid {label} {raw!r} — must be letters, digits, "
        "underscore, hyphen or dot (no '..', no leading or trailing dot, "
        "not a reserved device name like 'nul')"
    )


def today_iso() -> str:
    """Local-timezone date today as ISO string (``"YYYY-MM-DD"``).

    For app data dated by calendar day — habit logs, daily journal entries,
    "what did I do today" summaries — where the user's local day boundary is
    what matters.

    Not for: timestamps (use ``now_iso()``); UTC date keys (call ``today_utc()``
    from ``time_series`` so the timezone intent is visible at the call site).
    """
    return date.today().isoformat()


def now_iso() -> str:
    """UTC timestamp ISO string with seconds precision.

    Canonical form for ``created`` / ``updated`` frontmatter on vault notes
    and similar machine-readable timestamps. Equivalent to
    ``datetime.now(timezone.utc).isoformat(timespec="seconds")``.

    For local-day strings use ``today_iso``; for date-keyed UTC counters use
    ``today_utc`` from ``time_series``.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clamp_days(value: Any, *, default: int = 1, lo: int = 1, hi: int = 90) -> int:
    """Coerce a loose day-count (query param str, kwarg int, None) into [lo, hi].

    Shared by the suite timeline contract (``timeline_items(days)`` +
    ``GET /api/timeline-items?days=N`` on each contributing app — see
    docs/suites/life-cohesion.md) so every member clamps identically and a
    hostile/typo'd ``days`` can never widen a scan window.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(n, hi))


def config_flag(config: Any, key: str, default: bool = False) -> bool:
    """Read a boolean feature flag from a kernel config, coercing loose shapes.

    TOML booleans arrive as ``bool``, but flags set via the settings store or
    hand-edited configs may arrive as strings (``"true"``, ``"1"``, ``"yes"``,
    ``"on"``). getattr-guarded so a minimal/fake config object (test fakes,
    stripped kernels) degrades to ``default`` rather than raising. Anything
    that isn't a bool or a truthy string reads as False.
    """
    get = getattr(config, "get", None)
    v = get(key, default) if callable(get) else default
    if isinstance(v, bool):
        return v
    return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "on")


def streak_from_dates(dates: set[str] | list[str], from_date: date | None = None) -> int:
    """Count consecutive days backward from today (or from_date) in a set of date strings.

    Used by healing, journal, meditation, reader, english apps for streak calculation.

    Args:
        dates: Set/list of ISO date strings (YYYY-MM-DD).
        from_date: Start counting back from this date. Defaults to today.

    Returns:
        Number of consecutive days with entries.
    """
    date_set = set(dates) if not isinstance(dates, set) else dates
    d = from_date or date.today()
    streak = 0
    while d.isoformat() in date_set:
        streak += 1
        d -= timedelta(days=1)
    return streak


def parse_data_url(data_url: str) -> tuple[str, bytes]:
    """Decode a data URL into (mime_type, raw_bytes).

    Used by apps consuming the browser-webcam see provider, which returns
    a base64 data URL like 'data:image/jpeg;base64,/9j/4AAQ...'. Apps that
    need the raw image bytes (to save, transcode, or pass to a vision
    model) call this.

    Raises ValueError if the input isn't a base64-encoded data URL.
    """
    import base64

    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        raise ValueError("not a data URL")
    try:
        header, payload = data_url.split(",", 1)
    except ValueError:
        raise ValueError("malformed data URL: missing comma separator") from None
    if ";base64" not in header:
        raise ValueError("only base64-encoded data URLs are supported")
    mime = header[len("data:") :].split(";", 1)[0] or "application/octet-stream"
    raw = base64.b64decode(payload)
    return mime, raw


def image_to_data_url(path, *, max_width: int = 1280, quality: int = 72) -> str | None:
    """Downscale + JPEG-encode an image file → a ``data:`` URL. None on any error.

    The inverse of :func:`parse_data_url`, and the encode step every consumer of
    the ``see`` capability needs: the ``webcam`` provider returns a *file path*
    while ``browser-webcam`` returns a *data URL*, so an app feeding a captured
    frame to ``think(images=[...])`` has to normalise to the URL form.

    Downscaling is the point, not a nicety — a raw camera frame is megabytes,
    and several of them in one vision call blow the request past what providers
    accept. Without PIL the original bytes are passed through, guarded by a 4 MB
    ceiling so a huge file degrades to None rather than to a failed request.

    Extracted 2026-08-16 from ``apps/public/labs/operate/shared.py`` at its
    second consumer (nutrition's photo meal log), per CLAUDE.md rule 9. The
    defaults are operate's, so its behaviour is unchanged.
    """
    import base64
    import io
    from pathlib import Path

    p = Path(path)
    try:
        from PIL import Image
    except Exception:
        try:
            raw = p.read_bytes()
        except OSError:
            return None
        if len(raw) > 4 * 1024 * 1024:
            return None
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    try:
        with Image.open(p) as im:
            im = im.convert("RGB")
            if im.width > max_width:
                h = int(im.height * max_width / im.width)
                im = im.resize((max_width, h), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=quality)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


# Wikilinks ------------------------------------------------------------------
# Canonical pattern for [[Note]] / [[Note|label]] / [[Note#section]] /
# [[Note#section|label]]. Group 1 is always the bare slug (no label, no anchor).
# Use this from any app that needs to scan note bodies — extracted because
# apps/link, apps/kb, and emptyos/sdk/markdown_render all had near-identical
# private regexes that drifted in subtle ways (anchor support, label group).
class FakeRequest:
    """Minimal Request shim for cross-app `call_app` invocations.

    `@web_route` handlers on the called app expect a Starlette-shaped request:
    `request.path_params["..."]` for path vars, `await request.json()` for the
    body, `request.query_params.get(...)` for query args. When invoked via
    `call_app` instead of an HTTP round-trip, callers construct one of these to
    satisfy that contract.

    Every argument is optional — pass only what the target handler reads.
    `query_params` defaults to an empty dict (a plain dict satisfies the
    `.get(...)` reads handlers do on Starlette's QueryParams).
    """

    def __init__(self, *, body: dict | None = None, path_params: dict | None = None,
                 query_params: dict | None = None):
        self.path_params = path_params or {}
        self.query_params = query_params or {}
        self._body = body or {}

    async def json(self):
        return self._body


WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def normalize_link_target(raw: str) -> str:
    """A wikilink target reduced to the form a note path can be compared against.

    Strips a ``#heading`` / ``^block`` reference, a trailing ``.md``, wrapping
    slashes and whitespace, and case. Backslashes fold to ``/`` so a link
    authored on Windows still matches a vault-relative path (CLAUDE.md rule 20).

    Shared because two apps resolve link targets — ``link`` (the index) and
    ``vault-graph`` (three sites) — and the copies had already diverged inside a
    day: only one stripped anchors. That one was correct by accident of its
    caller, since ``extract_wikilinks`` removes the anchor upstream; anything
    else feeding it a raw target would have silently failed to resolve.
    """
    t = str(raw or "").strip().replace("\\", "/")
    t = re.split(r"[#^]", t, 1)[0].strip().strip("/")
    if t.lower().endswith(".md"):
        t = t[:-3]
    return t.lower()


def note_stem_key(rel_path: str) -> str:
    """The bare filename of ``rel_path``, in the same comparable form."""
    return normalize_link_target(str(rel_path or "").replace("\\", "/").rsplit("/", 1)[-1])


def extract_wikilinks(text: str) -> set[str]:
    """Return the set of wikilink target slugs in ``text``.

    Strips ``#section`` anchors and ``|label`` aliases. Empty input → empty set.

    Ignores anything inside fenced or inline code: a ``[[Note]]`` written in a
    code sample is a literal the author typed, not a link, and every consumer
    here (kb graph, rooms, vault-graph, shadowing) wants it that way. Measured
    on the live vault before this landed — the three most-referenced link
    targets overall were ``${block.reference}``, ``" + block.reference +"`` and
    ``${refId}``, JavaScript template literals in fenced examples.
    """
    if not text:
        return set()
    from emptyos.sdk.markdown_render import strip_code

    return {
        m.group(1).strip()
        for m in WIKILINK_RE.finditer(strip_code(text))
        if m.group(1).strip()
    }


# Markdown tables ------------------------------------------------------------
# Pure transforms between pipe-shaped markdown tables, list-of-dicts, and CSV.
# Apps use these at the LLM boundary: feed a vault table to ``self.think()``
# as CSV (LLMs reason over tabular data more reliably as CSV than as a pipe
# table), parse the response back into rows, and write rows back as a pipe
# table that the vault and Obsidian both render natively. The vault stays
# plain markdown; CSV is a transient transport format, never the source of
# truth.


def _is_md_table_separator(line: str) -> bool:
    s = line.strip()
    if not s or "-" not in s:
        return False
    return all(c in "|-: \t" for c in s)


def _split_md_table_row(line: str) -> list[str]:
    """Split a pipe-table row into trimmed cells, tolerating optional outer pipes."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [cell.strip() for cell in s.split("|")]


def parse_markdown_table(text: str) -> list[dict]:
    """Parse the first pipe-shaped markdown table in ``text`` into rows of dicts.

    Returns an empty list when no table is found. Keys come from the header
    row in the order they appear; cells are stripped. The table ends at the
    first non-table line (blank, prose, new heading). Outer pipes are
    optional (GFM tolerates both ``| a | b |`` and ``a | b``).

    Multiple tables in one chunk are not supported — split the text first
    (e.g. by ``##`` section) and call once per chunk.
    """
    if not text:
        return []
    lines = text.splitlines()
    for i in range(len(lines) - 1):
        if "|" not in lines[i] or _is_md_table_separator(lines[i]):
            continue
        if not _is_md_table_separator(lines[i + 1]):
            continue
        headers = _split_md_table_row(lines[i])
        rows: list[dict] = []
        for line in lines[i + 2 :]:
            if not line.strip() or "|" not in line:
                break
            cells = _split_md_table_row(line)
            cells = (cells + [""] * len(headers))[: len(headers)]
            rows.append(dict(zip(headers, cells)))
        return rows
    return []


def format_markdown_table(rows: list[dict], columns: list[str] | None = None) -> str:
    """Format a list-of-dicts as a GitHub-flavored pipe table.

    Column order comes from ``columns`` if given, otherwise the first row's
    insertion order. Missing keys render as empty cells. All values pass
    through ``str()``. Returns an empty string when ``rows`` is empty.

    Column widths are sized to the widest cell or header (ASCII-width;
    CJK content will visually misalign but parses identically).
    """
    if not rows:
        return ""
    cols = list(columns) if columns else list(rows[0].keys())
    body = [
        ["" if r.get(c) is None else str(r.get(c)) for c in cols] for r in rows
    ]
    widths = [max(len(c), *(len(row[i]) for row in body)) for i, c in enumerate(cols)]

    def _fmt(cells: list[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"

    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    return "\n".join([_fmt(cols), sep, *(_fmt(r) for r in body)])


def fmt_num(value, spec: str, unit: str = "", *, absent: str = "—") -> str:
    """Format a number for a report cell, or ``absent`` when there is no number.

    ``spec`` is a format spec (``".1f"``, ``",.2f"``, ``"g"``); ``unit`` is
    appended verbatim, so the caller owns the space (``" kV"``) or its absence
    (``"%"``). The default ``absent`` is an em-dash, which is what every report
    table in the engineering apps already printed for a missing figure.

    Returns ``absent`` for anything that is not a number — ``None``, a string,
    a dict — **and for NaN**. Those are two different absences, and a guard
    that checks only one lets the other through: of the nine hand-rolled
    copies this replaced, eight guarded ``None`` alone and would print the
    word ``nan`` into a calculation sheet; only one was NaN-safe. The dash is
    the one honest answer to either.

    Rules:
    - Use it wherever a report, table or CLI line prints a figure that may be
      missing. Do not use it for a value that must exist — a missing number
      there is a bug to raise, not a dash to print.
    - It formats; it never converts. A string ``"12"`` renders as ``absent``,
      not as 12 — coerce at the read boundary (frontmatter is string-typed)
      before calling. ``inf`` is a number and prints as one, and a ``bool``
      is an ``int`` to Python and formats as one, exactly as the copies did.
    - Pass the value itself, or arithmetic that is already None-safe (a unit
      conversion that returns ``None`` for ``None``). Never ``x * 1000`` on a
      maybe-``None`` ``x`` — that multiply is what this keeps away from it.
    """
    if not isinstance(value, (int, float)) or value != value:
        return absent
    return f"{value:{spec}}{unit}"


# Leading characters that Excel/Sheets/LibreOffice interpret as "this cell is
# a formula" rather than literal text (OWASP CSV Injection). Any exported cell
# built from user-controlled text — a title, a free-text label, a note field —
# is exposed the moment a human opens the file in a spreadsheet app instead of
# a text editor, regardless of how the CSV is served.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe_cell(value):
    """Prefix a formula-triggering leading character with a single quote so
    a spreadsheet app renders the cell as literal text. Only strings can
    carry a formula; numbers/None/other types pass through unchanged. The
    leading quote is not stripped back out by :func:`csv_to_rows` — that's
    correct for the read side too, since a quote there also came from a
    defanged export, not from data EmptyOS itself produced.
    """
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def rows_to_csv(rows: list[dict], columns: list[str] | None = None) -> str:
    """Serialize a list-of-dicts as CSV (RFC 4180-ish, via stdlib :mod:`csv`).

    Use this at the LLM boundary — tabular reasoning is more reliable when
    the model sees CSV than a markdown table. Column order follows
    ``columns`` if given, otherwise the first row's insertion order. ``None``
    cell values render as empty fields. Returns an empty string when ``rows``
    is empty. Uses ``\\r\\n`` line endings (RFC 4180 default from stdlib csv).

    String cells are defanged against CSV formula injection (see
    :func:`_csv_safe_cell`) — this function is the shared choke-point for
    every CSV export in the codebase, so the fix lives here once rather than
    per-caller.
    """
    if not rows:
        return ""
    import csv
    import io

    cols = list(columns) if columns else list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: _csv_safe_cell("" if r.get(c) is None else r.get(c)) for c in cols})
    return buf.getvalue()


def csv_to_rows(text: str) -> list[dict]:
    """Parse CSV text into a list of dicts keyed by header.

    Use to ingest LLM output that returns tabular data. Empty input returns
    an empty list. Cells are returned verbatim — CSV is transparent about
    whitespace, so callers strip if they need to.
    """
    if not text or not text.strip():
        return []
    import csv
    import io

    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def sniff_columns(headers: list[str], aliases: dict[str, list[str]]) -> dict[str, str]:
    """Best-effort map of canonical field → the actual CSV header that carries it.

    ``aliases`` is the caller's ``{field: [header substrings, ...]}`` vocabulary —
    domain knowledge that stays with the app (bank statements, contact exports,
    word lists); only the matching is shared. Headers are compared lowercased
    and stripped, by **substring**, so ``"Debit Amount (AUD)"`` still matches
    ``"debit"``. Fields are visited in ``aliases`` order, so a field whose alias
    is a substring of another field's header (``name`` vs ``first_name``) must
    come *after* it. Within a field the aliases are tried in order and the
    first hit wins regardless of header position, so put the more specific
    alias first (``"transaction date"`` before ``"date"``). A header is claimed
    by the first field whose alias it contains, so one column can never fill
    two roles.

    Returns only the fields that matched — a missing field is absent, not ``""``.
    Not for exact-key matching (cable_network's rating import normalises keys
    and compares whole); this is the ordered-substring shape.
    """
    normalized = [(h, (h or "").strip().lower()) for h in headers]
    claimed: set[str] = set()
    mapping: dict[str, str] = {}
    for field, field_aliases in aliases.items():
        for alias in field_aliases:
            hit = next(
                (h for h, low in normalized if h not in claimed and alias in low),
                None,
            )
            if hit is not None:
                mapping[field] = hit
                claimed.add(hit)
                break
    return mapping


def csv_download_response(rows: list[dict], columns: list[str], filename: str):
    """Build a downloadable CSV ``starlette.responses.Response`` — the shared
    tail behind every ``@web_route`` CSV export (list-of-dicts → a browser
    download with the right content-type + filename).

    Unlike :func:`rows_to_csv`, ``columns`` is required and the header line is
    always written even when ``rows`` is empty — a download must open as a
    valid (if empty) spreadsheet, not silently return nothing.

    String cells are defanged against CSV formula injection the same way
    :func:`rows_to_csv` is — see :func:`_csv_safe_cell`.
    """
    import csv
    import io

    from starlette.responses import Response

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: _csv_safe_cell("" if r.get(c) is None else r.get(c)) for c in columns})
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


async def read_upload_text(
    request, *, max_bytes: int, json_key: str = "csv_content",
) -> tuple[str, str]:
    """Read one text payload from a ``@web_route`` request → ``(text, error)``.

    Accepts either a multipart upload under the form field ``file`` or a JSON
    body carrying ``json_key`` — the import-preview counterpart of
    :func:`csv_download_response`. Branch on ``error``, not on ``text``: on
    success ``error`` is ``""``; on the failures it names (no / non-file /
    empty / oversize upload, absent or empty JSON value) ``text`` is ``""`` and
    ``error`` is the short message the route returns in-band as
    ``{"error": ...}`` — so a user mistake stays a 200-with-error, never a 500
    (`.claude/rules/dev-gotchas.md` § route 500). ``text`` can still be empty
    with no error — a BOM-only file, the 3 bytes a spreadsheet writes for an
    empty sheet — which every caller then reports as "no rows found". A
    malformed JSON body still raises out of ``request.json()``, as every
    caller did before.

    ``max_bytes`` has no default on purpose — the ceiling is the app's domain
    claim ("a contact list is never 2 MB", "a statement is never 10 MB"). Bytes
    decode as ``utf-8-sig`` with replacement, so a spreadsheet export's BOM is
    dropped and one stray byte cannot abort the whole import.
    """
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return "", "no file uploaded"
        data = await upload.read()
        if not data:
            return "", "the file is empty"
        if len(data) > max_bytes:
            return "", "file is too large"
        return data.decode("utf-8-sig", "replace"), ""
    body = await request.json()
    text = body.get(json_key, "")
    return (text, "") if text else ("", f"{json_key} required")


def sweep_values(
    start: float, end: float, steps: int, *, scale: str = "linear"
) -> list[float]:
    """Generate evenly spaced axis points for a parameter sweep.

    Pairs with ``BaseApp.sweep_method`` — produces the ``values`` an
    engineering calculator is run across (e.g. ambient temperature 10→50 °C,
    or cross-section 16→400 mm² on a log axis).

    Args:
        start, end: inclusive endpoints. ``end`` may be < ``start`` (a
            descending sweep); the returned list runs start→end either way.
        steps: number of points (>= 2). ``steps == 1`` is rejected because a
            one-point "sweep" is just a single calculation.
        scale: ``"linear"`` (default) for arithmetic spacing, or ``"log"``
            for geometric spacing. ``"log"`` requires ``start`` and ``end``
            both > 0.

    Returns a list of ``steps`` floats, endpoints exact.
    """
    if steps < 2:
        raise ValueError("sweep needs steps >= 2")
    if scale not in ("linear", "log"):
        raise ValueError(f"unknown scale {scale!r} (expected 'linear' or 'log')")

    if scale == "log":
        if start <= 0 or end <= 0:
            raise ValueError("log sweep requires start > 0 and end > 0")
        import math

        log_lo, log_hi = math.log(start), math.log(end)
        out = [
            math.exp(log_lo + (log_hi - log_lo) * i / (steps - 1))
            for i in range(steps)
        ]
    else:
        out = [start + (end - start) * i / (steps - 1) for i in range(steps)]

    # Pin the endpoints exactly (float drift on the last step otherwise).
    out[0] = float(start)
    out[-1] = float(end)
    return [float(v) for v in out]


# --- Speech language routing -------------------------------------------------
# Re-exported, not defined here: tts_cache needs the same rule and must stay
# stdlib-only, so the implementation lives at top level (emptyos/speechlang.py)
# like frontmatter.py. Kept exported from here because callers import it from
# emptyos.sdk.
from emptyos.speechlang import detect_speech_language  # noqa: E402,F401

CJK_TTS_PROVIDER = "edge-tts"


def speak_provider_preference(
    text: str,
    *,
    prefer_provider=None,
    only_provider: str | None = None,
) -> list[str] | str | None:
    """Steer a Chinese line to a provider that can pronounce it.

    Applies ONLY when the caller expressed no preference. A caller that named
    providers is left exactly alone, because a pinned chain encodes
    constraints this function cannot see: voice-assistant pins kokoro to get
    **WAV** for thin clients that have no MP3 decoder, and edge-tts returns
    mp3 — an earlier draft of this function reordered that chain and turned a
    garbled-but-audible Chinese reply into a silent one written under a .wav
    name. Correcting a chain therefore needs the caller's own knowledge of
    format and locality, not a guess from here.

    Consequence worth stating: a caller that pins a kokoro-led chain still
    gets unintelligible Chinese. podcast does (``tts_providers`` defaults to
    ``["kokoro", "openai-tts"]``), so Chinese podcast audio needs that config
    changed rather than a silent override from here.

    Note the routed provider is a CLOUD one (edge-tts is Microsoft's
    endpoint, declared ``trust = "service"``), so this steer sends the text
    off-machine and passes the consent gate. That is the real trade: on this
    machine intelligible Mandarin is not available locally.
    """
    if only_provider or prefer_provider:
        return prefer_provider
    if detect_speech_language(text) == "zh":
        return [CJK_TTS_PROVIDER]
    return None
