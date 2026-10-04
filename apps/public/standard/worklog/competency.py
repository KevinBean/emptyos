"""worklog — Engineers Australia Stage 2 competency evidence roll-up.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: reading `#cN` competency tags back out of logged work items and
aggregating them into a per-element evidence view, plus the hub panel that
surfaces which elements are still unevidenced, plus the propose→accept bridge
that makes tagging cheap: `suggest_competencies` reads a day's UNTAGGED items
and proposes elements (never writes), `tag_competency` is the accept half and
appends `#cN` to one item's text under the day lock.

Typing `#c11` by hand still works and stays the primary path — the suggester
exists because a mechanism nobody remembers to use carries no evidence: not a
single `#cN` tag existed anywhere in the 227-note corpus when this was added
(2026-09-07), so every element read as unevidenced.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._all_days`` (reads.py), ``self._daily_path``
/ ``self._daily_lock`` (app.py spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from emptyos.sdk.utils import parse_llm_json

from .parser import _parse_item, replace_section, split_sections
from .shared import (
    COMPETENCIES,
    COMPETENCY_AREAS,
    COMPETENCY_FOCUS,
    _parse_date,
    parse_competencies,
    strip_competency_tags,
)

if TYPE_CHECKING:
    from .app import WorklogApp  # noqa: F401 — for type hints only


# ─── Bind to WorklogApp class as ─────────────────────────────────────
#   competency_report     = _competency.competency_report
#   api_competency        = _competency.api_competency
#   panel_competency      = _competency.panel_competency
#   suggest_competencies  = _competency.suggest_competencies
#   tag_competency        = _competency.tag_competency
#   api_competency_suggest = _competency.api_competency_suggest
#   api_competency_tag    = _competency.api_competency_tag
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

# A window long enough that a quiet fortnight doesn't read as a gap, short
# enough to still be "current practice" for an assessor. Overridable per call.
DEFAULT_WINDOW_DAYS = 180

# At most this many elements per item. An item evidencing five competencies is
# the model padding, not a rich day — and an over-tagged corpus is worse than an
# untagged one, because an assessor reading it finds the same work under
# everything.
MAX_ELEMENTS_PER_ITEM = 3


def _cap_elements(elements: list[int]) -> list[int]:
    """At most ``MAX_ELEMENTS_PER_ITEM``, keeping the ones that matter.

    Capping with ``sorted(elements)[:3]`` truncates by element NUMBER, which
    preferentially discards 11 and 13 — precisely the two `shared.py` says can
    fail the application and cannot be back-filled. Rank by focus first (gap,
    then thin), and break ties on arrival order, which is the model's own
    ranking. The survivors are sorted only for stable rendering.
    """
    def rank(pair):
        i, n = pair
        kind = COMPETENCY_FOCUS.get(n, "")
        return (0 if kind == "gap" else 1 if kind == "thin" else 2, i)

    keep = sorted(enumerate(elements), key=rank)[:MAX_ELEMENTS_PER_ITEM]
    return sorted(n for _, n in keep)


def _clean_elements(raw) -> list[int]:
    """Element numbers from caller-supplied input, or [] if it is not a list.

    A bare string must NOT be accepted: `for n in "13"` iterates characters, so
    it would silently write `#c1 #c3` — two wrong competencies — and answer ok.
    `bool` is excluded for the same reason (`int(True) == 1`).
    """
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        return []
    out: list[int] = []
    for n in raw:
        if isinstance(n, bool) or not isinstance(n, (int, float, str)):
            continue
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if n in COMPETENCIES and n not in out:
            out.append(n)
    return out

COMPETENCY_SUGGEST_SYSTEM = (
    "You map an engineer's logged work items onto the Engineers Australia "
    "Stage 2 competency elements. You are proposing evidence for a Chartered "
    "(CPEng) application, so a wrong tag costs the applicant credibility.\n\n"
    "Return ONLY a JSON object: {\"proposals\": [{\"item\": int, "
    "\"elements\": [int], \"why\": str}]}\n"
    "item: the NUMBER of the work item, from the numbered list given.\n"
    "elements: element numbers from the list given, at most "
    f"{MAX_ELEMENTS_PER_ITEM}.\n"
    "why: one short clause naming what in the item is the evidence.\n\n"
    "Do NOT:\n"
    "- Include an item you are not confident about. Omitting an item is the "
    "correct answer far more often than guessing; an empty list is fine.\n"
    "- Invent an element number, or one outside the list.\n"
    "- Return an item number that is not in the list.\n"
    "- Tag routine admin (filing, emailing, submitting) as judgement or "
    "local engineering knowledge just because it happened at work.\n"
    "- Output prose, markdown, or a code fence around the JSON."
)


async def competency_report(
    self,
    *,
    since: str = "",
    until: str = "",
    employer: str = "",
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict:
    """Roll logged work items up by EA competency element.

    Callable via call_app("worklog", "competency_report", since=..., ...).
    Returns every one of the 16 elements, including those at zero — an element
    with no evidence is the finding, so it must not be filtered out of the list.
    """
    today = date.today()
    try:
        end = date.fromisoformat(until) if until else today
    except ValueError:
        end = today
    try:
        start = date.fromisoformat(since) if since else end - timedelta(days=max(1, window_days))
    except ValueError:
        start = end - timedelta(days=max(1, window_days))

    hits: dict[int, list[dict]] = {n: [] for n in COMPETENCIES}
    total_items = 0
    tagged_items = 0

    for day in await self._all_days(employer):
        d = day["date"]
        if not (start.isoformat() <= d <= end.isoformat()):
            continue
        for group in day["parsed"]["projects"]:
            for item in group["items"]:
                text = item.get("text") or ""
                total_items += 1
                elements = parse_competencies(text)
                if not elements:
                    continue
                tagged_items += 1
                for n in elements:
                    hits[n].append({
                        "date": d,
                        "project": group.get("project") or "General",
                        "text": strip_competency_tags(text),
                        "status": item.get("status"),
                    })

    areas = []
    for area, nums in COMPETENCY_AREAS.items():
        areas.append({
            "area": area,
            "elements": [{
                "n": n,
                "name": COMPETENCIES[n],
                "focus": COMPETENCY_FOCUS.get(n, ""),
                "count": len(hits[n]),
                # Newest first, capped — the panel wants a sample, not a dump.
                "items": sorted(hits[n], key=lambda x: x["date"], reverse=True)[:8],
            } for n in nums],
        })

    # The two elements that can fail the application, still at zero here.
    open_focus = [
        n for n, kind in sorted(COMPETENCY_FOCUS.items())
        if kind == "gap" and not hits[n]
    ]
    return {
        "since": start.isoformat(),
        "until": end.isoformat(),
        "employer": employer,
        "areas": areas,
        "covered": sum(1 for n in COMPETENCIES if hits[n]),
        "total_elements": len(COMPETENCIES),
        "tagged_items": tagged_items,
        "total_items": total_items,
        "open_focus": open_focus,
        "focus": {str(n): k for n, k in COMPETENCY_FOCUS.items()},
    }


@web_route("GET", "/api/competency")
async def api_competency(self, request):
    """Competency evidence roll-up. Query: since, until, employer, window_days."""
    q = request.query_params
    try:
        window = int(q.get("window_days") or DEFAULT_WINDOW_DAYS)
    except (TypeError, ValueError):
        window = DEFAULT_WINDOW_DAYS
    return await self.competency_report(
        since=(q.get("since") or "").strip(),
        until=(q.get("until") or "").strip(),
        employer=(q.get("employer") or "").strip(),
        window_days=window,
    )


async def panel_competency(self) -> list[dict] | None:
    """Hub panel — the focus elements still carrying no evidence.

    Returns None (panel disappears) once nothing is outstanding, so a healthy
    week shows no row rather than a permanent green badge nobody reads.
    """
    try:
        rep = await self.competency_report()
    except Exception:
        return None
    if not rep["open_focus"]:
        return None
    rows = []
    for n in rep["open_focus"]:
        rows.append({
            "title": f"Element {n} — {COMPETENCIES[n]}",
            "subtitle": "still no tagged evidence · tag a work item #c%d" % n,
            "href": "/worklog/#competency",
            "icon": "⚠",
        })
    return rows


# ── propose → accept ────────────────────────────────────────────────────────
# Split deliberately (.claude/rules/proposed-action.md): `suggest_competencies`
# writes nothing, `tag_competency` writes exactly what the user accepted. The
# model never reaches the vault.

def _untagged_items(day: dict) -> list[dict]:
    """The day's work items carrying no `#cN` yet, in document order."""
    out: list[dict] = []
    for group in day["parsed"]["projects"]:
        for item in group["items"]:
            text = item.get("text") or ""
            if not text.strip() or parse_competencies(text):
                continue
            out.append({"project": group.get("project") or "General", "text": text})
    return out


