"""Worklog markdown parsing — status emoji map + canonical-note read/render.

Canonical day-note (written by the importer and by the app):

    # 2024-10-11 Friday

    ## Plan
    …prose…

    ## Work
    ### Data Insights
    - ✅ Wire feed endpoint
    - 🔄 Tune ranking weights

    ## Timesheet
    - Data Insights: 4h

    ## Notes
    - …

Status lives as a leading emoji on a `- ` bullet — visible in a markdown
editor, yet invisible to the task aggregator (which only scans `- [ ]`
checkboxes). Pure
module: no `self`, no I/O. Frontmatter is handled by the app via the SDK.
"""
from __future__ import annotations

import re

# Ordered most-urgent → least so a day's "dominant" status sorts predictably.
STATUS_ORDER = ["blocked", "review", "in-progress", "waiting", "todo", "next", "complete"]
STATUS_EMOJI = {
    "complete": "✅",
    "in-progress": "🔄",
    "todo": "⬜",
    "next": "⏭️",
    "waiting": "⏳",
    "review": "👀",
    "blocked": "⛔",
}
EMOJI_TO_STATUS = {v: k for k, v in STATUS_EMOJI.items()}
# Status → calendar/heatmap tone (consumed by monthGrid cell tone).
STATUS_TONE = {"blocked": "overdue", "review": "today", "in-progress": "today", "complete": "done"}


def split_sections(content: str) -> dict[str, str]:
    """Map ``## Header`` → body text (everything until the next ``## ``)."""
    sections: dict[str, str] = {}
    name = None
    buf: list[str] = []
    for line in content.split("\n"):
        if line.startswith("## "):
            if name is not None:
                sections[name] = "\n".join(buf).strip()
            name = line[3:].strip()
            buf = []
        elif name is not None:
            buf.append(line)
    if name is not None:
        sections[name] = "\n".join(buf).strip()
    return sections


def _parse_item(line: str) -> dict:
    """`- ✅ text` / `- text` → {text, status|None, emoji}."""
    body = line[2:].strip() if line.startswith("- ") else line.lstrip("-").strip()
    for status, emoji in STATUS_EMOJI.items():
        if body.startswith(emoji):
            return {"text": body[len(emoji):].strip(), "status": status, "emoji": emoji}
    return {"text": body, "status": None, "emoji": ""}


def parse_work(work_body: str) -> list[dict]:
    """`## Work` body → [{project, items:[{text,status,emoji}]}]."""
    groups: list[dict] = []
    cur: dict | None = None
    for line in work_body.split("\n"):
        s = line.rstrip()
        if s.startswith("### "):
            cur = {"project": s[4:].strip(), "items": []}
            groups.append(cur)
        elif s.lstrip().startswith("- "):
            if cur is None:
                cur = {"project": "General", "items": []}
                groups.append(cur)
            cur["items"].append(_parse_item(s.strip()))
    return groups


def parse_timesheet(body: str) -> list[dict]:
    out = []
    for line in body.split("\n"):
        s = line.strip()
        if not s.startswith("- ") or ":" not in s:
            continue
        label, _, rest = s[2:].partition(":")
        rest = rest.strip()
        note = ""
        if "—" in rest:
            rest, _, note = rest.partition("—")
        hours = rest.strip().rstrip("hH").strip()
        try:
            hours_f = float(hours)
        except ValueError:
            continue
        out.append({"project": label.strip(), "hours": hours_f, "note": note.strip()})
    return out


# ── logged time ───────────────────────────────────────────────────────────
# The hand-written convention already in the corpus: a `> Logged time: …` line
# inside `## Work`, one per day, in 138 of 213 notes. `## Timesheet` bullets
# (which parse_timesheet reads) appear on 5 days and are all machine-written.
# So this is what the user ACTUALLY records — the app reads their convention
# rather than asking them to adopt the app's. Never rewrite these lines into
# `## Timesheet`: the note is hand-editable and stays the source of truth.
_LOGGED_TIME = re.compile(r"logged\s+time\s*:\s*(.+?)\s*$", re.I)
# `8:30 - 17:00`, `09:00-17:30`, `8:30 am - 11:30 pm`; trailing prose is a note.
_TIME_RANGE = re.compile(
    r"^(\d{1,2}):(\d{2})\s*(am|pm)?\s*[-–—]+\s*(\d{1,2}):(\d{2})\s*(am|pm)?\s*(.*)$",
    re.I,
)
# `8h with 0.5h break` — a bare duration instead of a range.
_TIME_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*h\b\s*(.*)$", re.I)


