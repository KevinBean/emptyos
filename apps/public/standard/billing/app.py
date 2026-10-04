"""Billing — LLM + image generation cost tracking.

Listens to think:executed and studio:generated events.
Persists daily usage stats to JSON. Supports budget alerts.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from emptyos.sdk import (
    BaseApp,
    cli_command,
    config_flag,
    ensure_column,
    on_event,
    today_iso,
    web_route,
)

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


def _meter_spend(data_root, actor_id: str, cost: float, enabled: bool) -> bool:
    """Bridge billed cloud cost into the autopilot budget ledger.

    Attribution is the calling app id (`think:executed.app`) — for the
    autonomous spenders (staff, dogfood-agent, fix-agent) the app IS the
    actor. Per-room/per-job actor threading is deferred until a consumer
    needs the split. Dark-flagged via `[autopilot] meter_spend`; returns
    True iff a spend was recorded. Module-level + kernel-free so it unit
    tests without a daemon (`tests/test_unit_billing_meter.py`).
    """
    if not enabled or not actor_id:
        return False
    try:
        amount = float(cost)
    except (TypeError, ValueError):
        return False
    if amount <= 0:
        return False
    from emptyos.sdk.autopilot import record_spend

    record_spend(data_root, actor_id, amount)
    return True


# Per-trace rows older than this are pruned on boot — the table is a debugging
# aid for recent agent turns, not a second billing ledger.
_TRACE_RETENTION_DAYS = 30


def _cache_hit_pct(cached_tokens, prompt_tokens) -> float:
    """Share of prompt tokens served from the provider's prefix cache, in %.

    `cached_tokens` is a subset of `prompt_tokens` (OpenAI automatic prompt
    caching / Anthropic cache_control both report it that way); clamp anyway
    so a malformed event can't yield >100%. Reasonix calls cache-hit rate
    "the key observability signal" for prefix-stable prompting — see
    `.claude/rules/prompt-prefix-cache.md`.
    """
    try:
        pt = int(prompt_tokens or 0)
        cached = max(0, min(int(cached_tokens or 0), pt))
    except (TypeError, ValueError):
        return 0.0
    if pt <= 0:
        return 0.0
    return round(100.0 * cached / pt, 1)


def _record_trace_call(
    db,
    data: dict,
    *,
    ts: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    enabled: bool,
) -> bool:
    """Retain one per-call cost row keyed by the agent-turn trace id.

    The daily aggregates discard per-call detail after summing; this keeps it —
    but only for calls carrying a ``trace_id`` (fired inside a traced agent
    turn, see ``emptyos.sdk.trace``) and only when the dark flag
    ``[apps.billing] feature.trace-costs.enabled`` is on. Module-level +
    kernel-free so it unit tests without a daemon
    (``tests/test_unit_billing_trace.py``). Caller owns the commit.
    Returns True iff a row was written.
    """
    if not enabled:
        return False
    trace_id = data.get("trace_id")
    if not trace_id:
        return False
    db.execute(
        "INSERT INTO trace_calls (ts, trace_id, app, provider, prompt_tokens,"
        " completion_tokens, cost, latency_ms) VALUES (?,?,?,?,?,?,?,?)",
        (
            ts,
            str(trace_id),
            data.get("app") or "unknown",
            data.get("provider") or "unknown",
            int(prompt_tokens or 0),
            int(completion_tokens or 0),
            float(cost or 0),
            int(data.get("latency_ms") or 0),
        ),
    )
    return True


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
        # Register a hard spend cap with the cloud-consent gate. Once today's
        # cloud spend reaches the limit, further cloud calls are refused
        # pre-flight (they fall through to local/human providers). Distinct
        # from `daily_budget`, which only *alerts* and keeps spending.
        cm = self.service("cloud_consent")
        if cm is not None and hasattr(cm, "set_spend_guard"):
            cm.set_spend_guard(self._spend_guard)

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
            CREATE TABLE IF NOT EXISTS trace_calls (
                ts TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                app TEXT NOT NULL,
                provider TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0.0,
                latency_ms INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_trace_calls_trace ON trace_calls(trace_id);
        """)
        # Prefix-cache observability (prompt-prefix-cache rule): providers
        # report `cached_tokens` on think:executed; accumulate so cache-hit %
        # is visible. ADD COLUMN migration for pre-existing databases.
        ensure_column(self.db, "daily_stats", "cached_tokens", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(self.db, "app_stats", "cached_tokens", "INTEGER NOT NULL DEFAULT 0")
        # Bounded retention — ISO timestamps compare lexically against a date.
        cutoff = (date.today() - timedelta(days=_TRACE_RETENTION_DAYS)).isoformat()
        self.db.execute("DELETE FROM trace_calls WHERE ts < ?", (cutoff,))
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
        try:
            cached = max(0, min(int(d.get("cached_tokens") or 0), int(pt or 0)))
        except (TypeError, ValueError):
            cached = 0

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
                total_tokens = total_tokens + ?, cached_tokens = cached_tokens + ?,
                cost = round(cost + ?, 6)
            WHERE date = ?
        """,
            (pt, ct, pt + ct, cached, real_cost, today),
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
            INSERT INTO app_stats (date, app, calls, tokens, cost, cached_tokens)
            VALUES (?,?,1,?,?,?)
            ON CONFLICT(date, app) DO UPDATE SET
                calls = calls + 1, tokens = tokens + ?, cost = round(cost + ?, 6),
                cached_tokens = cached_tokens + ?
        """,
            (today, app, pt + ct, real_cost, cached, pt + ct, real_cost, cached),
        )

        # Per-trace cost retention (dark flag, default off): keep the per-call
        # row the daily aggregates discard, keyed by the agent-turn trace_id.
        # Never let it break the aggregate write path.
        try:
            _record_trace_call(
                self.db,
                d,
                ts=datetime.now(timezone.utc).isoformat(),
                prompt_tokens=pt,
                completion_tokens=ct,
                cost=real_cost,
                enabled=bool(self.app_config("feature.trace-costs.enabled", False)),
            )
        except Exception:
            pass

        self.db.commit()

        # Spend attribution → autopilot budget ledger (dark flag, default
        # off; `.claude/rules/autopilot-grants.md` § per-actor budget caps).
        # Never let a ledger write break billing.
        try:
            metering = config_flag(self.kernel.config, "autopilot.meter_spend")
            _meter_spend(self.kernel.config.data_dir, app, real_cost, metering)
        except Exception:
            pass

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
                # Route through the shared proactive gate — dedup_key means this
                # only actually delivers once per day even though every call
                # over budget re-triggers this branch (was previously spamming
                # a raw notification on every single call). Reuses the existing
                # "budget" kind (proactive's own periodic scan uses the same
                # label for the same concept).
                await self.proactive_notify_or_raw(
                    kind="budget",
                    text=f"Daily budget exceeded: ${total:.4f} > ${budget:.2f}",
                    dedup_key=f"billing-budget:{today}",
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
        if not isinstance(state, dict):
            return 0.0
        return float(state.get("daily_budget", 0))

    def _get_hard_limit(self) -> float:
        """Hard daily cloud-spend cap in dollars. 0 = no cap (default)."""
        state = self.load_state({})
        if not isinstance(state, dict):
            return 0.0   # _cap_unconfirmed reports this state as unknown
        try:
            return float(state.get("daily_hard_limit", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _cap_unconfirmed(self) -> bool:
        """True when the daily cap is unknown because billing.json was lost
        (load_state kept it aside as ``billing.json.corrupt-<ms>``) and the cap
        has not been set again since.

        Losing the file also loses the user's daily cap, and reading that as
        "no cap" would let metered cloud spend run unbounded with nothing on
        screen. Setting the cap on /billing (any value, 0 included) records
        which kept copy it answered, ``cap_confirmed_loss``; a newer copy blocks
        again. No timestamps are compared, so a clock step cannot confirm or
        un-confirm anything. Unreadable state of any other kind (not a dict,
        or load_state itself raising) also counts as unknown.
        """
        try:
            state = self.load_state({})  # first, so a bad file is moved aside
        except Exception:
            return True
        if not isinstance(state, dict):
            return True
        newest = self._newest_lost_copy()
        if newest is None:
            return False
        try:
            answered = int(state.get("cap_confirmed_loss", -1))
        except (TypeError, ValueError):
            answered = -1
        return newest > answered

    def _newest_lost_copy(self) -> int | None:
        """The ms suffix of the newest ``billing.json.corrupt-<ms>``, or None."""
        prefix = f"{self.state_path.name}.corrupt-"
        found = [int(p.name[len(prefix):]) for p in self.state_path.parent.glob(prefix + "*")
                 if p.name[len(prefix):].isdigit()]
        return max(found) if found else None

    def _is_metered(self, provider: str, capability: str) -> bool:
        """Whether a cloud provider is billed per call. A provider that cannot
        be found counts as metered, so an unknown cap still holds for it."""
        from emptyos.capabilities.spend_cap import is_metered  # noqa: PLC0415
        try:
            cap = self.kernel.capabilities.get(capability)
            found = next((p for p in cap.all_providers() if p.name == provider), None)
        except Exception:
            found = None
        return True if found is None else is_metered(found)

    async def _report_lost_cap(self) -> None:
        """Say once per lost copy why paid cloud calls stopped: a syslog error
        and a nudge through the proactive gate (deduped on the copy, so it
        survives restarts without repeating). Never raises."""
        newest = self._newest_lost_copy()
        reported = getattr(self, "_lost_cap_reported", None)
        if newest is None or reported == newest:
            return
        self._lost_cap_reported = newest
        try:
            self.log_error(f"paid cloud calls blocked: {self.LOST_CAP_REASON}")
        except Exception:
            pass
        try:
            await self.proactive_notify_or_raw(
                kind="budget",
                text="Paid cloud calls are paused: billing settings were lost, so the "
                     "daily spend cap is unknown. Set it again on /billing.",
                dedup_key=f"billing-lost-cap:{newest}",
                priority="warning",
                source="billing",
            )
        except Exception:
            pass

    LOST_CAP_REASON = ("billing settings were lost, so the daily spend cap is unknown — "
                       "set it again on /billing to resume paid cloud calls")

    def _today_total_cost(self) -> float:
        row = self.db.execute(
            "SELECT cost, image_cost FROM daily_stats WHERE date = ?", (today_iso(),)
        ).fetchone()
        if not row:
            return 0.0
        return (row["cost"] or 0) + (row["image_cost"] or 0)

    async def _spend_guard(self, provider: str, capability: str) -> str | None:
        """Pre-flight cap consulted by the cloud-consent gate before every
        cloud call. Returns a reason string to BLOCK, or None to allow.

        Blocks once today's spend has already reached the cap — a bounded
        one-call overshoot, since per-call cost isn't known until after the
        call. When the cap itself is unknown (``_cap_unconfirmed``) it fails
        CLOSED for providers billed per call and lets subscription and free
        ones (claude-cli, codex, edge-tts) through, since the cap bounds
        dollars. Any other fault fails open: a billing bug must never wedge
        every cloud capability.
        """
        if self._cap_unconfirmed() and self._is_metered(provider, capability):
            await self._report_lost_cap()
            return self.LOST_CAP_REASON
        try:
            limit = self._get_hard_limit()
            if limit <= 0:
                return None
            spent = self._today_total_cost()
            if spent >= limit:
                return f"daily cloud spend cap reached (${spent:.4f} / ${limit:.2f})"
            return None
        except Exception:
            return None

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

    @web_route("GET", "/api/hard-limit")
    async def api_get_hard_limit(self, request):
        return {
            "daily_hard_limit": self._get_hard_limit(),
            "today_spent": round(self._today_total_cost(), 6),
            "cap_unconfirmed": self._cap_unconfirmed(),
        }

    @web_route("POST", "/api/hard-limit")
    async def api_set_hard_limit(self, request):
        body = await request.json()
        state = self.load_state({})
        try:
            limit = float(body.get("daily_hard_limit", 0) or 0)
        except (TypeError, ValueError):
            limit = 0.0
        if not isinstance(state, dict):
            state = {}
        state["daily_hard_limit"] = max(0.0, limit)
        newest = self._newest_lost_copy()
        if newest is not None:
            state["cap_confirmed_loss"] = newest   # this save answers that loss
        self.save_state(state)
        return {"ok": True, "daily_hard_limit": state["daily_hard_limit"]}

    # ── API ───────────────────────────────────────────────────

    # Zero-filled shape for a day with no usage. Mirrors the daily_stats
    # column defaults; `date` is filled per-call.
    _EMPTY_DAY = {
        "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "total_tokens": 0, "cost": 0.0, "images": 0, "image_cost": 0.0,
    }

    def _get_day(self, day: str) -> dict:
        """Get a day's stats as a dict (for API compatibility).

        Always returns the FULL shape. A day with no usage has no daily_stats
        row, and returning ``{}`` made every field vanish from
        ``GET /billing/api/today`` — so a consumer reading ``cost`` (or
        ``calls``, or ``by_provider``) broke on any quiet day and on every
        fresh install, while working fine once some usage existed. Both
        callers already read through ``.get(..., 0)``, so neither depended on
        the empty dict being falsy.
        """
        row = self.db.execute("SELECT * FROM daily_stats WHERE date = ?", (day,)).fetchone()
        result = {**self._EMPTY_DAY, "date": day, **(dict(row) if row else {})}
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

    def _counterfactual_saved(self, by_provider: dict) -> float:
        """What today's usage would have cost if every call had run on the
        reference cloud rate instead — the value delivered by running local
        (found dark during the 2026-08 gap-analysis pass: with claude-cli and
        ollama both at $0.00, the dashboard showed $0 for a day of real work,
        which is exactly the number the local-first thesis should surface).

        `reference_provider` defaults to "openai" (the cheapest metered cloud
        rate already in `_cost_rates()`), overridable via
        `billing.reference_provider` for a different comparison point.
        """
        rates = self._cost_rates()
        reference = str(self.app_config("billing.reference_provider", "openai"))
        ref_rate = rates.get(reference, 0)
        if ref_rate <= 0:
            return 0.0
        saved = 0.0
        for stats in by_provider.values():
            tokens = stats.get("tokens", 0) or 0
            actual = stats.get("cost", 0) or 0
            counterfactual = tokens / 1000 * ref_rate
            saved += max(0.0, counterfactual - actual)
        return round(saved, 4)

    async def today_summary(self) -> dict:
        """Today's usage summary — safe to call via call_app()/[DO:]."""
        today = self._get_day(today_iso())
        budget = self._get_budget()
        cap_lost = self._cap_unconfirmed()   # first: moves a bad file aside
        hard_limit = self._get_hard_limit()
        total_cost = today.get("cost", 0) + today.get("image_cost", 0)
        return {
            **today,
            "date": today_iso(),
            "total_cost": round(total_cost, 6),
            "cache_hit_pct": _cache_hit_pct(
                today.get("cached_tokens", 0), today.get("prompt_tokens", 0)
            ),
            "saved_today": self._counterfactual_saved(today.get("by_provider", {})),
            "budget": budget,
            "over_budget": budget > 0 and total_cost > budget,
            "hard_limit": hard_limit,
            "cloud_blocked": (hard_limit > 0 and total_cost >= hard_limit) or cap_lost,
            "cloud_blocked_reason": (self.LOST_CAP_REASON if cap_lost
                                     else "daily cap reached" if hard_limit > 0 and total_cost >= hard_limit
                                     else ""),
        }

    @web_route("GET", "/api/today")
    async def api_today(self, request):
        return await self.today_summary()

    @web_route("GET", "/api/traces")
    async def api_traces(self, request):
        """Recent agent-turn cost roll-ups (per-trace retention, dark-flagged)."""
        limit = min(int(request.query_params.get("limit", 20)), 200)
        rows = self.db.execute(
            """
            SELECT trace_id, COUNT(*) AS calls,
                   SUM(prompt_tokens + completion_tokens) AS tokens,
                   round(SUM(cost), 6) AS cost, SUM(latency_ms) AS llm_ms,
                   MIN(ts) AS first_ts, MAX(ts) AS last_ts
            FROM trace_calls GROUP BY trace_id
            ORDER BY MAX(ts) DESC LIMIT ?
        """,
            (limit,),
        ).fetchall()
        return {
            "enabled": bool(self.app_config("feature.trace-costs.enabled", False)),
            "traces": [dict(r) for r in rows],
        }

    @web_route("GET", "/api/trace/{trace_id}")
    async def api_trace(self, request):
        """Per-call cost breakdown for one agent-turn trace."""
        trace_id = request.path_params.get("trace_id", "")
        rows = self.db.execute(
            "SELECT * FROM trace_calls WHERE trace_id = ? ORDER BY ts", (trace_id,)
        ).fetchall()
        calls = [dict(r) for r in rows]
        return {
            "trace_id": trace_id,
            "calls": calls,
            "totals": {
                "calls": len(calls),
                "tokens": sum(c["prompt_tokens"] + c["completion_tokens"] for c in calls),
                "cost": round(sum(c["cost"] for c in calls), 6),
                "llm_ms": sum(c["latency_ms"] for c in calls),
            },
        }

    @web_route("GET", "/api/usage")
    async def api_usage(self, request):
        """Aggregated usage from persistent stats."""
        days = int(request.query_params.get("days", "30"))
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        total_row = self.db.execute(
            """
            SELECT COALESCE(SUM(calls),0) as calls, COALESCE(SUM(total_tokens),0) as tokens,
                   COALESCE(SUM(prompt_tokens),0) as prompt_tokens,
                   COALESCE(SUM(cached_tokens),0) as cached_tokens,
                   COALESCE(SUM(cost),0) as cost, COALESCE(SUM(images),0) as images,
                   COALESCE(SUM(image_cost),0) as image_cost
            FROM daily_stats WHERE date >= ?
        """,
            (cutoff,),
        ).fetchone()
        total = {
            "calls": total_row["calls"],
            "tokens": total_row["tokens"],
            "cached_tokens": total_row["cached_tokens"],
            "cache_hit_pct": _cache_hit_pct(
                total_row["cached_tokens"], total_row["prompt_tokens"]
            ),
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
            r["app"]: {
                "calls": r["calls"],
                "tokens": r["tokens"],
                "cached_tokens": r["cached_tokens"],
                "cost": round(r["cost"], 6),
            }
            for r in self.db.execute(
                """
                SELECT app, SUM(calls) as calls, SUM(tokens) as tokens,
                       SUM(cached_tokens) as cached_tokens, SUM(cost) as cost
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

    async def cost_for_app_since(self, app_id: str, since_iso: str) -> dict:
        """Total cost/tokens for one app since a given ISO date/datetime.

        A plain cross-app helper (not a `@web_route`) so callers can reach it
        via `call_app` without a `request` object — `api_usage`'s ``days=N``
        shape doesn't fit a caller that knows an exact start time, not a
        lookback window. Day-granularity like the rest of `daily_stats`/
        `app_stats`; a brief spanning part of a day is billed from that
        day's start.
        """
        since_date = (since_iso or "")[:10] or date.today().isoformat()
        row = self.db.execute(
            """
            SELECT COALESCE(SUM(calls),0) as calls, COALESCE(SUM(tokens),0) as tokens,
                   COALESCE(SUM(cost),0) as cost
            FROM app_stats WHERE app = ? AND date >= ?
            """,
            (app_id, since_date),
        ).fetchone()
        return {"calls": row["calls"], "tokens": row["tokens"], "cost": round(row["cost"], 6)}

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
        path = f"30_Resources/EmptyOS/billing/reports/{month}-billing.md"
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
