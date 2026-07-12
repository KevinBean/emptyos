"""Worklog markdown parsing — status emoji map + canonical-note read/render.

Canonical day-note (written by the importer and by the app):

    # 2024-10-11 Friday

    ## Plan
    …prose…

    ## Work
    ### AI Insights
    - ✅ Wire feed endpoint
    - 🔄 Tune ranking weights

    ## Timesheet
    - AI Insights: 4h

    ## Notes
    - …

Status lives as a leading emoji on a `- ` bullet — visible in a markdown
editor, yet invisible to the task aggregator (which only scans `- [ ]`
checkboxes). Pure
module: no `self`, no I/O. Frontmatter is handled by the app via the SDK.
"""
from __future__ import annotations

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
    return {
        "plan": secs.get("Plan", ""),
        "update": secs.get("Update", ""),
        "projects": groups,
        "timesheet": parse_timesheet(secs.get("Timesheet", "")),
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


def render_work(groups: list[dict]) -> str:
    """[{project, items}] → ``## Work`` body markdown (re-rendered on each write)."""
    out: list[str] = []
    for g in groups:
        if not g.get("items"):
            continue
        out.append(f"### {g['project']}")
        for it in g["items"]:
            emoji = STATUS_EMOJI.get(it.get("status") or "", "")
            prefix = f"{emoji} " if emoji else ""
            out.append(f"- {prefix}{it['text']}")
        out.append("")
    return "\n".join(out).strip()
