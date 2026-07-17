"""Dev-track data layer — parsers for the session-track index, track briefs,
and the deferred-work registry, plus the surgical row edits devboard performs.

Canonical extraction (CLAUDE.md rule 9, second consumer) of the freestanding
parsers in ``scripts/session_start_brief.py``. The hook keeps its own
stdlib-only copy by design — it must survive a fresh clone before
``pip install -e .`` — so changes to the table formats must be mirrored there.

Sources parsed:

- ``{vault}/10_Projects/emptyos/log/_next/_index.md`` — one markdown table
  ``| Track | Last touched | Last session | File |``. Col 3 is free prose that
  embeds pipes and even nested tables, so parsing anchors on the header row
  and keeps only rows whose second cell parses as a date.
- ``{vault}/10_Projects/emptyos/log/_next/<slug>.md`` — per-track briefs with
  the 8-key frontmatter contract written by /eos-session-wrapup.
- ``docs/DEFERRED-WORK.md`` — table ``| Feature | Trigger | Reference |
  Source | Added | Status |``; shipped rows are struck through (``~~…~~``).

Pure functions only — no ``self``, no kernel access, no I/O.
"""

from __future__ import annotations

import datetime
import re
import tomllib
from dataclasses import dataclass, field

# Shared thresholds (the hook's _AGING_DAYS is the same 30).
FRESH_DAYS = 7
STALE_DAYS = 30
AGING_DAYS = 30

DEFERRED_STATUS_VOCAB = frozenset(
    {"deferred", "triggered", "building", "built", "dropped"}
)

_MD_LINK_RE = re.compile(r"^\[([^\]]+)\]\([^)]*\)$")
_WIKILINK_RE = re.compile(r"^\[\[([^\]|]+)(?:\|[^\]]*)?\]\]$")
_BLOCKED_HUMAN_RE = re.compile(r"\[blocked-human\]", re.IGNORECASE)


@dataclass
class TrackRow:
    """One parsed row of the _index.md track table."""

    name: str  # slug extracted from the Track cell
    last_touched: str  # YYYY-MM-DD (validated)
    last_session: str  # free prose (may be truncated mid-pipe — display only)
    file: str  # brief filename hint from the File cell ("" when absent)
    line_no: int  # 0-based line index in the source text (row surgery anchor)
    raw_line: str


@dataclass
class DeferredRow:
    """One parsed row of the DEFERRED-WORK.md table."""

    feature: str
    trigger: str
    reference: str
    verdict: str
    added: str
    status: str
    struck: bool  # ~~…~~ shipped rows — excluded from live counts
    line_no: int
    raw_line: str


@dataclass
class TrackBrief:
    """Parsed per-track brief (frontmatter contract + body sections)."""

    track: str
    written: str
    last_session: str
    last_session_title: str
    threads_cleared: int
    threads_added: int
    threads_carried: int
    sections: dict[str, str] = field(default_factory=dict)
    open_threads: list[dict] = field(default_factory=list)  # {text, blocked_human}
    blocked_human: bool = False


def parse_date(s: str) -> datetime.date | None:
    """Parse the loose date shapes the tables use (mirrors the hook)."""
    s = (s or "").strip().lstrip("~").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _cell_slug(cell: str) -> str:
    """Extract the track slug from a Track/File cell: ``[slug](slug.md)``,
    ``[[slug]]``, or a bare name."""
    cell = cell.strip().strip("*").strip()
    m = _MD_LINK_RE.match(cell) or _WIKILINK_RE.match(cell)
    return (m.group(1) if m else cell).strip()


def parse_track_index(text: str) -> list[TrackRow]:
    """Parse the 'Next-session tracks' table out of _index.md.

    Anchors on the ``| Track | Last touched`` header and keeps only rows
    whose second cell parses as a date — col-3 prose embeds pipes, so a
    naive pipe-split is only trusted for the first two cells.
    """
    rows: list[TrackRow] = []
    in_table = False
    for i, ln in enumerate(text.splitlines()):
        if not in_table:
            low = ln.lower()
            if low.startswith("| track") and "last touched" in low:
                in_table = True
            continue
        if not ln.lstrip().startswith("|"):
            break  # table ended
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        name_cell, touched = cells[0], cells[1]
        if set(name_cell) <= {"-", ":", " "}:  # separator row
            continue
        if not parse_date(touched):
            continue
        rows.append(
            TrackRow(
                name=_cell_slug(name_cell),
                last_touched=touched,
                last_session=cells[2] if len(cells) > 2 else "",
                file=_cell_slug(cells[-1]) if len(cells) >= 4 else "",
                line_no=i,
                raw_line=ln,
            )
        )
    return rows


