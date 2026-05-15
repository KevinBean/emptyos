"""Expense — financial tracking.

Reads from vault expense logs (20_Areas/Finances/expense-log-YYYY.md).
New entries written to both vault (markdown table) and app-local JSON.
Vault is source of truth for historical data.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, timedelta
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route

from .categories import (
    CATEGORY_KEYWORDS,
    detect_category as _detect_category,
    parse_aa_split as _parse_aa_split,
)

TABLE_ROW = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*\$?([\d,.]+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|"
)

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



class ExpenseApp(BaseApp):
    def _log_dir(self) -> Path:
        return self.vault_config_path("log_dir", "20_Areas/Finances") or Path(".")

    def _log_path(self, year: int = 0) -> Path:
        y = year or date.today().year
        pattern = self.vault_config("pattern", "expense-log-*.md")
        filename = pattern.replace("*", str(y))
        return self._log_dir() / filename

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

    async def summary(self, month: str = "") -> dict:
        """Callable: monthly expense summary. Used by finance, tracker, dashboard."""
        if not month:
            month = date.today().strftime("%Y-%m")
        expenses = await self.list_expenses(month=month)
        return self._summarize(expenses)

    # ── Hub panel contributions ──

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
        """Month-vs-last compare tile for the hub."""
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
                "name": "Expenses",
                "curr": f"${curr_total:,.0f}",
                "prev": f"${prev_total:,.0f}",
                "delta": curr_total - prev_total,
                "unit": "",
                "inverse": True,  # lower is better for spending
            }
        ]

    async def add(self, amount: float, description: str, category: str = "Other") -> dict:
        """Add an expense to the vault log + local backup."""
        today = date.today()
        entry = {
            "date": today.isoformat(),
            "amount": amount,
            "description": description,
            "category": category,
            "source": "EmptyOS",
        }

        # Append to vault expense log. Read first; on FileNotFoundError we
        # start a fresh log instead of silently swallowing the write — that
        # used to drop the very first expense in a brand-new vault.
        path = self._log_path()
        try:
            content = await self.read(str(path))
        except FileNotFoundError:
            content = ""
        except Exception as e:
            print(f"[Expense] Failed to read vault log: {e}")
            content = ""
        row = f"| {entry['date']} | ${amount:.2f} | {description} | {category} | EmptyOS |"
        content = content.rstrip() + ("\n" if content.strip() else "") + row + "\n"
        try:
            await self.write(str(path), content)
        except Exception as e:
            print(f"[Expense] Failed to write vault: {e}")

        await self.emit("expense:added", entry)
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
        await self.write(str(path), "\n".join(new_lines))

        new_desc = value if field == "description" else match.get("description", "")
        new_cat = value if field == "category" else match.get("category", "Other")
        await self.add(amount, new_desc, new_cat)
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
        return await self.add(amount, desc, category)

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

    # --- Enhanced endpoints (HP parity) ---

    @web_route("POST", "/api/delete")
    async def api_delete(self, request):
        """Delete an expense by matching date+amount+description."""
        data = await request.json()
        target = data.get("entry", {})
        if not target.get("date") or not target.get("amount"):
            return {"error": "entry with date and amount required"}
        path = self._log_path(int(target["date"][:4]))
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

    @web_route("POST", "/api/edit")
    async def api_edit(self, request):
        """Edit an expense: send original + updated."""
        data = await request.json()
        original = data.get("original", {})
        updated = data.get("updated", {})
        # Delete original, add updated
        await self.api_delete(type("R", (), {"json": lambda: {"entry": original}})())
        return await self.add(
            float(updated.get("amount", original.get("amount", 0))),
            updated.get("description", original.get("description", "")),
            updated.get("category", original.get("category", "Other")),
        )

    @web_route("GET", "/api/budget")
    async def api_get_budget(self, request):
        """Get monthly budget target."""
        state = self.load_state({"budget": 3000, "presets": []})
        return {"budget": state.get("budget", 3000)}

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
        state = self.load_state({"budget": 3000, "presets": []})
        budget = state.get("budget", 3000)
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
        result = await self.think(
            user_msg, system=EXPENSE_INSIGHT_SYSTEM, domain="text", temperature=0.4
        )
        return {"insight": result, "month": s["month"], "total": s["total"], "provenance": self.last_provenance()}

    # --- Recurring expenses ---

    def _default_state(self) -> dict:
        return {"budget": 3000, "presets": [], "recurring": [], "income": []}

    @web_route("GET", "/api/recurring")
    async def api_get_recurring(self, request):
        """List all recurring expense rules."""
        state = self.load_state(self._default_state())
        return state.get("recurring", [])

    @web_route("POST", "/api/recurring")
    async def api_add_recurring(self, request):
        """Add a recurring expense rule.

        Body: {text, frequency: weekly|fortnightly|monthly|yearly, enabled?}
        """
        data = await request.json()
        text = data.get("text", "")
        frequency = data.get("frequency", "monthly")
        if not text:
            return {"error": "text required"}
        if frequency not in ("weekly", "fortnightly", "monthly", "yearly"):
            return {"error": f"invalid frequency: {frequency}"}

        today = date.today()
        rule = {
            "text": text,
            "frequency": frequency,
            "next_due": today.isoformat(),
            "enabled": data.get("enabled", True),
            "last_logged": None,
            "created": today.isoformat(),
        }
        state = self.load_state(self._default_state())
        state.setdefault("recurring", []).append(rule)
        self.save_state(state)
        return {"ok": True, "rule": rule, "count": len(state["recurring"])}

    @web_route("POST", "/api/recurring/check")
    async def api_recurring_check(self, request):
        """Check and auto-log any due recurring expenses."""
        today = date.today()
        state = self.load_state(self._default_state())
        rules = state.get("recurring", [])
        logged = []

        for rule in rules:
            if not rule.get("enabled"):
                continue
            next_due = date.fromisoformat(rule["next_due"])
            if next_due > today:
                continue

            # Parse text into amount + description + category (same as HP: "700 rent weekly")
            parts = rule["text"].strip().split(None, 1)
            try:
                amount = float(parts[0])
                desc = parts[1] if len(parts) > 1 else "Recurring"
            except (ValueError, IndexError):
                continue

            entry = await self.add(amount, desc, "Recurring")
            logged.append(entry)
            rule["last_logged"] = today.isoformat()

            # Advance next_due
            freq = rule["frequency"]
            if freq == "weekly":
                rule["next_due"] = (next_due + timedelta(days=7)).isoformat()
            elif freq == "fortnightly":
                rule["next_due"] = (next_due + timedelta(days=14)).isoformat()
            elif freq == "monthly":
                m = next_due.month % 12 + 1
                y = next_due.year + (1 if next_due.month == 12 else 0)
                rule["next_due"] = next_due.replace(year=y, month=m).isoformat()
            elif freq == "yearly":
                rule["next_due"] = next_due.replace(year=next_due.year + 1).isoformat()

        self.save_state(state)
        return {"logged": logged, "count": len(logged)}

    # --- CSV export / import ---

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

    # --- Category trend ---

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

    # ── Income tracking ─────────────────────────────────────

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
