"""Pure aggregations over task dicts — no IO, no app state.

Inputs are the open/done lists produced by ``indexer.scan_vault`` /
``indexer.get_cached_or_scan``. Outputs are the shapes the web/hub/voice
surfaces consume verbatim.
"""

from __future__ import annotations

import random
import re
from datetime import date, timedelta

CONTEXT_KEYWORDS = {
    "career": ["career", "job", "resume", "interview", "application", "linkedin", "recruiter", "工作", "求职"],
    "immigration": ["visa", "189", "niw", "green card", "migration", "immiaccount", "pr ", "移民", "签证"],
    "health": ["health", "gym", "exercise", "zumba", "fitness", "medical", "doctor", "therapy", "healing", "健康", "锻炼"],
    "finance": ["expense", "tax", "ibkr", "invest", "salary", "budget", "rent", "房租", "报税", "投资"],
    "english": ["english", "speaking", "vocabulary", "ielts", "pte", "pronunciation", "口语"],
    "dev": ["emptyos", "app", "bug", "refactor", "endpoint", "plugin", "vault", "daemon"],
    "relationships": ["family", "mom", "dad", "friend", "call ", "reply", "message", "家人", "回复"],
    "admin": ["renew", "form", "account", "appointment", "book ", "register", "申请", "预约"],
}

_TAG_RE = re.compile(r"#([\w一-鿿]+)")
_RECUR_RE = re.compile(r"🔁\s*(daily|weekly|monthly|yearly|biweekly)")

# ── natural-language quick-add ───────────────────────────────────────────────
_QUICKADD_PRIORITY = {"p1": "highest", "p2": "high", "p3": "medium", "p4": "low", "p5": "lowest"}
_BANG_PRIORITY = {"!!": "high", "!!!": "highest"}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_IN_DAYS_RE = re.compile(r"\bin (\d{1,3}) days?\b", re.IGNORECASE)
# p1..p5 or !!/!!! only (single '!' would eat ordinary exclamations).
_PRIORITY_TOKEN_RE = re.compile(r"(?:^|\s)(p[1-5]|!{2,3})(?=\s|$)", re.IGNORECASE)


def parse_quick_add(text: str, today: date | None = None) -> dict:
    """Extract a due date + priority from a natural quick-add string.

    Recognizes (case-insensitive): an explicit YYYY-MM-DD, 'in N days',
    'today', 'tomorrow', 'next week', or a weekday name; and a priority token
    p1..p5 or !!/!!!. Consumed tokens are stripped from the text; #tags stay
    intact. Deterministic — no LLM. Returns ``{text, due, priority}`` where
    ``due`` is 'YYYY-MM-DD' or '' and ``priority`` is a level name or ''.
    """
    today = today or date.today()
    s = text or ""
    due = ""

    m = _ISO_DATE_RE.search(s)
    if m:
        due = m.group(1)
        s = s[: m.start()] + " " + s[m.end():]
    if not due:
        m = _IN_DAYS_RE.search(s)
        if m:
            due = (today + timedelta(days=int(m.group(1)))).isoformat()
            s = s[: m.start()] + " " + s[m.end():]
    if not due:
        for pat, delta in (("next week", 7), ("tomorrow", 1), ("today", 0)):
            m = re.search(r"\b" + pat + r"\b", s, re.IGNORECASE)
            if m:
                due = (today + timedelta(days=delta)).isoformat()
                s = s[: m.start()] + " " + s[m.end():]
                break
    if not due:
        for i, wd in enumerate(_WEEKDAYS):
            m = re.search(r"\b" + wd + r"\b", s, re.IGNORECASE)
            if m:
                ahead = (i - today.weekday()) % 7 or 7  # a weekday name is always in the future
                due = (today + timedelta(days=ahead)).isoformat()
                s = s[: m.start()] + " " + s[m.end():]
                break

    priority = ""
    pm = _PRIORITY_TOKEN_RE.search(s)
    if pm:
        tok = pm.group(1).lower()
        priority = _QUICKADD_PRIORITY.get(tok) or _BANG_PRIORITY.get(tok, "")
        if priority:
            s = s[: pm.start(1)] + s[pm.end(1):]

    clean = re.sub(r"\s+", " ", s).strip()
    return {"text": clean or (text or "").strip(), "due": due, "priority": priority}