def parse_track_brief(frontmatter: dict, body: str) -> TrackBrief:
    """Parse a per-track brief: the 8-key frontmatter contract + ## sections."""

    def _int(key: str) -> int:
        try:
            return int(str(frontmatter.get(key, 0)).strip())
        except (TypeError, ValueError):
            return 0

    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for ln in (body or "").splitlines():
        if ln.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = ln[3:].strip()
            buf = []
        elif current is not None:
            buf.append(ln)
    if current is not None:
        sections[current] = "\n".join(buf).strip()

    open_threads: list[dict] = []
    for ln in sections.get("Open threads", "").splitlines():
        s = ln.strip()
        if not s.startswith(("- ", "* ")):
            continue
        txt = s[2:].strip()
        open_threads.append(
            {"text": txt, "blocked_human": bool(_BLOCKED_HUMAN_RE.search(txt))}
        )

    return TrackBrief(
        track=str(frontmatter.get("track", "") or ""),
        written=str(frontmatter.get("written", "") or ""),
        last_session=str(frontmatter.get("last_session", "") or ""),
        last_session_title=str(frontmatter.get("last_session_title", "") or ""),
        threads_cleared=_int("threads_cleared"),
        threads_added=_int("threads_added"),
        threads_carried=_int("threads_carried"),
        sections=sections,
        open_threads=open_threads,
        blocked_human=any(t["blocked_human"] for t in open_threads),
    )


def parse_deferred_table(text: str) -> list[DeferredRow]:
    """Parse every data row of the DEFERRED-WORK.md table (≥6 cells)."""
    rows: list[DeferredRow] = []
    for i, ln in enumerate(text.splitlines()):
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 6:
            continue
        if set(cells[0]) <= {"-", ":", " "}:  # separator
            continue
        if cells[0].lower() == "feature" and cells[-1].lower() == "status":
            continue  # header
        feature = cells[0]
        struck = feature.startswith("~~")
        rows.append(
            DeferredRow(
                feature=feature.replace("~~", "").replace("**", "").strip(),
                trigger=cells[1],
                reference=cells[2],
                verdict=cells[3] if len(cells) > 3 else "",
                added=cells[-2],
                status=cells[-1].strip("~").strip().lower(),
                struck=struck,
                line_no=i,
                raw_line=ln,
            )
        )
    return rows


def classify_track(
    last_touched: str, blocked: bool, today: datetime.date | None = None
) -> str:
    """Health class: blocked > stale (>30d) > aging (7-30d) > fresh (<7d)."""
    if blocked:
        return "blocked"
    d = parse_date(last_touched)
    if d is None:
        return "stale"
    age = ((today or datetime.date.today()) - d).days
    if age > STALE_DAYS:
        return "stale"
    if age >= FRESH_DAYS:
        return "aging"
    return "fresh"


def classify_deferred(added: str, today: datetime.date | None = None) -> str:
    d = parse_date(added)
    if d is None:
        return "aging"
    age = ((today or datetime.date.today()) - d).days
    return "aging" if age > AGING_DAYS else "fresh"


def age_days(date_str: str, today: datetime.date | None = None) -> int | None:
    d = parse_date(date_str)
    if d is None:
        return None
    return ((today or datetime.date.today()) - d).days


# ── Dev goals (themes) ───────────────────────────────────────────────────────
# {vault}/10_Projects/emptyos/log/_themes.toml — the hand-curated goal registry
# ([[theme]] tables). The progress app renders the same file; keep schemas in
# sync (apps/extension/dev/progress/app.py api_summary).


@dataclass
class Theme:
    """One dev goal from _themes.toml."""

    id: str
    order: int
    name: str
    purpose: str  # "niw" | "personal"
    done_enough: str
    tracks: list[str] = field(default_factory=list)  # declared _index.md slugs
    milestones: list[dict] = field(default_factory=list)  # {text, done}
    shipped: int = 0  # milestones with done=true
    total: int = 0


def parse_themes(toml_text: str) -> list[Theme]:
    """Parse _themes.toml into ordered Theme records. Fail-soft to []."""
    try:
        raw = tomllib.loads(toml_text or "")
    except (tomllib.TOMLDecodeError, TypeError):
        return []
    themes: list[Theme] = []
    for t in raw.get("theme", []) or []:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        ms = [m for m in (t.get("milestones") or []) if isinstance(m, dict)]
        themes.append(
            Theme(
                id=str(t["id"]),
                order=int(t.get("order") or 0),
                name=str(t.get("name") or t["id"]),
                purpose=str(t.get("purpose") or ""),
                done_enough=str(t.get("done_enough") or ""),
                tracks=[str(s) for s in (t.get("tracks") or [])],
                milestones=ms,
                shipped=sum(1 for m in ms if m.get("done")),
                total=len(ms),
            )
        )
    themes.sort(key=lambda t: t.order)
    return themes


