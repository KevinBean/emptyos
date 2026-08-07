"""worklog — free-text search + the untagged backlog.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns AND-over-terms substring search across work items and
Plan/Update prose, plus the ``status=none`` sentinel that surfaces untagged
items, the result cap, and the untagged-status vocabulary.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._all_days (reads) for the corpus.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from emptyos.sdk import web_route
from .parser import looks_like_import_debris
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import WorklogApp  # noqa: F401 — for type hints only


# Ceiling on search results returned in one response. The overflow is reported
# as `truncated` so a narrow-your-query hint can be shown rather than silently
# handing back a subset that looks complete.
SEARCH_MAX_RESULTS = 200

# Sentinel for "items carrying no status emoji". 81% of this corpus is
# untagged, which makes it invisible to the hub panels, the blocked/review
# rollup and the calendar tone — a plain status value can't express "absent".
UNTAGGED_STATUS = "none"


# ─── Bind to WorklogApp class as ────────────────────────────────
#   search_items  = _search.search_items
#   api_search    = _search.api_search
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def search_items(
    self, q: str = "", *, employer: str = "", status: str = "",
    project: str = "", start: str = "", end: str = "", limit: int = SEARCH_MAX_RESULTS,
    hide_debris: bool = False,
) -> dict:
    """Free-text search across work items and Plan/Update prose.

    AND over whitespace-separated terms, case-insensitive substring — the
    corpus is ~1.5k short items, so a real index would be machinery without
    a payoff, and substring beats stemming on strings like "45007" or
    "TB 908". Prose is searched too: the Plan is written on 80% of days and
    is often the only place a decision is recorded.

    Public so other apps can reach it via ``call_app``.
    """
    terms = [t for t in (q or "").lower().split() if t]
    status, project = status.strip().lower(), project.strip().lower()
    # A filter alone is a valid search: browsing every untagged item is the
    # whole point of the status backlog pass, and it has no query.
    if not terms and not (status or project):
        return {"error": "query required"}
    want_untagged = status == UNTAGGED_STATUS
    limit = max(1, min(int(limit or SEARCH_MAX_RESULTS), SEARCH_MAX_RESULTS))
    hits: list[dict] = []
    total = 0
    hidden = 0

    def _matches(*fields: str) -> bool:
        hay = " ".join(f.lower() for f in fields if f)
        return all(t in hay for t in terms)

    for day in await self._all_days(employer):
        d = day["date"]
        if (start and d < start) or (end and d > end):
            continue
        parsed = day["parsed"]
        base = {"date": d, "weekday": day["weekday"], "employer": day["employer"]}
        for g in parsed["projects"]:
            proj = g["project"]
            if project and project not in proj.lower():
                continue
            for it in g["items"]:
                st = it.get("status") or ""
                if status and (bool(st) if want_untagged else st != status):
                    continue
                # Project name counts as haystack: "cablehv modelling" should
                # find an item whose text alone says only "modelling".
                if not _matches(it["text"], proj):
                    continue
                # Opt-in only, and it filters the VIEW — the note is never
                # touched, so a wrongly-hidden item is one toggle away.
                if hide_debris and looks_like_import_debris(it["text"]):
                    hidden += 1
                    continue
                total += 1
                if len(hits) < limit:
                    hits.append({**base, "kind": "item", "project": proj,
                                 "status": st, "text": it["text"]})
        # Prose carries no status/project, so those filters exclude it.
        if status or project:
            continue
        for kind in ("plan", "update"):
            body = (parsed.get(kind) or "").strip()
            if body and _matches(body):
                total += 1
                if len(hits) < limit:
                    hits.append({**base, "kind": kind, "project": "",
                                 "status": "", "text": body})
    return {"query": q, "terms": terms, "count": total,
            "truncated": max(0, total - len(hits)),
            # Report what the filter removed — a quietly shortened list
            # reads as "that is all there is".
            "hidden_debris": hidden, "results": hits}


@web_route("GET", "/api/search")
async def api_search(self, request):
    p = request.query_params
    return await self.search_items(
        p.get("q", ""), employer=p.get("employer", ""), status=p.get("status", ""),
        project=p.get("project", ""), start=p.get("from", ""), end=p.get("to", ""),
        hide_debris=p.get("hide_debris") in ("1", "true", "yes"),
    )