# ── actionability (the primary organizing axis) ──────────────────────────
# Every open task is one of these. Inferred by default so a 3,000-task
# backlog never needs hand-tagging; an explicit #next/#someday/#waiting tag
# always overrides the inference.
ACTIONABILITY_STATES = ("next", "waiting", "someday")
_ACTION_TAGS = frozenset(ACTIONABILITY_STATES)
# Cues that a task is blocked on someone/something external. Kept tight to
# avoid over-matching — these are strong "not actionable by me right now"
# signals, not every mention of another person.
_WAITING_CUES = (
    "waiting", "awaiting", "blocked", "depends on", "pending reply",
    "follow up on", "followup on", "chase up", "等回复", "待回复", "等待", "等 ",
)


def classify_actionability(task: dict) -> str:
    """Infer a task's actionability state: ``next`` / ``waiting`` / ``someday``.

    Precedence: explicit ``#next``/``#waiting``/``#someday`` tag → an unmet
    project dependency (``blocked_by`` from projects.get_all_tasks) → waiting
    cue in text → has a due date (committed) → default ``someday`` (a bare
    capture). Pure + defensive — works on delegated task dicts that may lack
    ``tier``/``overdue_days``/``blocked_by``.
    """
    text = task.get("text", "") or ""
    tags = {t.lower() for t in _TAG_RE.findall(text)}
    for state in ("next", "waiting", "someday"):  # next wins over someday if both present
        if state in tags and state in _ACTION_TAGS:
            return state
    # A task with an unfinished project dependency is authoritatively waiting —
    # projects resolves depends_on/blocks; surface that instead of guessing.
    if task.get("blocked_by"):
        return "waiting"
    low = text.lower()
    if any(cue in low for cue in _WAITING_CUES):
        return "waiting"
    if (task.get("due") or "")[:10]:
        return "next"
    return "someday"


def someday_sample(someday: list[dict], n: int = 5, *, rng: random.Random | None = None) -> list[dict]:
    """Sample ``n`` someday tasks for a review ritual, biased toward the oldest.

    Draws from the stalest quarter of the backlog (min 4×n pool) so the
    2,000-item tail actually gets reviewed instead of whatever was captured
    last week, while still varying between calls.
    """
    if not someday:
        return []
    r = rng or random
    pool_size = max(4 * n, 20)
    oldest_first = sorted(someday, key=lambda t: (t.get("mtime") or 0.0, t.get("text", "")))
    pool = oldest_first[:pool_size]
    if len(pool) <= n:
        return pool
    return r.sample(pool, n)


def group_by_actionability(open_tasks: list[dict]) -> dict[str, list[dict]]:
    """Group open tasks by actionability, each list ordered for display.

    ``next`` by urgency (due date, then focus score); ``waiting`` by due then
    text; ``someday`` newest-capture-first (source-file mtime desc) so fresh
    ideas sit above the ancient backlog.
    """
    groups: dict[str, list[dict]] = {"next": [], "waiting": [], "someday": []}
    for t in open_tasks:
        groups[classify_actionability(t)].append(t)
    # urgency first (Taskwarrior-style), due date as tiebreaker — so a
    # high-priority or overdue task tops Next even without an exact date.
    groups["next"].sort(key=lambda t: (-t.get("focus_score", 0), (t.get("due") or "9999")[:10]))
    groups["waiting"].sort(key=lambda t: ((t.get("due") or "9999")[:10], t.get("text", "")))
    groups["someday"].sort(key=lambda t: (-t.get("mtime", 0.0), t.get("text", "")))
    return groups


# Markers stripped when normalizing a task line for duplicate detection.
_NORM_STRIP_RE = re.compile(
    r"📅\s*\d{4}-\d{2}-\d{2}|✅\s*\d{4}-\d{2}-\d{2}|🔁\s*\w+|#[\w一-鿿]+|[\[\]()📅✅🔁🌱🕸️❤️🗂️]"
)
_NORM_WS_RE = re.compile(r"\s+")


