"""expense — income entries, the credit side of the ledger.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Income list/add/delete and the income summary. Kept apart from expenses because income rows live in their own state list and never enter the expense log or its analytics.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.load_state / self._default_state / self.save_state (spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date
from emptyos.sdk import web_route
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import ExpenseApp  # noqa: F401 — for type hints only


# ─── Bind to ExpenseApp class as ────────────────────────────────
#   api_income_list     = _income.api_income_list
#   api_income_add      = _income.api_income_add
#   api_income_delete   = _income.api_income_delete
#   api_income_summary  = _income.api_income_summary
#   income_summary      = _income.income_summary
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/income")
async def api_income_list(self, request):
    """List income entries, filter by ?month= or ?year=."""
    state = self.load_state(self._default_state())
    entries = state.get("income", [])
    month = request.query_params.get("month")
    year = request.query_params.get("year")
    if month:
        entries = [e for e in entries if e["date"].startswith(month)]
    elif year:
        entries = [e for e in entries if e["date"].startswith(year)]
    entries.sort(key=lambda e: e["date"], reverse=True)
    return {
        "entries": entries,
        "total_gross": sum(e.get("gross", 0) for e in entries),
        "total_net": sum(e.get("net", e.get("gross", 0)) for e in entries),
    }


@web_route("POST", "/api/income")
async def api_income_add(self, request):
    """Record an income entry (salary, freelance, etc.)."""
    data = await request.json()
    gross = float(data.get("gross", 0))
    if gross <= 0:
        return {"error": "gross must be positive"}
    tax = float(data.get("tax", 0))
    entry = {
        "date": data.get("date", date.today().isoformat()),
        "gross": gross,
        "tax": tax,
        "super": float(data.get("super", 0)),
        "net": float(data.get("net", 0)) or (gross - tax),
        "source": data.get("source", ""),
        "type": data.get("type", "salary"),
        "note": data.get("note", ""),
    }
    state = self.load_state(self._default_state())
    state.setdefault("income", []).append(entry)
    self.save_state(state)
    await self.emit(
        "expense:income-added",
        {
            "date": entry["date"],
            "gross": entry["gross"],
            "net": entry["net"],
            "type": entry["type"],
        },
    )
    return {"ok": True, "entry": entry}


@web_route("DELETE", "/api/income")
async def api_income_delete(self, request):
    """Delete an income entry by date + gross."""
    data = await request.json()
    target_date = data.get("date", "")
    target_gross = float(data.get("gross", 0))
    state = self.load_state(self._default_state())
    before = len(state.get("income", []))
    state["income"] = [
        e
        for e in state.get("income", [])
        if not (e["date"] == target_date and abs(e.get("gross", 0) - target_gross) < 0.01)
    ]
    self.save_state(state)
    return {"ok": True, "deleted": before - len(state["income"])}


@web_route("GET", "/api/income/summary")
async def api_income_summary(self, request):
    """Monthly income totals."""
    month = request.query_params.get("month", date.today().strftime("%Y-%m"))
    return await self.income_summary(month=month)


async def income_summary(self, month: str = "") -> dict:
    """Callable: monthly income summary for finance app."""
    if not month:
        month = date.today().strftime("%Y-%m")
    state = self.load_state(self._default_state())
    entries = [e for e in state.get("income", []) if e["date"].startswith(month)]
    return {
        "month": month,
        "income_gross": round(sum(e.get("gross", 0) for e in entries), 2),
        "income_net": round(sum(e.get("net", e.get("gross", 0)) for e in entries), 2),
        "count": len(entries),
    }