def _to_minutes(hour: str, minute: str, meridiem: str | None) -> int:
    h, m = int(hour), int(minute)
    if meridiem:
        mer = meridiem.lower()
        if mer == "pm" and h != 12:
            h += 12
        elif mer == "am" and h == 12:
            h = 0
    return h * 60 + m


def parse_logged_time(content: str) -> list[dict]:
    """Whole-note scan for `Logged time:` entries → [{start, end, hours, note}].

    Section-agnostic on purpose: the convention is a blockquote that happens to
    sit under `## Work`, and pinning it to a section would silently drop it the
    day someone files it elsewhere. Unparseable values (`Logged time: 3 Feb 25`)
    are skipped rather than guessed at, and a range that does not move forward
    is treated as a typo, not an overnight shift — a work log is not a night
    roster, and inventing 24 hours would corrupt a total.
    """
    out: list[dict] = []
    for line in content.split("\n"):
        found = _LOGGED_TIME.search(line.strip().lstrip("> ").strip())
        if not found:
            continue
        value = found.group(1).strip()
        span = _TIME_RANGE.match(value)
        if span:
            start = _to_minutes(span.group(1), span.group(2), span.group(3))
            end = _to_minutes(span.group(4), span.group(5), span.group(6))
            if end <= start:
                continue
            out.append({
                "start": f"{start // 60:02d}:{start % 60:02d}",
                "end": f"{end // 60:02d}:{end % 60:02d}",
                "hours": round((end - start) / 60, 2),
                "note": span.group(7).strip().lstrip(",").strip(),
            })
            continue
        dur = _TIME_DURATION.match(value)
        if dur:
            out.append({"start": "", "end": "", "hours": round(float(dur.group(1)), 2),
                        "note": dur.group(2).strip()})
    return out


def day_hours(parsed_timesheet: list[dict], logged: list[dict]) -> float:
    """Hours for one day, without double-counting the two conventions.

    An itemised `## Timesheet` is per-project and strictly more informative, so
    it wins when both are present; `Logged time:` is the day-level fallback.
    """
    if parsed_timesheet:
        return round(sum(float(t.get("hours") or 0) for t in parsed_timesheet), 2)
    return round(sum(float(t.get("hours") or 0) for t in logged), 2)


# ── import debris ─────────────────────────────────────────────────────────
# Long URLs in imported content wrapped across lines, and every line beginning
# `- ` was read as its own work item. 122 of this vault's 1,265 untagged items
# are such fragments; they clutter the tagging backlog and can never carry a
# meaningful status.
#
# Calibrated against the real corpus (.claude/rules/audits.md — measure the
# false-positive rate before shipping a heuristic). A blanket "under 6
# characters" rule was tried first and REJECTED: it hid `DONE`, `HOLD`, `HVDC`,
# `Cores` and `base`, which are real. What survives is three rules with no
# observed false positive, so `Cab`/`d`/`ors` stay visible — mildly noisy, but
# hiding real work is the worse error, and this only ever powers an opt-in
# toggle, never a write.
_URL_ITEM = re.compile(r"^https?://", re.I)
# Length alone is not enough: `emptyos/runtime/vault_index.py` is 30 characters
# with no spaces and is a perfectly good work item. What separates debris is
# URL/query *syntax* — a query or fragment character, several path segments, or
# an opaque identifier run (`price_1AbcdeFGhijKLmnoPQrsTuvw`).
_DEBRIS_SYNTAX = re.compile(r"[=&%?#]")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9]{16,}")


def looks_like_import_debris(text: str) -> bool:
    """True for a work item that is a fragment of imported content, not work."""
    t = (text or "").strip()
    if not t:
        return True
    if _URL_ITEM.match(t):                       # a bare pasted link
        return True
    if " " not in t and len(t) > 25 and (
        _DEBRIS_SYNTAX.search(t) or t.count("/") >= 3 or _OPAQUE_ID.search(t)
    ):
        return True
    if not re.search(r"[A-Za-z]", t):            # page numbers, "-1", "1."
        return True
    return False


