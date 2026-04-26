"""Shared utilities for EmptyOS apps."""

import json
import re
from datetime import date, timedelta
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


def save_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write ``data`` as UTF-8 JSON to ``path``.

    ``default=str`` so datetime/Path values serialise without crashing.
    ``ensure_ascii=False`` preserves non-ASCII characters readably (e.g. note
    titles in other scripts) instead of escaping to ``\\uXXXX``.
    """
    path.write_text(
        json.dumps(data, indent=indent, default=str, ensure_ascii=False),
        encoding="utf-8",
    )

# --- Task parsing constants ---

TASK_RE = re.compile(
    r"- \[([ xX])\] (.+?)(?:\s*📅\s*(\d{4}-\d{2}-\d{2}))?(?:\s*✅\s*(\d{4}-\d{2}-\d{2}))?\s*$"
)
DUE_PATTERN = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
DONE_PATTERN = re.compile(r"✅\s*(\d{4}-\d{2}-\d{2})")

CAPTURE_LINE_RE = re.compile(
    r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — (.+?)(?:\s+#(\S+))?$"
)


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
            entries.append({
                "timestamp": m.group(1),
                "text": m.group(2).strip(),
                "tag": m.group(3) or "",
            })
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
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            if val:
                # Inline YAML array: [a, b, c]
                if val.startswith("[") and val.endswith("]"):
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
            return content[end + 3:]
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


def slugify(text: str, max_len: int = 60) -> str:
    """Convert text to a URL/filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len]


def today_iso() -> str:
    """Local-timezone date today as ISO string (``"YYYY-MM-DD"``).

    For app data dated by calendar day — habit logs, daily journal entries,
    "what did I do today" summaries — where the user's local day boundary is
    what matters.

    Not for: timestamps (use ``datetime.now(timezone.utc).isoformat()``);
    UTC date keys (call ``today_utc()`` from ``time_series`` so the timezone
    intent is visible at the call site).
    """
    return date.today().isoformat()


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
        raise ValueError("malformed data URL: missing comma separator")
    if ";base64" not in header:
        raise ValueError("only base64-encoded data URLs are supported")
    mime = header[len("data:"):].split(";", 1)[0] or "application/octet-stream"
    raw = base64.b64decode(payload)
    return mime, raw
