"""Billing — LLM + image generation cost tracking.

Listens to think:executed and studio:generated events.
Persists daily usage stats to JSON. Supports budget alerts.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from emptyos.sdk import BaseApp, cli_command, on_event, today_iso, web_route

# Default cost per 1K tokens (input+output averaged)
_DEFAULT_RATES = {
    "ollama": 0.0,
    "claude-cli": 0.0,
    "openai": 0.0006,
}

# Image generation costs
_IMAGE_COSTS = {
    "comfyui": 0.0,  # local GPU, free
    "openai-image": 0.008,  # gpt-image-2, ~$8/1M img tokens (est. ~0.008/image)
    "dalle": 0.04,  # DALL-E 3, deprecated May 2026
}


COST_INSIGHT_SYSTEM = """You are a cost analyst reviewing the user's LLM usage.

Give 2-3 brief, actionable cost-optimization tips grounded in the data shown.
Each tip is one sentence. Name the specific provider or app the tip targets.

Do NOT:
- Invent numbers, providers, or apps the data doesn't mention.
- Propose generic advice ("monitor your usage", "consider rate limiting") with no anchor in the data.
- Recommend cloud upgrades or paid tools — the user runs local providers by default.
- Restate totals back at the user — they can see them.
- Hedge ("you might want to maybe consider…"); state the recommendation directly.
- Add greetings, caveats about being an AI, or markdown headers.
"""

COST_INSIGHT_USER_TMPL = (
    "{context}\n\nGive 2-3 cost-optimization tips. One sentence each."
)


class BillingApp(BaseApp):
    async def setup(self):
        await super().setup()
        self._init_billing_db()

    def _init_billing_db(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT NOT NULL,
                calls INTEGER NOT NULL DEFAULT 0,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0.0,
                images INTEGER NOT NULL DEFAULT 0,
                image_cost REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (date)
            );
            CREATE TABLE IF NOT EXISTS provider_stats (
                date TEXT NOT NULL,
                provider TEXT NOT NULL,
                calls INTEGER NOT NULL DEFAULT 0,
                tokens INTEGER NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (date, provider)
            );
            CREATE TABLE IF NOT EXISTS app_stats (
                date TEXT NOT NULL,
                app TEXT NOT NULL,
                calls INTEGER NOT NULL DEFAULT 0,
                tokens INTEGER NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (date, app)
            );
        """)
        self.db.commit()
        # Migrate from JSON if exists and DB is empty
        json_path = self.data_dir / "daily_stats.json"
        if (
            json_path.exists()
            and self.db.execute("SELECT COUNT(*) FROM daily_stats").fetchone()[0] == 0
        ):
            self._migrate_from_json(json_path)

    def _migrate_from_json(self, json_path):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            for day_str, day in data.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO daily_stats VALUES (?,?,?,?,?,?,?,?)",
                    (
                        day_str,
                        day.get("calls", 0),
                        day.get("prompt_tokens", 0),
                        day.get("completion_tokens", 0),
                        day.get("total_tokens", 0),
                        day.get("cost", 0.0),
                        day.get("images", 0),
                        day.get("image_cost", 0.0),
                    ),
                )
                for p, pd in day.get("by_provider", {}).items():
                    self.db.execute(
                        "INSERT OR IGNORE INTO provider_stats VALUES (?,?,?,?,?)",
                        (day_str, p, pd.get("calls", 0), pd.get("tokens", 0), pd.get("cost", 0.0)),
                    )
                for a, ad in day.get("by_app", {}).items():
                    self.db.execute(
                        "INSERT OR IGNORE INTO app_stats VALUES (?,?,?,?,?)",
                        (day_str, a, ad.get("calls", 0), ad.get("tokens", 0), ad.get("cost", 0.0)),
                    )
            self.db.commit()
            json_path.rename(json_path.with_suffix(".json.bak"))
        except Exception as e:
            print(f"[Billing] JSON migration failed: {e}")

    def _ensure_day(self, day: str):
        self.db.execute("INSERT OR IGNORE INTO daily_stats (date) VALUES (?)", (day,))

    def _cost_rates(self) -> dict:
        rates = dict(_DEFAULT_RATES)
        try:
            custom = self.kernel.config.get("billing.custom_rates", {})
            if isinstance(custom, dict):
                rates.update(custom)
        except Exception:
            pass
        return rates

    # ── Event listeners ───────────────────────────────────────

    @on_event("think:executed")
    async def on_think(self, event):
        """Track every LLM call with real token counts when available."""
        d = event.data
        today = today_iso()
        self._ensure_day(today)

        provider = d.get("provider", "unknown")
        app = d.get("app", "unknown")

        pt = d.get("prompt_tokens", 0)
        ct = d.get("completion_tokens", 0)
        real_cost = d.get("cost", 0)

        if not pt and not ct:
            prompt_len = d.get("prompt_len", 0)
            pt = prompt_len // 4
            ct = pt // 2
            rates = self._cost_rates()
            real_cost = (pt + ct) / 1000 * rates.get(provider, 0)

        self.db.execute(
            """
            UPDATE daily_stats SET calls = calls + 1,
                prompt_tokens = prompt_tokens + ?, completion_tokens = completion_tokens + ?,
                total_tokens = total_tokens + ?, cost = round(cost + ?, 6)
            WHERE date = ?
        """,
            (pt, ct, pt + ct, real_cost, today),
        )

        self.db.execute(
            """
            INSERT INTO provider_stats (date, provider, calls, tokens, cost) VALUES (?,?,1,?,?)
            ON CONFLICT(date, provider) DO UPDATE SET
                calls = calls + 1, tokens = tokens + ?, cost = round(cost + ?, 6)
        """,
            (today, provider, pt + ct, real_cost, pt + ct, real_cost),
        )

        self.db.execute(
            """
            INSERT INTO app_stats (date, app, calls, tokens, cost) VALUES (?,?,1,?,?)
            ON CONFLICT(date, app) DO UPDATE SET
                calls = calls + 1, tokens = tokens + ?, cost = round(cost + ?, 6)
        """,
            (today, app, pt + ct, real_cost, pt + ct, real_cost),
        )

        self.db.commit()

        # Budget alert
        budget = self._get_budget()
        if budget > 0:
            row = self.db.execute(
                "SELECT cost, image_cost FROM daily_stats WHERE date = ?", (today,)
            ).fetchone()
            total = (row["cost"] or 0) + (row["image_cost"] or 0) if row else 0
            if total > budget:
                await self.emit(
                    "billing:budget_alert",
                    {
                        "total": round(total, 4),
                        "budget": budget,
                        "date": today,
                    },
                )
                notif = self.service("notifications")
                if notif:
                    await notif.send(
                        f"Daily budget exceeded: ${total:.4f} > ${budget:.2f}",
                        priority="warning",
                        source="billing",
                    )

    @on_event("studio:generated")
    async def on_image(self, event):
        """Track image generation costs."""
        d = event.data
        backend = d.get("backend", "comfyui")
        cost = _IMAGE_COSTS.get(backend, 0)
        today = today_iso()
        self._ensure_day(today)
        self.db.execute(
            """
            UPDATE daily_stats SET images = images + 1,
                image_cost = round(image_cost + ?, 6) WHERE date = ?
        """,
            (cost, today),
        )
        self.db.commit()

    # ── Budget ────────────────────────────────────────────────

    def _get_budget(self) -> float:
        state = self.load_state({"daily_budget": 0})
        return float(state.get("daily_budget", 0))

    @web_route("GET", "/api/budget")
    async def api_get_budget(self, request):
        return {"daily_budget": self._get_budget()}

    @web_route("POST", "/api/budget")
    async def api_set_budget(self, request):
        body = await request.json()
        state = self.load_state({})
        state["daily_budget"] = float(body.get("daily_budget", 0))
        self.save_state(state)
        return {"ok": True, "daily_budget": state["daily_budget"]}

    # ── API ───────────────────────────────────────────────────

    def _get_day(self, day: str) -> dict:
        """Get a day's stats as a dict (for API compatibility)."""
        row = self.db.execute("SELECT * FROM daily_stats WHERE date = ?", (day,)).fetchone()
        if not row:
            return {}
        result = dict(row)
        result["by_provider"] = {
            r["provider"]: {"calls": r["calls"], "tokens": r["tokens"], "cost": r["cost"]}
            for r in self.db.execute(
                "SELECT * FROM provider_stats WHERE date = ?", (day,)
            ).fetchall()
        }
        result["by_app"] = {
            r["app"]: {"calls": r["calls"], "tokens": r["tokens"], "cost": r["cost"]}
            for r in self.db.execute("SELECT * FROM app_stats WHERE date = ?", (day,)).fetchall()
        }
        return result

    async def today_summary(self) -> dict:
        """Today's usage summary — safe to call via call_app()/[DO:]."""
        today = self._get_day(today_iso())
        budget = self._get_budget()
        total_cost = today.get("cost", 0) + today.get("image_cost", 0)
        return {
            **today,
            "date": today_iso(),
            "total_cost": round(total_cost, 6),
            "budget": budget,
            "over_budget": budget > 0 and total_cost > budget,
        }

    @web_route("GET", "/api/today")
    async def api_today(self, request):
        return await self.today_summary()

    @web_route("GET", "/api/usage")
    async def api_usage(self, request):
        """Aggregated usage from persistent stats."""
        days = int(request.query_params.get("days", "30"))
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        total_row = self.db.execute(
            """
            SELECT COALESCE(SUM(calls),0) as calls, COALESCE(SUM(total_tokens),0) as tokens,
                   COALESCE(SUM(cost),0) as cost, COALESCE(SUM(images),0) as images,
                   COALESCE(SUM(image_cost),0) as image_cost
            FROM daily_stats WHERE date >= ?
        """,
            (cutoff,),
        ).fetchone()
        total = {
            "calls": total_row["calls"],
            "tokens": total_row["tokens"],
            "cost": round(total_row["cost"], 6),
            "images": total_row["images"],
            "image_cost": round(total_row["image_cost"], 6),
            "total_cost": round(total_row["cost"] + total_row["image_cost"], 6),
        }

        by_provider = {
            r["provider"]: {"calls": r["calls"], "tokens": r["tokens"], "cost": round(r["cost"], 6)}
            for r in self.db.execute(
                """
                SELECT provider, SUM(calls) as calls, SUM(tokens) as tokens, SUM(cost) as cost
                FROM provider_stats WHERE date >= ? GROUP BY provider ORDER BY cost DESC
            """,
                (cutoff,),
            ).fetchall()
        }
        by_app = {
            r["app"]: {"calls": r["calls"], "tokens": r["tokens"], "cost": round(r["cost"], 6)}
            for r in self.db.execute(
                """
                SELECT app, SUM(calls) as calls, SUM(tokens) as tokens, SUM(cost) as cost
                FROM app_stats WHERE date >= ? GROUP BY app ORDER BY cost DESC
            """,
                (cutoff,),
            ).fetchall()
        }

        daily = []
        for i in range(days - 1, -1, -1):
            d = (date.today() - timedelta(days=i)).isoformat()
            row = self.db.execute(
                "SELECT calls, cost, image_cost FROM daily_stats WHERE date = ?", (d,)
            ).fetchone()
            if row:
                daily.append(
                    {
                        "date": d,
                        "cost": round(row["cost"] + row["image_cost"], 6),
                        "calls": row["calls"],
                    }
                )
            else:
                daily.append({"date": d, "cost": 0, "calls": 0})

        return {
            "days": days,
            "total": total,
            "by_provider": by_provider,
            "by_app": by_app,
            "daily": daily,
        }

    @web_route("GET", "/api/rates")
    async def api_rates(self, request):
        return {**self._cost_rates(), **{f"image_{k}": v for k, v in _IMAGE_COSTS.items()}}

    @web_route("POST", "/api/rates")
    async def api_set_rates(self, request):
        body = await request.json()
        rates = self._cost_rates()
        for provider, rate in body.items():
            try:
                rates[provider] = float(rate)
            except (ValueError, TypeError):
                continue
        self.kernel.config.set("billing.custom_rates", rates)
        return {"ok": True, "rates": rates}

    @web_route("GET", "/api/monthly")
    async def api_monthly(self, request):
        """Monthly cost summary."""
        rows = self.db.execute("""
            SELECT substr(date, 1, 7) as month,
                   SUM(calls) as calls, SUM(total_tokens) as tokens,
                   SUM(cost) + SUM(image_cost) as cost, SUM(images) as images
            FROM daily_stats GROUP BY month ORDER BY month
        """).fetchall()
        return {
            r["month"]: {
                "calls": r["calls"],
                "cost": round(r["cost"], 6),
                "tokens": r["tokens"],
                "images": r["images"],
            }
            for r in rows
        }

    @web_route("GET", "/api/vault-report")
    async def api_vault_report(self, request):
        """Write monthly cost report to vault."""
        today = date.today()
        month = today.strftime("%Y-%m")
        row = self.db.execute(
            """
            SELECT SUM(calls) as calls, SUM(total_tokens) as tokens,
                   SUM(cost) + SUM(image_cost) as cost, SUM(images) as images
            FROM daily_stats WHERE date LIKE ?
        """,
            (month + "%",),
        ).fetchone()
        total = {
            "calls": row["calls"] or 0,
            "cost": row["cost"] or 0.0,
            "tokens": row["tokens"] or 0,
            "images": row["images"] or 0,
        }
        content = (
            f"---\ndate: {today.isoformat()}\ntype: billing-report\nmonth: {month}\n---\n\n"
            f"## Billing Report — {month}\n\n"
            f"| Metric | Value |\n|---|---|\n"
            f"| LLM Calls | {total['calls']} |\n"
            f"| Tokens | {total['tokens']:,} |\n"
            f"| Images | {total['images']} |\n"
            f"| Total Cost | ${total['cost']:.4f} |\n"
        )
        path = f"40_Journal/Reports/{month}-billing.md"
        await self.write(path, content)
        await self.emit("billing:report_generated", {"path": path, "month": month, **total})
        return {"ok": True, "path": path, "month": month, **total}

    # ── AI insight ────────────────────────────────────────────

    @web_route("GET", "/api/insight")
    async def api_insight(self, request):
        """AI-generated cost analysis and optimization recommendations."""
        days = int(request.query_params.get("days", "30"))
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        total_row = self.db.execute(
            """
            SELECT COALESCE(SUM(calls),0) as calls, COALESCE(SUM(total_tokens),0) as tokens,
                   COALESCE(SUM(cost),0) + COALESCE(SUM(image_cost),0) as cost
            FROM daily_stats WHERE date >= ?
        """,
            (cutoff,),
        ).fetchone()

        by_provider = [
            f"{r['provider']}: {r['calls']} calls, ${r['cost']:.4f}"
            for r in self.db.execute(
                """
                SELECT provider, SUM(calls) as calls, SUM(cost) as cost
                FROM provider_stats WHERE date >= ? GROUP BY provider ORDER BY cost DESC
            """,
                (cutoff,),
            ).fetchall()
        ]

        by_app = [
            f"{r['app']}: {r['calls']} calls, ${r['cost']:.4f}"
            for r in self.db.execute(
                """
                SELECT app, SUM(calls) as calls, SUM(cost) as cost
                FROM app_stats WHERE date >= ? GROUP BY app ORDER BY cost DESC LIMIT 10
            """,
                (cutoff,),
            ).fetchall()
        ]

        context = (
            f"Period: last {days} days\n"
            f"Total: {total_row['calls']} calls, {total_row['tokens']:,} tokens, ${total_row['cost']:.4f}\n"
            f"By provider: {'; '.join(by_provider)}\n"
            f"Top apps: {'; '.join(by_app)}"
        )

        insight = await self.think(
            COST_INSIGHT_USER_TMPL.format(context=context),
            system=COST_INSIGHT_SYSTEM,
            domain="text",
            temperature=0.4,
        )
        return {"insight": insight, "period_days": days, "total_cost": round(total_row["cost"], 4)}

    # ── CLI ───────────────────────────────────────────────────

    @cli_command("billing", help="LLM usage and cost tracking")
    async def cmd_billing(self, action: str = "today"):
        if action == "today":
            today = self._get_day(today_iso())
            total = today.get("cost", 0) + today.get("image_cost", 0)
            print(
                f"\n  Today: {today.get('calls', 0)} calls, {today.get('total_tokens', 0)} tokens"
            )
            print(f"  LLM cost: ${today.get('cost', 0):.4f}")
            print(f"  Image cost: ${today.get('image_cost', 0):.4f}")
            print(f"  Total: ${total:.4f}")
            budget = self._get_budget()
            if budget:
                print(f"  Budget: ${budget:.2f} {'OVER' if total > budget else 'OK'}")
            for p, pd in today.get("by_provider", {}).items():
                print(f"    {p:<14} {pd['calls']:>3}x  {pd['tokens']:>6} tokens  ${pd['cost']:.4f}")
            print()