def theme_index(themes: list[Theme]) -> dict[str, str]:
    """Reverse map track-slug → theme id (first declaring theme wins)."""
    out: dict[str, str] = {}
    for t in themes:
        for slug in t.tracks:
            out.setdefault(slug, t.id)
    return out


_DEVLOG_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(.+))?$")


def parse_devlog_meta(frontmatter: dict, filename: str) -> dict | None:
    """Meta for one dated devlog: {date, title, tracks}.

    ``filename`` is the stem (no .md). Non-dated filenames (``_index``,
    ``_themes``, prose notes) return None. ``tracks`` comes from the
    frontmatter list the wrapup skill stamps (absent on legacy devlogs → []).
    """
    m = _DEVLOG_DATE_RE.match((filename or "").strip())
    if not m:
        return None
    tracks_raw = frontmatter.get("tracks")
    if isinstance(tracks_raw, str):
        tracks = [s for s in re.split(r"[,\s]+", tracks_raw) if s]
    elif isinstance(tracks_raw, list):
        tracks = [str(s) for s in tracks_raw if s]
    else:
        tracks = []
    title = str(frontmatter.get("title") or "").strip()
    if not title and m.group(2):
        title = m.group(2).replace("-", " ")
    return {"date": m.group(1), "title": title, "tracks": tracks}


# ── Cross-linking ────────────────────────────────────────────────────────────

_NON_KEY_RE = re.compile(r"[^a-z0-9]+")


def link_key(s: str) -> str:
    """Normalize an identifier for fuzzy matching: lowercase, punctuation → '-'.

    Flag keys drop their ``feature.`` / ``.enabled`` wrapper first so a flag
    matches the slug it wraps.
    """
    s = (s or "").strip().lower()
    if s.startswith("feature.") and s.endswith(".enabled"):
        s = s[len("feature.") : -len(".enabled")]
    return _NON_KEY_RE.sub("-", s).strip("-")


def links_for_track(track_slug: str, items: list[dict]) -> list[dict]:
    """Items related to one track.

    Each item: ``{kind, key, title, href, track?}`` — ``track`` is an explicit
    frontmatter declaration and wins outright. Otherwise fuzzy: normalized keys
    equal, or one contains the other (min length 6 to avoid noise).
    """
    tk = link_key(track_slug)
    out: list[dict] = []
    for item in items:
        explicit = link_key(str(item.get("track") or ""))
        if explicit:
            if explicit == tk:
                out.append(item)
            continue
        ik = link_key(str(item.get("key") or ""))
        if not ik or not tk:
            continue
        if ik == tk or (
            min(len(ik), len(tk)) >= 6 and (ik in tk or tk in ik)
        ):
            out.append(item)
    return out


# ── Row surgery (write verbs) ────────────────────────────────────────────────


def remove_index_row(text: str, track_slug: str) -> str | None:
    """Delete exactly one track's row from the _index.md table.

    Returns the new text, or None when the track has no (unique) row. Never
    rebuilds the table — col-3 prose is not round-trippable through a
    pipe-split.
    """
    rows = [r for r in parse_track_index(text) if r.name == track_slug]
    if len(rows) != 1:
        return None
    lines = text.splitlines(keepends=True)
    target = rows[0].line_no
    if target >= len(lines):
        return None
    del lines[target]
    return "".join(lines)


def set_deferred_status(text: str, feature: str, status: str) -> str | None:
    """Replace only the final (Status) cell of the matching deferred row.

    ``feature`` is matched against the parsed feature text (bold/strike
    markers stripped). Returns new text, or None on unknown status / no
    unique match / struck row.
    """
    status = (status or "").strip().lower()
    if status not in DEFERRED_STATUS_VOCAB:
        return None
    matches = [
        r
        for r in parse_deferred_table(text)
        if not r.struck and r.feature == feature.strip()
    ]
    if len(matches) != 1:
        return None
    row = matches[0]
    line = row.raw_line
    body = line.rstrip()
    if not body.endswith("|"):
        return None
    head, sep, _last = body[:-1].rpartition("|")
    if not sep:
        return None
    new_line = f"{head}| {status} |" + line[len(body):]
    lines = text.splitlines(keepends=True)
    old = lines[row.line_no]
    trailing = old[len(old.rstrip("\r\n")):]
    lines[row.line_no] = new_line.rstrip("\r\n") + trailing
    return "".join(lines)