async def suggest_competencies(self, date_s: str = "", employer: str = "") -> dict:
    """Propose EA element tags for one day's untagged items. Never writes.

    Callable via call_app("worklog", "suggest_competencies", date_s=...).
    """
    d = _parse_date(date_s)
    target = d.isoformat()
    day = next((x for x in await self._all_days(employer) if x["date"] == target), None)
    if day is None:
        return {"ok": False, "date": target, "proposals": [], "error": "no worklog for that day"}

    pending = _untagged_items(day)
    if not pending:
        return {"ok": True, "date": target, "proposals": [], "reason": "every item is already tagged"}

    taxonomy = "\n".join(
        f"{n}. {name}" + (f"  [{COMPETENCY_FOCUS[n]} — evidence is short here]"
                          if n in COMPETENCY_FOCUS else "")
        for n, name in COMPETENCIES.items()
    )
    # Numbered, and addressed BY number in the reply. Asking the model to echo
    # the text back verbatim made two items with identical text in different
    # projects indistinguishable, and any reply that copied the rendered line
    # (project prefix included) failed to match at all — silently, as an empty
    # proposal list.
    listing = "\n".join(f"{i}. ({p['project']}) {p['text']}"
                        for i, p in enumerate(pending, 1))
    prompt = (
        f"Competency elements:\n{taxonomy}\n\n"
        f"Work items logged on {target} ({day.get('weekday', '')}):\n{listing}"
    )
    try:
        raw = await self.think(prompt, system=COMPETENCY_SUGGEST_SYSTEM,
                               domain="reason", temperature=0.2)
    except Exception as e:
        # A provider outage, a rate limit or an unapproved cloud-consent gate
        # is an ordinary outcome for a suggestion, not a server fault. Answering
        # in-band keeps the reason on screen (the page renders `error` verbatim)
        # instead of an opaque 500 — verified live against a 429.
        return {"ok": False, "date": target, "proposals": [],
                "error": f"the model could not be reached — {e}"}
    # fallback= is load-bearing: parse_llm_json RAISES by default, and a model
    # that answers in prose ("I can't do that") would otherwise 500 the route
    # rather than degrading to "nothing confidently maps".
    # fallback= is load-bearing: parse_llm_json RAISES by default, so a model
    # answering in prose would 500 the route. The isinstance guard is the other
    # half — parse_llm_json legitimately returns a LIST when the model omits the
    # wrapper object, and `.get` on a list is an AttributeError, i.e. the same
    # 500 by a different road.
    parsed = parse_llm_json(raw, fallback={})
    if not isinstance(parsed, dict):
        parsed = {}

    # Validate against what we actually sent. A proposal naming an item we did
    # not offer, or an element outside the standard, is dropped rather than
    # repaired — a silently "fixed" tag is the one an assessor would catch.
    proposals: list[dict] = []
    seen: set[int] = set()
    for row in (parsed.get("proposals") or []):
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("item"))
        except (TypeError, ValueError):
            continue
        if not (1 <= idx <= len(pending)) or idx in seen:
            continue
        elements = _clean_elements(row.get("elements"))
        if not elements:
            continue
        seen.add(idx)
        src = pending[idx - 1]
        proposals.append({
            "project": src["project"],
            "text": src["text"],
            "display": strip_competency_tags(src["text"]),
            "elements": _cap_elements(elements),
            "why": str(row.get("why") or "").strip(),
        })
    return {
        "ok": True,
        "date": target,
        "considered": len(pending),
        "proposals": proposals,
        "provenance": self.last_provenance(),
    }


