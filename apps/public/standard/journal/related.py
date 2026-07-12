"""journal — semantic 'you wrote about this before' related-entry surface.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The embedding-cosine related-entries endpoint and the journal-corpus collector it walks. Source of truth for /api/related and _collect_journal_entries.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._daily_path + self.recall/self.embeddings_available (spine/BaseApp); the _RELATED_* tuning constants stay class attributes on the spine.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from .parser import extract_section, parse_entries

if TYPE_CHECKING:
    from .app import JournalApp  # noqa: F401 — for type hints only


# ─── Bind to JournalApp class as ────────────────────────────────
#   api_related               = _related.api_related
#   _collect_journal_entries  = _related._collect_journal_entries
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/related")
async def api_related(self, request):
    """GET /journal/api/related?text=...&top_k=5

    Or pass `date=YYYY-MM-DD` (and optional `index=N`) to use one of that
    day's existing entries as the query — the post-write nudge.
    """
    text = (request.query_params.get("text") or "").strip()
    date_str = (request.query_params.get("date") or "").strip()
    index = int(request.query_params.get("index", "0"))
    top_k = int(request.query_params.get("top_k", "5"))

    if not text and date_str:
        try:
            d = date.fromisoformat(date_str)
            content = await self.read(str(self._daily_path(d)))
            entries = parse_entries(content)
            if 0 <= index < len(entries):
                text = entries[index]["text"]
        except Exception:
            pass

    if not text or len(text) < self._RELATED_MIN_CHARS:
        return {"text": text, "results": [], "reason": "query too short"}

    if not self.embeddings_available:
        return {"text": text, "results": [], "reason": "embeddings unavailable"}

    items = await self._collect_journal_entries(query_text=text)
    if not items:
        return {"text": text, "results": []}

    # Reuses the shared BaseApp.recall() scorer. Pure-similarity weights
    # (1,0,0) reproduce the prior `index.search` ordering exactly; the
    # recency + mood-salience terms are available here but left off so
    # "related entries" stays a semantic-nearest view (opt into recency
    # by passing weights=(1.0, 0.3, 0.1) if that's ever wanted).
    ranked = await self.recall(
        text,
        items=items,
        text_fn=lambda it: it["text"],
        top_k=top_k + 1,
        weights=(1.0, 0.0, 0.0),
        min_sim=self._RELATED_MIN_SCORE,
    )
    # Drop the entry that exactly matches the query (same date+index path)
    out = []
    for it in ranked:
        if it["text"].strip() == text.strip():
            continue
        out.append(
            {
                "date": it["date"],
                "mood": it.get("mood", ""),
                "emoji": it.get("emoji", ""),
                "text": it["text"],
                "score": round(it["sim"], 3),
            }
        )
        if len(out) >= top_k:
            break
    return {"text": text, "results": out}


async def _collect_journal_entries(self, query_text: str = "") -> list[dict]:
    """Walk the journal vault folder and return every entry as a dict.

    `query_text` is unused today but reserved — a future optimization
    could pre-filter by year/keyword to avoid embedding the whole
    history when only a slice is plausibly relevant.
    """
    from datetime import timedelta as _td

    items: list[dict] = []
    today = date.today()
    cutoff = today - _td(days=self._RELATED_LOOKBACK_DAYS)

    # Walk by year folder — `_daily_path` formats as 50_Journal/{year}/{date}.md
    # so we just iterate the year range.
    years_seen: set[int] = set()
    d = cutoff
    while d <= today:
        years_seen.add(d.year)
        d += _td(days=365)

    for year in sorted(years_seen):
        year_dir = self._daily_path(date(year, 1, 1)).parent
        if not year_dir.exists():
            continue
        for md_path in sorted(year_dir.glob("*.md")):
            stem = md_path.stem  # YYYY-MM-DD
            try:
                d_parsed = date.fromisoformat(stem)
            except ValueError:
                continue
            if d_parsed < cutoff or d_parsed > today:
                continue
            try:
                content = md_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, e in enumerate(parse_entries(content)):
                text = (e.get("text") or "").strip()
                if len(text) < self._RELATED_MIN_CHARS:
                    continue
                # Skip Playwright test fixtures — they leak into the journal
                # via the system test suite.
                if "PLAYWRIGHT-TEST" in text:
                    continue
                items.append(
                    {
                        "id": f"{stem}:{i}",
                        "date": stem,
                        "mood": e.get("mood", ""),
                        "emoji": e.get("emoji", ""),
                        "text": text,
                    }
                )
            # Also pull substantive prose from Milestone + Three Things —
            # the Journal section is mostly auto-populated breadcrumbs;
            # the user's reflective writing lives in these sections.
            for header, kind in (
                ("### Milestone", "milestone"),
                ("#### Three successful things", "three-things"),
            ):
                body = extract_section(content, header).strip()
                if not body or "PLAYWRIGHT-TEST" in body:
                    continue
                if len(body) < self._RELATED_MIN_CHARS:
                    continue
                items.append(
                    {
                        "id": f"{stem}:{kind}",
                        "date": stem,
                        "mood": "",
                        "emoji": "",
                        "kind": kind,
                        "text": body,
                    }
                )
    return items
