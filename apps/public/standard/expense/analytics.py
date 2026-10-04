"""expense — spending analytics + the monthly report.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Every read-only derivation over the expense log: forecast, heatmap, week/month comparison, category trend, YTD, daily average, savings-goal progress, the optional DuckDB breakdown, and the two LLM surfaces (AI insight + monthly report). Owns EXPENSE_INSIGHT_SYSTEM, the prompt both LLM surfaces share.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.list_expenses / self._summarize (spine) for the underlying rows; self.load_state / self._default_state (spine) for budget + savings goal.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from emptyos.sdk import web_route
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import ExpenseApp  # noqa: F401 — for type hints only



EXPENSE_INSIGHT_SYSTEM = """You are a sharp personal finance analyst. Given a month's expense data, provide actionable insights.

## Structure
Return exactly:
1. **Pattern** (1 sentence): What's the dominant spending pattern this month?
2. **Anomaly** (1 sentence): What's unusual compared to what you'd expect? (largest single category, unexpected ratio)
3. **Blind spot** (1 sentence): What might be hiding in the data? (small daily purchases that compound, missing categories)
4. **One action** (1 sentence): The single highest-leverage change to reduce spending.
5. **Health score**: Rate 1-10 (1=crisis, 5=average, 10=excellent frugality).