async def tag_competency(self, date_s: str, project: str, item_text: str,
                         elements: list) -> dict:
    """Append `#cN` tags to one logged item — the accept half of the bridge."""
    d = _parse_date(date_s)
    wanted = _cap_elements(_clean_elements(elements))
    if not wanted:
        return {"error": "no valid element numbers"}

    async with self._daily_lock(d):
        try:
            content = await self.read(str(self._daily_path(d)))  # noqa: eos-rmw
        except Exception:
            return {"error": "no worklog for that day"}
        work = split_sections(content).get("Work", "")
        # Line-targeted, not a parse/render round trip. Appending a tag to one
        # item has no business rewriting the whole section, so this edits one
        # line and leaves every other byte alone — including a `> Logged time:`
        # blockquote sitting next to it. (The round trip used to DELETE those;
        # `render_work_preserving` fixed that for log_work / set_status / the
        # import merge, so this is now belt-and-braces rather than the only
        # safe path.)
        lines = work.split("\n")
        # parse_work synthesises "General" for items appearing before any
        # `### ` heading (parser.py), and the suggester proposes them under that
        # name — so accepting one has to resolve the same way, or the proposal
        # is a dead end that answers "item not found".
        here = "General"
        new_text = ""
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("### "):
                here = stripped[4:].strip()
                continue
            if not stripped.startswith("- ") or here.lower() != (project or "").strip().lower():
                continue
            item = _parse_item(stripped)
            if item["text"].strip() != (item_text or "").strip():
                continue
            # Already-present tags are kept, not duplicated — accepting the
            # same proposal twice must be a no-op, not `#c11 #c11`.
            add = [n for n in wanted if n not in parse_competencies(item["text"])]
            new_text = item["text"]
            if not add:
                # Every tag is already there. Returning before the write matters:
                # replace_section re-flows the section's blank lines, so a "no-op"
                # that still wrote would silently reformat hand-written markdown
                # and emit a second event for a change that did not happen.
                return {"ok": True, "date": d.isoformat(), "text": new_text,
                        "elements": parse_competencies(new_text), "unchanged": True}
            new_text = new_text.rstrip() + " " + " ".join(f"#c{n}" for n in add)
            prefix = f"{item['emoji']} " if item["emoji"] else ""
            lines[idx] = f"- {prefix}{new_text}"
            break
        if not new_text:
            return {"error": "item not found"}
        await self.write(str(self._daily_path(d)),
                         replace_section(content, "Work", "\n".join(lines)))
    await self.emit("worklog:competency-tagged",
                    {"date": d.isoformat(), "project": project, "elements": wanted})
    return {"ok": True, "date": d.isoformat(), "text": new_text,
            "elements": parse_competencies(new_text)}


@web_route("POST", "/api/competency/suggest")
async def api_competency_suggest(self, request):
    data = await self.read_json(request)
    return await self.suggest_competencies(
        date_s=(data.get("date") or "").strip(),
        employer=(data.get("employer") or "").strip(),
    )


@web_route("POST", "/api/competency/tag")
async def api_competency_tag(self, request):
    data = await self.read_json(request)
    return await self.tag_competency(
        (data.get("date") or "").strip(),
        data.get("project") or "",
        data.get("item") or "",
        data.get("elements") or [],
    )