def _norm_task_text(text: str) -> str:
    """Collapse a task line to a comparison key — strips dates, tags, recur
    markers, bracket/emoji noise, wikilink brackets, case + whitespace."""
    s = (text or "").replace("[[", "").replace("]]", "")
    s = _NORM_STRIP_RE.sub(" ", s)
    s = _NORM_WS_RE.sub(" ", s).strip().casefold()
    return s


def detect_duplicates(tasks: list[dict]) -> list[list[dict]]:
    """Group tasks whose text collapses to the same normalized key.

    Returns only the groups with ≥2 members (the actual duplicates), each
    ordered by source line so the first occurrence is the keeper. Empty keys
    (a line that normalizes to nothing) are never grouped.
    """
    buckets: dict[str, list[dict]] = {}
    for t in tasks:
        key = _norm_task_text(t.get("text", ""))
        if not key:
            continue
        buckets.setdefault(key, []).append(t)
    dupes = [grp for grp in buckets.values() if len(grp) >= 2]
    for grp in dupes:
        grp.sort(key=lambda t: (t.get("file", ""), t.get("line", 0)))
    dupes.sort(key=len, reverse=True)
    return dupes


VALID_DISPOSITIONS = ("promote", "keep", "archive")


def map_dispositions(raw, tasks: list[dict]) -> list[dict]:
    """Map LLM disposition dicts back onto the indexed task batch.

    Each raw item is ``{"i": <int>, "disposition": "...", "reason": "..."}``.
    Drops anything with a bad index or an invalid disposition (defensive —
    the model can drift). Pure, no IO.
    """
    out = []
    for d in raw if isinstance(raw, list) else []:
        if not isinstance(d, dict):
            continue
        disp = str(d.get("disposition", "")).strip().lower()
        if disp not in VALID_DISPOSITIONS:
            continue
        try:
            idx = int(d.get("i"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(tasks):
            t = tasks[idx]
            out.append({
                "file": t.get("file", ""), "line": t.get("line", 0),
                "text": t.get("text", ""), "disposition": disp,
                "reason": str(d.get("reason", ""))[:200],
            })
    return out


def domain_balance(open_tasks: list[dict]) -> dict[str, int]:
    """Inferred life-domain counts for the wellbeing readout (NOT a picker).

    Each task counts toward the first matching domain (or ``other``). Mirrors
    ``group_by_context`` but over the wider CONTEXT_KEYWORDS set.
    """
    counts: dict[str, int] = {}
    for t in open_tasks:
        low = (t.get("text", "") or "").lower()
        domain = "other"
        for ctx, keywords in CONTEXT_KEYWORDS.items():
            if any(kw in low for kw in keywords):
                domain = ctx
                break
        counts[domain] = counts.get(domain, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def project_from_path(rel_path: str) -> str:
    """Tasks under ``10_Projects/<id>/...`` belong to ``<id>``; else ``inbox``."""
    if not rel_path:
        return "inbox"
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0].lower() in ("10_projects", "projects"):
        return parts[1]
    return "inbox"


def _days_until(due: str, today: date) -> int | None:
    if not due or len(due) < 10:
        return None
    try:
        return (date.fromisoformat(due[:10]) - today).days
    except (ValueError, TypeError):
        return None


# ── urgency (Taskwarrior-style single-number ranking) ────────────────────
# Obsidian Tasks priority markers → weights. Highest first so PRIORITY_RE's
# alternation prefers the strongest marker on a line.
_PRIORITY_WEIGHTS = [("🔺", 7), ("⏫", 6), ("🔼", 3), ("🔽", -1), ("⏬", -2)]
_PRIORITY_RE = re.compile("|".join(re.escape(m) for m, _ in _PRIORITY_WEIGHTS))


def priority_weight(text: str) -> int:
    """Weight of the strongest Obsidian-Tasks priority marker on a line (0 if none)."""
    m = _PRIORITY_RE.search(text or "")
    if not m:
        return 0
    return dict(_PRIORITY_WEIGHTS)[m.group(0)]


def compute_urgency(task: dict, today: date) -> int:
    """One ranking number folding due-proximity + priority + actionability.

    Replaces the dated-only ``focus_score`` (which returned 0 for any dateless
    task). Higher = more important; same ">90d-overdue stays low" anti-zombie
    shaping as the old formula, so very-stale tasks don't dominate. A dateless
    ``#next`` task now earns a real baseline (+6) so a promoted next-action
    ranks instead of sinking. Pure + defensive over delegated dicts.
    """
    u = 0.0
    text = task.get("text", "") or ""

    days = _days_until(task.get("due") or "", today)
    if days is not None:
        if days == 0:
            u += 12
        elif 0 < days <= 7:
            u += 8
        elif days > 7:
            u += 4  # future-scheduled
        else:  # overdue
            od = -days
            if od <= 14:
                u += 11
            elif od <= 90:
                u += 5
            else:
                u += 1  # very overdue → likely zombie, keep low

    u += priority_weight(text)

    act = task.get("actionability")
    if act == "next":
        u += 6
    elif act == "waiting":
        u -= 3

    low = text.lower()
    for keywords in CONTEXT_KEYWORDS.values():
        if any(kw in low for kw in keywords):
            u += 2
            break

    return round(max(u, 0.0))


def pulse_stats(open_tasks: list[dict], done_tasks: list[dict], today: date) -> list[dict]:
    today_str = today.isoformat()
    overdue = sum(1 for t in open_tasks if t.get("overdue_days", 0) > 0)
    due_today = sum(1 for t in open_tasks if (t.get("due") or "")[:10] == today_str)
    due_week = 0
    for t in open_tasks:
        d = _days_until(t.get("due") or "", today)
        if d is not None and 0 <= d <= 7:
            due_week += 1
    done_today = sum(1 for t in done_tasks if (t.get("done_date") or "")[:10] == today_str)
    return [
        {"value": overdue, "label": "Overdue", "tone": "red", "href": "/task/"},
        {"value": due_today, "label": "Today", "tone": "amber", "href": "/task/"},
        {"value": due_week, "label": "Week", "tone": "blue", "href": "/task/"},
        {"value": done_today, "label": "Done", "tone": "green", "href": "/task/"},
    ]


def todays_tasks_rows(open_tasks: list[dict], today: date, limit: int = 5) -> list[dict] | None:
    today_str = today.isoformat()

    def _bucket(t: dict) -> tuple:
        od = t.get("overdue_days", 0)
        if od > 0:
            return (0, -od)
        due = (t.get("due") or "")[:10]
        if due == today_str:
            return (1, -t.get("focus_score", 0))
        return (2, due or "9999")

    urgent = sorted(open_tasks, key=_bucket)[:limit]
    if not urgent:
        return None
    out = []
    for t in urgent:
        od = t.get("overdue_days", 0)
        due = (t.get("due") or "")[:10]
        if od > 0:
            tag, tag_tone = f"overdue {od}d", "overdue"
        elif due == today_str:
            tag, tag_tone = "today", "today"
        elif due:
            tag, tag_tone = due[5:], "week"
        else:
            tag, tag_tone = "", ""
        out.append(
            {
                "text": t["text"],
                "done": t.get("done", False),
                "tag": tag,
                "tag_tone": tag_tone,
                "href": "/task/",
            }
        )
    return out


def needs_attention_slot(open_tasks: list[dict], limit: int = 5) -> list[dict]:
    overdue = [t for t in open_tasks if t.get("overdue_days", 0) > 0]
    overdue.sort(key=lambda t: -t.get("overdue_days", 0))
    return [
        {
            "title": t["text"],
            "subtitle": f"overdue {t['overdue_days']}d",
            "href": "/task/",
            "badge": "overdue",
            "priority": min(100, 20 + t["overdue_days"]),
        }
        for t in overdue[:limit]
    ]


def due_today_slot(open_tasks: list[dict], today: date) -> list[dict]:
    today_str = today.isoformat()
    due_today = [t for t in open_tasks if (t.get("due") or "")[:10] == today_str]
    due_today.sort(key=lambda t: -t.get("focus_score", 0))
    return [
        {
            "title": t["text"],
            "subtitle": None,
            "href": "/task/",
            "badge": "due-today",
            "priority": t.get("focus_score", 0),
        }
        for t in due_today
    ]


def group_by_context(open_tasks: list[dict]) -> dict[str, list]:
    groups: dict[str, list] = {"other": []}
    for t in open_tasks:
        text_lower = t.get("text", "").lower()
        matched = False
        for ctx, keywords in CONTEXT_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                groups.setdefault(ctx, []).append(t)
                matched = True
                break
        if not matched:
            groups["other"].append(t)
    return groups


def agenda(open_tasks: list[dict], today: date) -> dict[str, list[dict]]:
    """Bucket open tasks into overdue / today / tomorrow / this-week / later / undated."""
    today_s = today.isoformat()
    tomorrow_s = (today + timedelta(days=1)).isoformat()
    week_end = today + timedelta(days=7)
    buckets: dict[str, list[dict]] = {
        "overdue": [], "today": [], "tomorrow": [],
        "this_week": [], "later": [], "undated": [],
    }
    for t in open_tasks:
        due = (t.get("due") or "")[:10]
        if not due:
            buckets["undated"].append(t)
            continue
        if due < today_s:
            buckets["overdue"].append(t)
        elif due == today_s:
            buckets["today"].append(t)
        elif due == tomorrow_s:
            buckets["tomorrow"].append(t)
        elif due <= week_end.isoformat():
            buckets["this_week"].append(t)
        else:
            buckets["later"].append(t)
    buckets["overdue"].sort(key=lambda t: t.get("due", ""))
    for k in ("today", "tomorrow", "this_week", "later"):
        buckets[k].sort(key=lambda t: (t.get("due", ""), -t.get("focus_score", 0)))
    return buckets


def group_by_date(open_tasks: list[dict], done_tasks: list[dict]) -> dict[str, list[dict]]:
    by_date: dict[str, list[dict]] = {}
    for t in open_tasks:
        d = t.get("due", "")
        if d:
            by_date.setdefault(d, []).append(t)
    for t in done_tasks:
        d = t.get("done_date", "") or t.get("due", "")
        if d:
            by_date.setdefault(d, []).append(t)
    return by_date


def top_focus(open_tasks: list[dict], limit: int = 3) -> list[dict]:
    scored = [t for t in open_tasks if t.get("focus_score", 0) > 0]
    scored.sort(key=lambda t: t["focus_score"], reverse=True)
    return scored[:limit]


def recurring_tasks(open_tasks: list[dict]) -> list[dict]:
    result = []
    for t in open_tasks:
        m = _RECUR_RE.search(t.get("text", ""))
        if m:
            result.append({**t, "frequency": m.group(1)})
    return result


def tag_counts(open_tasks: list[dict], done_tasks: list[dict]) -> dict[str, int]:
    tags: dict[str, int] = {}
    for t in open_tasks + done_tasks:
        for tag in _TAG_RE.findall(t.get("text", "")):
            tags[tag] = tags.get(tag, 0) + 1
    return dict(sorted(tags.items(), key=lambda x: -x[1]))


def stats(open_tasks: list[dict], done_tasks: list[dict], today: date) -> dict:
    today_str = today.isoformat()
    overdue = [t for t in open_tasks if t.get("overdue_days", 0) > 0]
    done_today = [t for t in done_tasks if t.get("done_date") == today_str]
    tiers: dict[str, int] = {}
    for t in open_tasks:
        tier = t.get("tier", "fresh")
        tiers[tier] = tiers.get(tier, 0) + 1
    return {
        "open": len(open_tasks),
        "done": len(done_tasks),
        "overdue": len(overdue),
        "done_today": len(done_today),
        "by_tier": tiers,
    }