## DO NOT:
- Say "consider tracking your expenses" — they already are.
- Give generic advice ("cook at home more") without connecting to the actual category data.
- Be judgmental about discretionary spending — note it, don't moralize.
- Pad with filler. Every sentence must contain a specific number or category name."""

# ─── Bind to ExpenseApp class as ────────────────────────────────
#   api_forecast          = _analytics.api_forecast
#   api_heatmap           = _analytics.api_heatmap
#   api_week_compare      = _analytics.api_week_compare
#   _sql_breakdown        = _analytics._sql_breakdown
#   api_insight           = _analytics.api_insight
#   api_report_monthly    = _analytics.api_report_monthly
#   api_category_trend    = _analytics.api_category_trend
#   api_ytd               = _analytics.api_ytd
#   api_savings_goal      = _analytics.api_savings_goal
#   api_set_savings_goal  = _analytics.api_set_savings_goal
#   api_daily_avg         = _analytics.api_daily_avg
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/forecast")
async def api_forecast(self, request):
    """Spending forecast for current month."""
    month = date.today().strftime("%Y-%m")
    expenses = await self.list_expenses(month=month)
    total = sum(e["amount"] for e in expenses)
    days_elapsed = max(1, date.today().day)
    days_in_month = (
        (
            date(date.today().year, date.today().month % 12 + 1, 1)
            - date(date.today().year, date.today().month, 1)
        ).days
        if date.today().month < 12
        else 31
    )
    daily_avg = total / days_elapsed
    forecast = daily_avg * days_in_month
    budget = self._budget_setting()
    return {
        "total": round(total, 2),
        "daily_avg": round(daily_avg, 2),
        "forecast": round(forecast, 2),
        "budget": budget,
        "budget_pct": round(total / budget * 100) if budget > 0 else 0,
        "on_track": forecast <= budget,
    }


@web_route("GET", "/api/heatmap")
async def api_heatmap(self, request):
    """Daily spending heatmap for last N months."""
    from datetime import timedelta

    months = int(request.query_params.get("months", "6"))
    start = date.today().replace(day=1)
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)

    all_expenses = await self.list_expenses(year=date.today().year)
    if start.year != date.today().year:
        all_expenses = await self.list_expenses(year=start.year) + all_expenses

    daily = {}
    for e in all_expenses:
        if e["date"] >= start.isoformat():
            daily[e["date"]] = daily.get(e["date"], 0) + e["amount"]
    return {"start": start.isoformat(), "data": {k: round(v, 2) for k, v in daily.items()}}


@web_route("GET", "/api/week-compare")
async def api_week_compare(self, request):
    """Compare this week vs last week spending."""
    from datetime import timedelta

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    last_week_start = week_start - timedelta(days=7)

    month = today.strftime("%Y-%m")
    expenses = await self.list_expenses(month=month)
    # Also get last month if week crosses boundary
    if week_start.month != today.month:
        expenses += await self.list_expenses(month=last_week_start.strftime("%Y-%m"))

    this_week = sum(e["amount"] for e in expenses if e["date"] >= week_start.isoformat())
    last_week = sum(
        e["amount"]
        for e in expenses
        if last_week_start.isoformat() <= e["date"] < week_start.isoformat()
    )
    diff = this_week - last_week
    return {
        "this_week": round(this_week, 2),
        "last_week": round(last_week, 2),
        "diff": round(diff, 2),
        "pct_change": round(diff / last_week * 100) if last_week > 0 else 0,
    }


def _sql_breakdown(self, expenses: list[dict]) -> dict | None:
    """SQL-shaped cuts of a month that are tedious in Python — via the
    text-first tabular layer (`.claude/rules/text-first-data.md`).

    Fail-soft: returns None when the dark flag is off, the `data` extra
    is missing, or anything raises — the insight prompt is then exactly
    what it was before this feature.
    """
    if not self.app_config("feature.sql-analytics.enabled", False):
        return None
    if not expenses:
        return None
    try:
        weekpart = self.query(
            "SELECT CASE WHEN dayofweek(CAST(date AS DATE)) IN (0, 6)"
            " THEN 'weekend' ELSE 'weekday' END AS part,"
            " ROUND(SUM(amount), 2) AS total, COUNT(*) AS n"
            " FROM e GROUP BY part ORDER BY part DESC",
            tables={"e": expenses},
        )
        top = self.query(
            "SELECT description, ROUND(SUM(amount), 2) AS total, COUNT(*) AS n"
            " FROM e GROUP BY description ORDER BY total DESC LIMIT 5",
            tables={"e": expenses},
        )
        blind_spots = self.query(
            "SELECT description, COUNT(*) AS n, ROUND(SUM(amount), 2) AS total"
            " FROM e GROUP BY description"
            " HAVING COUNT(*) >= 3 AND AVG(amount) < 15"
            " ORDER BY total DESC LIMIT 5",
            tables={"e": expenses},
        )
        for r in (weekpart, top, blind_spots):
            if isinstance(r, dict) and r.get("error"):
                self.log_warn(f"sql breakdown unavailable: {r['error']}")
                return None
        return {"weekday_vs_weekend": weekpart, "top_spends": top, "blind_spots": blind_spots}
    except Exception as e:
        self.log_warn(f"sql breakdown failed: {e}")
        return None


@web_route("GET", "/api/ai-insight")
async def api_insight(self, request):
    """AI analysis of spending patterns."""
    month = request.query_params.get("month", date.today().strftime("%Y-%m"))
    expenses = await self.list_expenses(month=month)
    s = self._summarize(expenses)
    user_msg = (
        f"Month: {s['month']}\n"
        f"Total: ${s['total']:.2f}, {s['count']} entries\n"
        f"Categories: {json.dumps(s['by_category'])}"
    )
    breakdown = self._sql_breakdown(expenses)
    if breakdown:
        user_msg += (
            f"\nWeekday vs weekend: {json.dumps(breakdown['weekday_vs_weekend'])}"
            f"\nTop spends: {json.dumps(breakdown['top_spends'])}"
            f"\nSmall repeat purchases (possible blind spots): {json.dumps(breakdown['blind_spots'])}"
        )
    result = await self.think(
        user_msg, system=EXPENSE_INSIGHT_SYSTEM, domain="text", temperature=0.4
    )
    return {"insight": result, "month": s["month"], "total": s["total"], "provenance": self.last_provenance()}


@web_route("POST", "/api/report/monthly")
async def api_report_monthly(self, request):
    """Write a monthly expense report into the vault (AI-authored snapshot).

    Body ``{month?: "YYYY-MM"}`` — defaults to the current month. Lands
    under the app's ``outputs/`` folder with ``author: ai`` +
    ``lifecycle: snapshot`` per the authorship-boundary rule. Only
    locally-computed aggregates reach the model (Rule 19).
    """
    data = await self.read_json(request)
    month = (data.get("month") or "").strip() or date.today().strftime("%Y-%m")
    try:
        prev_first = date.fromisoformat(month + "-01")
    except ValueError:
        return {"error": f"bad month '{month}' — expected YYYY-MM"}
    expenses = await self.list_expenses(month=month, year=int(month[:4]))
    if not expenses:
        return {"error": f"no expenses recorded in {month}"}
    s = self._summarize(expenses)
    prev_month = (prev_first - timedelta(days=1)).strftime("%Y-%m")
    prev_expenses = await self.list_expenses(month=prev_month, year=int(prev_month[:4]))
    prev = self._summarize(prev_expenses) if prev_expenses else {"total": 0, "by_category": {}}

    prev_cats = prev.get("by_category", {})
    cat_rows = ["| Category | This month | Last month | Δ |", "|---|---|---|---|"]
    for cat, amt in s["by_category"].items():
        p = prev_cats.get(cat, 0)
        d = amt - p
        cat_rows.append(
            f"| {cat} | ${amt:,.2f} | ${p:,.2f} | {'+' if d >= 0 else '−'}${abs(d):,.2f} |"
        )

    try:
        insight = await self.think(
            f"Month: {s['month']}\nTotal: ${s['total']:.2f}, {s['count']} entries\n"
            f"Last month total: ${prev.get('total', 0):.2f}\n"
            f"Categories: {json.dumps(s['by_category'])}",
            system=EXPENSE_INSIGHT_SYSTEM,
            domain="text",
            temperature=0.4,
        )
    except Exception:
        insight = "_Insight unavailable — no think provider reachable._"

    total_delta = s["total"] - (prev.get("total", 0) or 0)
    body = (
        f"## Summary\n\n"
        f"- Total: **${s['total']:,.2f}** across {s['count']} entries\n"
        f"- vs {prev_month}: ${prev.get('total', 0):,.2f} "
        f"({'+' if total_delta >= 0 else '−'}${abs(total_delta):,.2f})\n\n"
        f"## Categories\n\n" + "\n".join(cat_rows) + "\n\n"
        f"## Insight\n\n{insight}\n"
    )
    rel = self.save_report_note(
        f"{month}-expense-report.md",
        title=f"Expense report {month}",
        body=body,
        tags=["expense", "report"],
        extra={"month": month, "total": s["total"]},
    )
    await self.emit(
        "expense:report-saved", {"month": month, "total": s["total"], "path": rel}
    )
    return {"ok": True, "path": rel, "month": month, "total": s["total"]}


@web_route("GET", "/api/category-trend")
async def api_category_trend(self, request):
    """Per-category spend this month vs last month."""
    today = date.today()
    this_month = today.strftime("%Y-%m")
    first = today.replace(day=1)
    last_month_end = first - timedelta(days=1)
    last_month = last_month_end.strftime("%Y-%m")

    this_expenses = await self.list_expenses(month=this_month)
    last_expenses = await self.list_expenses(month=last_month)

    this_cats: dict[str, float] = {}
    for e in this_expenses:
        this_cats[e["category"]] = this_cats.get(e["category"], 0) + e["amount"]

    last_cats: dict[str, float] = {}
    for e in last_expenses:
        last_cats[e["category"]] = last_cats.get(e["category"], 0) + e["amount"]

    all_cats = sorted(set(this_cats) | set(last_cats))
    trends = []
    for cat in all_cats:
        t = round(this_cats.get(cat, 0), 2)
        l = round(last_cats.get(cat, 0), 2)
        trends.append(
            {
                "category": cat,
                "this_month": t,
                "last_month": l,
                "diff": round(t - l, 2),
            }
        )
    trends.sort(key=lambda x: -abs(x["diff"]))
    return {"this_month": this_month, "last_month": last_month, "trends": trends}


@web_route("GET", "/api/ytd")
async def api_ytd(self, request):
    """Year-to-date spending summary."""
    year = request.query_params.get("year", str(date.today().year))
    total = 0
    by_month: dict[str, float] = {}
    by_category: dict[str, float] = {}
    count = 0
    for m in range(1, 13):
        month_str = f"{year}-{m:02d}"
        expenses = await self.list_expenses(month=month_str)
        month_total = sum(e["amount"] for e in expenses)
        if month_total > 0:
            by_month[month_str] = round(month_total, 2)
            total += month_total
            count += len(expenses)
            for e in expenses:
                cat = e.get("category", "Other")
                by_category[cat] = by_category.get(cat, 0) + e["amount"]
    months_with_data = len(by_month)
    return {
        "year": year,
        "total": round(total, 2),
        "count": count,
        "monthly_avg": round(total / months_with_data, 2) if months_with_data else 0,
        "by_month": by_month,
        "by_category": {
            k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda x: -x[1])
        },
    }


@web_route("GET", "/api/savings-goal")
async def api_savings_goal(self, request):
    """Savings goal progress."""
    state = self.load_state({"savings_goal": 0, "savings_label": ""})
    return state


@web_route("POST", "/api/savings-goal")
async def api_set_savings_goal(self, request):
    """Set a savings goal."""
    data = await request.json()
    state = self.load_state({})
    state["savings_goal"] = float(data.get("goal", 0))
    state["savings_label"] = data.get("label", "Savings Target")
    self.save_state(state)
    return state


@web_route("GET", "/api/daily-avg")
async def api_daily_avg(self, request):
    """Daily spending average for current month."""
    today = date.today()
    expenses = await self.list_expenses(month=today.strftime("%Y-%m"))
    total = sum(e["amount"] for e in expenses)
    days_elapsed = today.day
    avg = round(total / days_elapsed, 2) if days_elapsed else 0
    projected = round(avg * 30, 2)
    return {
        "daily_avg": avg,
        "total_so_far": round(total, 2),
        "days_elapsed": days_elapsed,
        "projected_monthly": projected,
    }