def status_counts(groups: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for g in groups:
        for it in g["items"]:
            st = it.get("status")
            if st:
                counts[st] = counts.get(st, 0) + 1
    return counts


def dominant_status(groups: list[dict]) -> str | None:
    counts = status_counts(groups)
    for st in STATUS_ORDER:
        if counts.get(st):
            return st
    return None


def parse_day(content: str) -> dict:
    """Full day note → structured dict (frontmatter excluded — app handles it)."""
    secs = split_sections(content)
    groups = parse_work(secs.get("Work", ""))
    timesheet = parse_timesheet(secs.get("Timesheet", ""))
    logged = parse_logged_time(content)
    return {
        "plan": secs.get("Plan", ""),
        "update": secs.get("Update", ""),
        "projects": groups,
        "timesheet": timesheet,
        "logged_time": logged,
        "hours": day_hours(timesheet, logged),
        "notes": [l[2:].strip() for l in secs.get("Notes", "").split("\n") if l.strip().startswith("- ")],
        "status_counts": status_counts(groups),
    }


def get_section(content: str, header: str) -> str:
    """Body under a ``## Header`` line (until the next ``## ``). '' if absent."""
    return split_sections(content).get(header[3:].strip() if header.startswith("## ") else header, "")


def replace_section(content: str, header: str, new_body: str) -> str:
    """Replace (or insert before EOF) the body under ``## Header``.

    ``header`` is the bare name (e.g. "Work"). Inserts the section at the end
    if it doesn't exist yet. The ``## Header`` line itself is preserved/created.
    """
    name = header[3:].strip() if header.startswith("## ") else header
    lines = content.split("\n")
    out: list[str] = []
    i = 0
    replaced = False
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("## ") and line[3:].strip() == name:
            out.append(line)
            out.append("")
            if new_body.strip():
                out.append(new_body.rstrip())
            out.append("")
            i += 1
            while i < n and not lines[i].startswith("## "):
                i += 1
            replaced = True
            continue
        out.append(line)
        i += 1
    result = "\n".join(out).rstrip() + "\n"
    if not replaced:
        body = ("\n" + new_body.rstrip()) if new_body.strip() else ""
        result = result.rstrip() + f"\n\n## {name}\n{body}\n"
    return result


def append_section(content: str, header: str, extra_body: str) -> str:
    """Append lines to a ``## Header`` body, keeping the existing body verbatim.

    ``replace_section`` + a re-render is lossy for any section the parser only
    partially models: ``## Notes`` keeps free prose and ``## Timesheet`` keeps
    rows whose project name contains a colon, neither of which survives a
    parse/render round trip. The importer appends through here so hand-written
    markdown is never rewritten — only added to.
    """
    if not extra_body.strip():
        return content
    body = get_section(content, header)
    merged = f"{body.rstrip()}\n{extra_body.strip()}" if body.strip() else extra_body.strip()
    return replace_section(content, header, merged)


def _render_item(it: dict) -> str:
    """One `- ` line. Shared so the two renderers cannot drift apart."""
    emoji = STATUS_EMOJI.get(it.get("status") or "", "")
    return f"- {emoji} {it['text']}" if emoji else f"- {it['text']}"


def render_work(groups: list[dict]) -> str:
    """[{project, items}] → ``## Work`` body markdown (re-rendered on each write)."""
    out: list[str] = []
    for g in groups:
        if not g.get("items"):
            continue
        out.append(f"### {g['project']}")
        out.extend(_render_item(it) for it in g["items"])
        out.append("")
    return "\n".join(out).strip()


# ── preserving render ────────────────────────────────────────────────────────
# `parse_work` models a `## Work` section as headings + items and NOTHING else,
# so `parse_work -> render_work` deletes every other line in that section. That
# is not hypothetical: `timer_stop` writes `> Logged time: 09:00-17:30` into
# `## Work` via append_section, and 138 of this vault's 227 day notes carry that
# hand-written convention — so stopping the timer and then logging one more item
# silently destroyed the day's recorded hours. The corpus also carries
# `<!-- ai-conversation-evidence:... -->` markers in the same section.
#
# `apply_merge_to_note` already protects Timesheet and Notes from exactly this
# ("a parse/render round trip would silently delete all three from the user's
# own file"); Work was the one section the lesson was never applied to.
#
# Callers that build groups from scratch (a brand-new note) can keep using
# `render_work` — there is nothing to preserve. Anything rewriting an EXISTING
# note goes through here.

def _is_heading(line: str) -> bool:
    """Mirror ``parse_work`` exactly: a heading must start at column 0."""
    return line.rstrip().startswith("### ")


def _is_item(line: str) -> bool:
    """Mirror ``parse_work`` exactly: an item may be indented."""
    return line.lstrip().startswith("- ")


def _trim(lines: list[str]) -> list[str]:
    """Drop leading/trailing blank lines so a re-render cannot accumulate them."""
    out = list(lines)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def work_extras(work_body: str) -> dict:
    """Lines in `## Work` the group model does not carry, keyed by where they sit.

    ``{"preamble": [...], "headings": [project_lower], "tails": {project_lower:
    [...]}, "tail": [...]}`` — `preamble` precedes the first group, a group's
    `tails` entry follows its items, and `tail` trails the last group (the
    day-level position the `> Logged time:` convention actually uses).

    ``headings`` exists so the renderer can keep a heading that has no items
    under it. The corpus has 9 such notes (`### 54 day safe projects`), and
    dropping the heading does not merely lose one line — it silently
    re-attributes the prose beneath it to the group above.

    The two predicates mirror ``parse_work`` character for character. They used
    to differ on indentation (`  ### X` was structural here and unmodelled
    there), which made that line belong to neither side and vanish.
    """
    preamble: list[str] = []
    headings: list[str] = []
    tails: dict[str, list[str]] = {}
    buf: list[str] = []
    current = ""
    for raw in (work_body or "").split("\n"):
        line = raw.rstrip("\r")     # a CRLF note must not emit mixed endings
        if _is_heading(line):
            if current:
                # extend, never assign: two groups sharing a name would
                # otherwise destroy the first one's extras.
                tails.setdefault(current, []).extend(_trim(buf))
            else:
                preamble.extend(_trim(buf))
            buf = []
            current = line.strip()[4:].strip().lower()
            headings.append(current)
        elif _is_item(line):
            # Outside any group an item closes the preamble. INSIDE a group the
            # buffer keeps accumulating, so prose sitting between two items is
            # re-emitted after both — a reordering, never a loss, and absent
            # from the corpus. Modelling it would need a per-item position.
            if not current and buf:
                preamble.extend(_trim(buf))
                buf = []
        else:
            buf.append(line)
    return {"preamble": preamble, "headings": headings,
            "tails": tails, "tail": _trim(buf)}


def render_work_preserving(original_work_body: str, groups: list[dict]) -> str:
    """Render *groups* keeping every unmodelled line from *original_work_body*.

    Position is preserved by project name. Extras belonging to a group that has
    since been renamed or removed are appended rather than dropped.

    Measured against the real 227-note corpus (209 with a `## Work`): the plain
    `render_work` round trip loses lines on **145** of them; this loses none.
    Three fidelity limits remain, all rooted in `parse_work` itself and none of
    them a *loss*: a nested bullet is flattened to column 0; prose sitting
    BETWEEN two items of one group is re-emitted after both; and a fenced code
    block is not understood as a fence, so `### x` inside one has always been
    read as a real heading (this moves the fence lines rather than fixing that).
    None occurs in the corpus. Fixing any would mean giving `parse_work` a
    per-item position model, which changes the `/api/day` and portable payloads.
    """
    extras = work_extras(original_work_body)
    tails = dict(extras["tails"])
    known = set(extras["headings"])
    out: list[str] = list(extras["preamble"])
    if out:
        out.append("")
    for g in groups:
        name = g.get("project") or ""
        key = name.lower()
        items = g.get("items") or []
        # An empty group the ORIGINAL carried keeps its heading; one invented by
        # a caller does not, which is what `render_work` has always done.
        if not items and key not in known:
            continue
        out.append(f"### {name}")
        out.extend(_render_item(it) for it in items)
        tail = tails.pop(key, [])
        if tail:
            out.append("")      # the blank line that separated items from extras
            out.extend(tail)
        out.append("")
    orphaned = [l for lines in tails.values() for l in lines]
    out.extend(orphaned + extras["tail"])
    return "\n".join(out).strip()


def render_timesheet(rows: list[dict]) -> str:
    """Structured portable rows -> canonical ``## Timesheet`` bullets."""
    out: list[str] = []
    for row in rows:
        project = str(row.get("project") or "General").strip() or "General"
        try:
            hours = float(row.get("hours") or 0)
        except (TypeError, ValueError):
            continue
        note = str(row.get("note") or "").strip()
        suffix = f" \u2014 {note}" if note else ""
        out.append(f"- {project}: {hours:g}h{suffix}")
    return "\n".join(out)


def render_notes(notes: list[str]) -> str:
    """Structured portable notes -> canonical ``## Notes`` bullets."""
    return "\n".join(f"- {str(note).strip()}" for note in notes if str(note).strip())
