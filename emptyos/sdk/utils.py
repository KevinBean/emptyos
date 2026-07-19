"""Shared utilities for EmptyOS apps."""

import json
import math
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


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


def new_id(prefix: str, n: int = 8) -> str:
    """Generate a short opaque ID like ``"out-3a7c91f2"``.

    Used across apps for ephemeral record IDs (learnings, outreach, stories,
    canvas cards, etc.). The prefix carries the type; the suffix carries
    enough entropy for collision avoidance within a single store.
    """
    return f"{prefix}-{uuid.uuid4().hex[:n]}"


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


def parse_llm_json(text: str, fallback: dict | list | None = None) -> dict | list:
    """Extract JSON from LLM output — handles markdown fences, preamble, nested braces/brackets.

    Supports both JSON objects ({}) and arrays ([]).

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
    # Find first valid JSON object or array by brace/bracket matching
    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        depth = 0
        start = None
        for i, ch in enumerate(text[:10000]):  # Cap at 10KB
            if ch == open_ch:
                if depth == 0:
                    start = i
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        start = None
    if fallback is not None:
        return fallback
    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}")


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


def parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from markdown content.

    Extracts key-value pairs from the ``---`` delimited block at the top of a
    markdown file.  Values are stripped of surrounding quotes.  Handles simple
    YAML lists (``- item`` lines following a key with no inline value).
    """
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end < 0:
        return {}
    fm: dict = {}
    current_key = None
    current_list: list[str] | None = None
    for line in content[3:end].strip().split("\n"):
        stripped = line.strip()
        # YAML list item (indented "- value")
        if stripped.startswith("- ") and current_key is not None and current_list is not None:
            current_list.append(stripped[2:].strip().strip('"').strip("'"))
            continue
        # Flush any pending list
        if current_key is not None and current_list is not None:
            fm[current_key] = current_list
            current_key = None
            current_list = None
        if ":" in line and not stripped.startswith("-"):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            # Track whether the raw value was wrapped in YAML string quotes
            # ("..." or '...'). A quote-wrapped value is ALWAYS a string —
            # never an inline YAML array — even if its inner content starts
            # with [. This prevents JSON-encoded strings like
            # `svg_callouts: "[{...}, {...}]"` from being mis-split on
            # commas into a list of garbage fragments.
            was_quoted = False
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                # YAML double-quoted strings support \" escapes; YAML single-
                # quoted strings don't, but JSON-encoded values always land
                # in double quotes from json.dumps. Unescape \" → " inside
                # double-quoted values so consumers see the real string.
                quote_char = val[0]
                val = val[1:-1]
                if quote_char == '"':
                    val = val.replace('\\"', '"').replace("\\\\", "\\")
                was_quoted = True
            if val:
                # Inline YAML array: [a, b, c] — only when the raw value
                # was NOT quote-wrapped. A quoted value like "[...]" is a
                # string whose content happens to start with [.
                if not was_quoted and val.startswith("[") and val.endswith("]"):

                    def _unquote(v: str) -> str:
                        v = v.strip()
                        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                            return v[1:-1]
                        return v

                    items = [_unquote(v) for v in val[1:-1].split(",") if v.strip()]
                    fm[key] = items if items else ""
                else:
                    fm[key] = val
            else:
                # Could be start of a list
                current_key = key
                current_list = []
    # Flush final list
    if current_key is not None and current_list is not None:
        fm[current_key] = current_list if current_list else ""
    return fm


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


def strip_frontmatter(content: str) -> str:
    """Return markdown content with the YAML frontmatter block removed."""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            return content[end + 3 :]
    return content


def set_frontmatter_field(content: str, key: str, raw_value: str) -> str:
    """Insert or replace ``key: <raw_value>`` in the frontmatter block.

    Pure string transform. *raw_value* is written verbatim after ``key: ``;
    the caller owns YAML encoding (quoting strings, ``[a, b]`` for lists,
    escaping newlines). If no ``---`` block exists, one is created at the top.

    Use for: simple single-line scalar/array fields. Not for: nested YAML,
    block-style list values (``key:\\n  - a``) — use a real YAML writer there.
    """
    line = f"{key}: {raw_value}"
    if content.startswith("---"):
        fm_end = content.find("---", 3)
        if fm_end > 0:
            fm_block = content[3:fm_end]
            pattern = re.compile(rf"(?m)^{re.escape(key)}\s*:.*$")
            if pattern.search(fm_block):
                fm_block = pattern.sub(lambda _m: line, fm_block, count=1)
            else:
                fm_block = fm_block.rstrip() + "\n" + line + "\n"
            return "---" + fm_block + content[fm_end:]
    return f"---\n{line}\n---\n{content}"


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


_SAFE_PATH_SEG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def safe_path_segment(raw: str) -> str:
    """Return ``raw`` if it's a plain slug, else ``""`` — a path-traversal guard.

    For caller-supplied ids (HTTP path params, agent ``call_app`` args) that
    flow into vault filesystem paths. Restricts to ``[A-Za-z0-9_-]`` so ``..``,
    ``/``, ``\\``, and leading dots can't escape the intended directory.

    Unlike :func:`slugify` / :func:`unique_slug` (which *transform* text into a
    slug), this **rejects** — it returns ``""`` for anything unsafe so callers
    fail loudly (or fall back to a contained default) rather than silently
    coercing ``"../etc"`` into ``"etc"``.
    """
    raw = (raw or "").strip()
    return raw if _SAFE_PATH_SEG_RE.match(raw) else ""


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
        "underscore or hyphen"
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


def extract_wikilinks(text: str) -> set[str]:
    """Return the set of wikilink target slugs in ``text``.

    Strips ``#section`` anchors and ``|label`` aliases. Empty input → empty set.
    """
    if not text:
        return set()
    return {m.group(1).strip() for m in WIKILINK_RE.finditer(text) if m.group(1).strip()}


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


def rows_to_csv(rows: list[dict], columns: list[str] | None = None) -> str:
    """Serialize a list-of-dicts as CSV (RFC 4180-ish, via stdlib :mod:`csv`).

    Use this at the LLM boundary — tabular reasoning is more reliable when
    the model sees CSV than a markdown table. Column order follows
    ``columns`` if given, otherwise the first row's insertion order. ``None``
    cell values render as empty fields. Returns an empty string when ``rows``
    is empty. Uses ``\\r\\n`` line endings (RFC 4180 default from stdlib csv).
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
        writer.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})
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
