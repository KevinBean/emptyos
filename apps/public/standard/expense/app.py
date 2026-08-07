"""Expense — financial tracking.

Reads from vault expense logs (20_Areas/Finances/expense-log-YYYY.md).
New entries written to both vault (markdown table) and app-local JSON.
Vault is source of truth for historical data.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.utils import clamp_days

from . import analytics as _analytics
from . import income as _income
from . import recurring as _recurring
from .shared import _RECURRING_JOB

from . import statements as _statements
from .categories import (
    detect_category as _detect_category,
    parse_aa_split as _parse_aa_split,
)

TABLE_ROW = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*\$?([\d,.]+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|"
)


class ExpenseApp(BaseApp):

    # Statement import — column mapping + dedupe + preview/confirm gate.
    api_import_preview = _statements.api_import_preview

    api_import_confirm = _statements.api_import_confirm

    # Bound on how many missed periods one rule may back-post in a single run.
    MAX_CATCHUP_PERIODS = 60

    async def setup(self):
        await super().setup()
        self._register_recurring_schedule()

    async def teardown(self):
        self.remove_cron_job(_RECURRING_JOB)
        await super().teardown()

    def _log_dir(self) -> Path:
        return self.vault_config_path("log_dir", "20_Areas/Finances") or Path(".")

    def _log_path(self, year: int = 0) -> Path:
        y = year or date.today().year
        pattern = self.vault_config("pattern", "expense-log-*.md")
        filename = pattern.replace("*", str(y))
        return self._log_dir() / filename

    async def _append_row(self, path, row: str) -> None:
        """Read-modify-write append of one table row to the yearly ledger.

        NON-locking — the caller MUST hold ``self.note_lock(path)``.
        Two concurrent appends without the lock both read the pre-write content
        and the later write drops the earlier row (CLAUDE.md vault RMW race).
        """
        try:
            content = await self.read(str(path))  # noqa: eos-rmw  (leaf — caller holds write_lock)
        except FileNotFoundError:
            content = ""
        except Exception as e:
            print(f"[Expense] Failed to read vault log: {e}")
            content = ""
        content = content.rstrip() + ("\n" if content.strip() else "") + row + "\n"
        try:
            await self.write(str(path), content)
        except Exception as e:
            print(f"[Expense] Failed to write vault: {e}")

    def _parse_vault_expenses(self, content: str) -> list[dict]:
        """Parse expense table rows from vault markdown."""
        expenses = []
        for line in content.split("\n"):
            m = TABLE_ROW.match(line)
            if m:
                amount_str = m.group(2).replace(",", "")
                try:
                    amount = float(amount_str)
                except ValueError:
                    continue
                expenses.append(
                    {
                        "date": m.group(1),
                        "amount": amount,
                        "description": m.group(3).strip(),
                        "category": m.group(4).strip(),
                        "source": m.group(5).strip(),
                    }
                )
        return expenses

    async def list_expenses(self, month: str = "", year: int = 0) -> list[dict]:
        """Get expenses from vault. Optionally filter by month."""
        y = year or date.today().year
        path = self._log_path(y)
        try:
            content = await self.read(str(path))
        except Exception:
            return []
        expenses = self._parse_vault_expenses(content)
        if month:
            expenses = [e for e in expenses if e["date"].startswith(month)]
        return expenses

    def _summarize(self, expenses: list[dict]) -> dict:
        """Compute summary from expense list."""
        total = sum(e["amount"] for e in expenses)
        by_category = {}
        for e in expenses:
            cat = e["category"]
            by_category[cat] = by_category.get(cat, 0) + e["amount"]
        month = expenses[0]["date"][:7] if expenses else date.today().strftime("%Y-%m")
        return {
            "month": month,
            "total": round(total, 2),
            "count": len(expenses),
            "by_category": {
                k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda x: -x[1])
            },
        }

    async def timeline_items(self, days: int = 1) -> list[dict]:
        """Life-suite timeline contribution ([[contributes.life.timeline]]).

        One item per expense row over the last ``days`` days, most recent
        first. Item shape (suite contract, docs/suites/life-cohesion.md):
        {ts, title, kind, href} + amount/category extras. The expense log
        carries no per-row times, so ts is the row's date at midnight.
        """
        n = clamp_days(days)
        today = date.today()
        cutoff = (today - timedelta(days=n - 1)).isoformat()
        rows = await self.list_expenses()
        if cutoff[:4] != str(today.year):
            rows += await self.list_expenses(year=today.year - 1)
        items: list[dict] = []
        for e in rows:
            if not e.get("date") or e["date"] < cutoff or e["date"] > today.isoformat():
                continue
            items.append({
                "ts": f"{e['date']}T00:00:00",
                "title": f"${e['amount']:g} {e['description']}".strip(),
                "kind": "expense",
                "href": "/expense/",
                "amount": e["amount"],
                "category": e.get("category", ""),
            })
        items.sort(key=lambda x: x["ts"], reverse=True)
        return items

    @web_route("GET", "/api/timeline-items")
    async def api_timeline_items(self, request):
        return {"items": await self.timeline_items(days=request.query_params.get("days"))}

    async def summary(self, month: str = "") -> dict:
        """Callable: monthly expense summary. Used by finance, tracker, dashboard."""
        if not month:
            month = date.today().strftime("%Y-%m")
        expenses = await self.list_expenses(month=month)
        return self._summarize(expenses)

    async def voice_add_expense(
        self, amount: float = 0, description: str = "", category: str = ""
    ) -> dict:
        try:
            amt = float(amount)
        except (TypeError, ValueError):
            return {"say": "I didn't catch the amount."}
        if amt <= 0:
            return {"say": "I didn't catch the amount."}
        desc = (description or "").strip()
        if not desc:
            return {"say": "What was the expense for?"}
        cat = (category or "").strip() or _detect_category(desc)
        try:
            await self.add(amt, desc, cat)
        except Exception as e:
            return {"say": f"Couldn't log that — {e}"}
        return {
            "say": f"Logged ${amt:.2f} for {desc} under {cat}.",
            "link": {"text": "Open expenses", "href": "/expense/"},
        }

    async def voice_month_summary(self, month: str = "") -> dict:
        s = await self.summary(month)
        spoken_month = s.get("month") or "this month"
        try:
            spoken_month = date.fromisoformat(s["month"] + "-01").strftime("%B")
        except (KeyError, ValueError, TypeError):
            pass
        if not s.get("count"):
            return {"say": f"No expenses logged for {spoken_month} yet."}
        cats = s.get("by_category") or {}
        say = f"{s['count']} expenses totalling ${s['total']:.2f} in {spoken_month}."
        top = next(iter(cats), "")
        if top:
            say += f" Biggest category: {top} at ${cats[top]:.2f}."
        return {
            "say": say,
            "card": {
                "renderer": "entity-card",
                "data": {
                    "title": f"{s.get('month', '')} — ${s['total']:.2f}",
                    "subtitle": f"{s['count']} expenses",
                    "fields": [
                        {"label": k, "value": f"${v:.2f}"} for k, v in list(cats.items())[:8]
                    ],
                },
            },
            "link": {"text": "Open expenses", "href": "/expense/"},
        }

    async def panel_quick_add(self) -> dict:
        """Hub: inline quick-add form, posts to smart-add endpoint."""
        return {
            "icon": "💰",
            "title": "Add expense",
            "endpoint": "/expense/api/smart-add",
            "field": "text",
            "placeholder": "35 lunch coffee",
            "hint": "amount + description · 'aa 2' splits in half",
            "href": "/expense/",
        }

    async def panel_month_spend(self) -> dict | None:
        """Dashboard tile: this month's spend + entry count."""
        s = await self.summary()
        total = s.get("total", 0)
        count = s.get("count", 0)
        if not count:
            return None
        return self.stat_tile("💰", f"${total:,.0f}", f"{count} entries", "/expense/")

    async def panel_month_compare(self) -> list[dict] | None:
        """Month-vs-last compare tile for the hub.

        Shape follows the compare-tile renderer contract in hub.js:
        ``{label, now, prev, delta_pct}`` — the earlier ``{name, curr, prev,
        delta}`` shape was silently filtered out as unusable.
        """
        today = date.today()
        curr_month = today.strftime("%Y-%m")
        last = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        curr = await self.summary(curr_month)
        prev = await self.summary(last)
        curr_total = curr.get("total", 0) or 0
        prev_total = prev.get("total", 0) or 0
        if not (curr_total or prev_total):
            return None
        return [
            {
                "label": "Expenses",
                "now": f"${curr_total:,.0f}",
                "prev": f"${prev_total:,.0f}",
                "delta_pct": round((curr_total - prev_total) / prev_total * 100)
                if prev_total
                else None,
            }
        ]

    def _budget_setting(self) -> float:
        """Monthly budget — the ⚙ Settings value wins over legacy app state.

        The settings panel has always written ``expense.budget``; until this
        helper, the backend only ever read its own state file, so the panel
        was decorative.
        """
        raw = self.setting("expense.budget", None)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
        state = self.load_state({"budget": 3000, "presets": []})
        try:
            return float(state.get("budget", 3000))
        except (TypeError, ValueError):
            return 3000.0

    async def _maybe_budget_nudge(self, month: str) -> None:
        """Over-threshold proactive nudge, deduped per month. Fail-soft; ships
        dark behind the proactive master toggle like every nudge."""
        budget = self._budget_setting()
        if budget <= 0:
            return
        try:
            threshold = float(self.setting("expense.alert_threshold", 80))
        except (TypeError, ValueError):
            threshold = 80.0
        expenses = await self.list_expenses(month=month)
        total = sum(e["amount"] for e in expenses)
        if total >= budget * threshold / 100:
            pct = round(total / budget * 100)
            await self.proactive_notify(
                "expense-budget",
                f"Spending is at {pct}% of the ${budget:,.0f} monthly budget (${total:,.0f}).",
                dedup_key=f"expense-budget:{month}",
                link={"text": "Open Expense", "href": "/expense/"},
            )

    async def add(self, amount: float, description: str, category: str = "Other", entry_date: str = "") -> dict:
        """Add an expense to the vault log + local backup."""
        iso = entry_date or date.today().isoformat()
        entry = {
            "date": iso,
            "amount": amount,
            "description": description,
            "category": category,
            "source": "EmptyOS",
        }

        # Append to vault expense log under the per-year-file lock so concurrent
        # adds can't drop each other's row. _append_row handles the fresh-vault
        # case (it used to drop the very first expense in a brand-new vault).
        path = self._log_path(int(iso[:4]))
        row = f"| {entry['date']} | ${amount:.2f} | {description} | {category} | EmptyOS |"
        async with self.note_lock(path):
            await self._append_row(path, row)

        await self.emit("expense:added", entry)
        try:
            await self._maybe_budget_nudge(iso[:7])
        except Exception:
            pass  # the add must never fail because a nudge did
        return entry

    # ── Boards view-layer integration ──
    # Expenses have no stable storage id (markdown table rows), so the board id is
    # a composite: "<date>|<amount>|<description>". Edits route through the same
    # delete-then-add machinery as api_edit. Date/amount edits aren't allowed —
    # changing the date can shift the row to a different year file, and the rest
    # of the system treats those as different entries.
    SETTABLE_FIELDS = {"category", "description"}

    @staticmethod
    def _expense_id(e: dict) -> str:
        return f"{e.get('date', '')}|{float(e.get('amount', 0)):.2f}|{e.get('description', '')}"

    async def list_all(self) -> list[dict]:
        """Flat list shape consumed by boards when source.type == 'app'.
        Returns current-year expenses (most recent first), with a composite id."""
        rows = await self.list_expenses(year=date.today().year)
        rows.sort(key=lambda e: e.get("date", ""), reverse=True)
        out = []
        for e in rows:
            out.append(
                {
                    "id": self._expense_id(e),
                    "date": e.get("date", ""),
                    "amount": e.get("amount", 0),
                    "description": e.get("description", ""),
                    "category": e.get("category", "Other"),
                    "source": e.get("source", ""),
                }
            )
        return out

    async def set_field(self, id: str, field: str, value) -> dict:
        """Cross-app setter for the boards view layer. Implemented as delete+add
        so the markdown table stays the single source of truth."""
        if field not in self.SETTABLE_FIELDS:
            return {"error": f"field '{field}' not settable"}
        try:
            d, amt_str, desc = id.split("|", 2)
            amount = float(amt_str)
        except (ValueError, AttributeError):
            return {"error": "invalid expense id"}

        rows = await self.list_expenses(year=int(d[:4]))
        match = next((e for e in rows if self._expense_id(e) == id), None)
        if not match:
            return {"error": "Expense not found"}

        path = self._log_path(int(d[:4]))
        new_desc = value if field == "description" else match.get("description", "")
        new_cat = value if field == "category" else match.get("category", "Other")
        new_row = f"| {d} | ${amount:.2f} | {new_desc} | {new_cat} | EmptyOS |"
        # Remove the old row + append the updated one as ONE locked RMW. Must
        # not call the (also-locked) add() — that would deadlock on the same
        # key (asyncio.Lock is not reentrant).
        async with self.note_lock(path):
            try:
                content = await self.read(str(path))
            except Exception as exc:
                return {"error": str(exc)}
            target_marker = f"| {d} | ${amount:.2f}"
            new_lines = []
            removed = False
            for line in content.split("\n"):
                if not removed and line.startswith(target_marker) and desc in line:
                    removed = True
                    continue
                new_lines.append(line)
            if not removed:
                return {"error": "Expense row not found in vault"}
            base = "\n".join(new_lines).rstrip()
            await self.write(str(path), base + ("\n" if base else "") + new_row + "\n")

        await self.emit(
            "expense:added",
            {"date": d, "amount": amount, "description": new_desc,
             "category": new_cat, "source": "EmptyOS"},
        )
        return {"ok": True}

    @cli_command("expense", help="Track expenses")
    async def cmd_expense(
        self, action: str = "summary", amount: str = "", category: str = "", note: str = ""
    ):
        if action == "add" and amount:
            entry = await self.add(float(amount), note or "expense", category or "Other")
            self.print_rich(f"[green]Added:[/green] ${entry['amount']:.2f} [{entry['category']}]")
        elif action == "summary":
            month = date.today().strftime("%Y-%m")
            expenses = await self.list_expenses(month=month)
            s = self._summarize(expenses)
            print(f"\n  {s['month']}: ${s['total']:.2f} ({s['count']} entries)")
            for cat, amt in s["by_category"].items():
                print(f"    {cat:<20} ${amt:.2f}")
            print()
        elif action == "list":
            month = date.today().strftime("%Y-%m")
            expenses = await self.list_expenses(month=month)
            for e in expenses[-15:]:
                print(
                    f"  {e['date']}  ${e['amount']:>8.2f}  {e['category']:<16}  {e.get('description', '')[:30]}"
                )
        else:
            print("Usage: eos expense [add|summary|list] [amount] [category] [note]")

    @web_route("POST", "/api/add")
    async def api_add(self, request):
        data = await request.json()
        amount = float(data.get("amount", 0))
        desc = data.get("note", data.get("description", ""))
        category = data.get("category", "")

        # Auto-detect category if not provided
        if not category or category == "Other":
            category = _detect_category(desc)

        if amount <= 0:
            return {"error": "amount must be positive"}
        return await self.add(amount, desc, category, entry_date=data.get("date", ""))

    @web_route("POST", "/api/smart-add")
    async def api_smart_add(self, request):
        """Parse natural language: '35 lunch coffee' or '50 dinner AA 2'."""
        data = await request.json()
        # `or ""` not `, ""` — JSON null parses to None, and dict.get's default
        # only fires for absent keys (see feedback_yaml_get_method_crash).
        text = (data.get("text") or "").strip()
        if not text:
            return {"error": "text required"}

        # Check AA split first
        aa = _parse_aa_split(text)
        if aa:
            amount, desc = aa
            category = _detect_category(desc)
            return await self.add(amount, desc, category)

        # Parse: number + description
        m = re.match(r"^(\d+\.?\d*)\s+(.*)", text)
        if not m:
            return {"error": "format: amount description (e.g., '35 lunch coffee')"}

        amount = float(m.group(1))
        desc = m.group(2).strip()
        category = _detect_category(desc)

        if amount <= 0:
            return {"error": "amount must be positive"}
        return await self.add(amount, desc, category)

    @web_route("GET", "/api/summary")
    async def api_summary(self, request):
        month = request.query_params.get("month", date.today().strftime("%Y-%m"))
        expenses = await self.list_expenses(month=month)
        return self._summarize(expenses)

    @web_route("GET", "/api/list")
    async def api_list(self, request):
        month = request.query_params.get("month", "")
        limit = int(request.query_params.get("limit", "50"))
        expenses = await self.list_expenses(month=month)
        return expenses[-limit:]

    async def _delete_entry(self, target: dict) -> dict:
        """Delete an expense row matching date+amount from the vault log."""
        if not target.get("date") or not target.get("amount"):
            return {"error": "entry with date and amount required"}
        path = self._log_path(int(target["date"][:4]))
        async with self.note_lock(path):
            try:
                content = await self.read(str(path))
                lines = content.split("\n")
                new_lines = []
                removed = False
                for line in lines:
                    if (
                        not removed
                        and f"| {target['date']}" in line
                        and f"${float(target['amount']):.2f}" in line
                    ):
                        removed = True
                        continue
                    new_lines.append(line)
                if removed:
                    await self.write(str(path), "\n".join(new_lines))
                return {"deleted": removed}
            except Exception as e:
                return {"error": str(e)}

    @web_route("POST", "/api/delete")
    async def api_delete(self, request):
        """Delete an expense by matching date+amount+description."""
        data = await request.json()
        return await self._delete_entry(data.get("entry", {}))

    @web_route("POST", "/api/edit")
    async def api_edit(self, request):
        """Edit an expense: send original + updated."""
        data = await request.json()
        original = data.get("original", {})
        updated = data.get("updated", {})
        await self._delete_entry(original)
        return await self.add(
            float(updated.get("amount", original.get("amount", 0))),
            updated.get("description", original.get("description", "")),
            updated.get("category", original.get("category", "Other")),
            entry_date=updated.get("date", original.get("date", "")),
        )

    @web_route("GET", "/api/budget")
    async def api_get_budget(self, request):
        """Get monthly budget target (⚙ Settings value wins over app state)."""
        return {"budget": self._budget_setting()}

    @web_route("POST", "/api/budget")
    async def api_set_budget(self, request):
        data = await request.json()
        state = self.load_state({"budget": 3000, "presets": []})
        state["budget"] = float(data.get("amount", 3000))
        self.save_state(state)
        return {"budget": state["budget"]}

    @web_route("GET", "/api/presets")
    async def api_presets(self, request):
        """Get quick-log presets."""
        state = self.load_state({"budget": 3000, "presets": []})
        return state.get("presets", [])

    @web_route("POST", "/api/presets")
    async def api_set_presets(self, request):
        data = await request.json()
        state = self.load_state({"budget": 3000, "presets": []})
        state["presets"] = data.get("presets", [])
        self.save_state(state)
        return {"count": len(state["presets"])}

    def _default_state(self) -> dict:
        return {"budget": 3000, "presets": [], "recurring": [], "income": []}

    @web_route("GET", "/api/export")
    async def api_export(self, request):
        """Export current month expenses as CSV string."""
        month = request.query_params.get("month", date.today().strftime("%Y-%m"))
        expenses = await self.list_expenses(month=month)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["date", "amount", "description", "category", "source"])
        for e in expenses:
            writer.writerow(
                [
                    e["date"],
                    e["amount"],
                    e.get("description", ""),
                    e["category"],
                    e.get("source", ""),
                ]
            )
        return {"month": month, "count": len(expenses), "csv": buf.getvalue()}

    @web_route("POST", "/api/import")
    async def api_import(self, request):
        """Import expenses from CSV content.

        Body: {csv_content, source?}
        """
        data = await request.json()
        csv_content = data.get("csv_content", "")
        source = data.get("source", "import")
        if not csv_content:
            return {"error": "csv_content required"}

        reader = csv.DictReader(io.StringIO(csv_content))
        imported = []
        for row in reader:
            try:
                amount = float(row.get("amount", 0))
            except (ValueError, TypeError):
                continue
            if amount <= 0:
                continue
            entry = await self.add(amount, row.get("description", ""), row.get("category", "Other"))
            entry["source"] = source
            imported.append(entry)
        return {"ok": True, "imported": len(imported)}

    # ── Analytics (extracted to analytics.py) ──
    api_forecast         = _analytics.api_forecast
    api_heatmap          = _analytics.api_heatmap
    api_week_compare     = _analytics.api_week_compare
    api_category_trend   = _analytics.api_category_trend
    api_ytd              = _analytics.api_ytd
    api_daily_avg        = _analytics.api_daily_avg
    api_savings_goal     = _analytics.api_savings_goal
    api_set_savings_goal = _analytics.api_set_savings_goal
    _sql_breakdown       = _analytics._sql_breakdown
    api_insight          = _analytics.api_insight
    api_report_monthly   = _analytics.api_report_monthly

    # ── Income (extracted to income.py) ──
    api_income_list    = _income.api_income_list
    api_income_add     = _income.api_income_add
    api_income_delete  = _income.api_income_delete
    api_income_summary = _income.api_income_summary
    income_summary     = _income.income_summary

    # ── Recurring (extracted to recurring.py) ──
    _register_recurring_schedule = _recurring._register_recurring_schedule
    _scheduled_recurring_check   = _recurring._scheduled_recurring_check
    api_recurring_reschedule     = _recurring.api_recurring_reschedule
    api_get_recurring            = _recurring.api_get_recurring
    api_add_recurring            = _recurring.api_add_recurring
    api_recurring_check          = _recurring.api_recurring_check
    _run_recurring_check         = _recurring._run_recurring_check
    _advance_due                 = _recurring._advance_due
