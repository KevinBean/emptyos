"""Proactive — the system speaks first, with restraint.

A scheduled scanner turns the user's live state into candidate nudges
(deadlines, journaling gaps, today's load, budget overruns) and routes each
through the shared gate in ``emptyos/sdk/proactive.py`` via
``BaseApp.proactive_notify``. The gate — master-enabled, per-kind mute, quiet
hours, a daily cap, a min gap, dedup — is what keeps a proactive system from
becoming noise the user learns to ignore.

**Dark by default.** The policy's master ``enabled`` is False on a fresh store,
so the scan runs but delivers nothing until the user flips the toggle on this
app's page. North-star fit: a nudge *to the user* is reversible/internal, so it
auto-runs once enabled; the safety net is the gate + the audit log on this page,
not approval-before-each-ping.

Sources are ordered by the wellbeing-wheel lens (CLAUDE.md Rule 16): emotional /
financial nudges get first crack at the daily budget before occupational ones,
so the engine doesn't only ever surface work.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

from emptyos.sdk import BaseApp, cli_command, scheduled, web_route
from emptyos.sdk import proactive as pro

# kind → human label for the policy UI (so the frontend doesn't hardcode the
# vocabulary). The order here is the wellbeing-lens scan order.
KINDS: dict[str, str] = {
    "journaling-gap": "Journaling gap",
    "budget": "Budget overrun",
    "deadline": "Deadlines due soon",
    "today-load": "Today's task load",
    "reminder": "Reminders",
    # Evening "lock in your wins" nudge — drafts three-things from today's activity:
    "eod-wins": "End-of-day wins",
    # Reactor event-chain nudges (reactor::_notify migration, 2026-07-03):
    "wellbeing": "Wellbeing support",
    "milestone": "Milestones & celebrations",
    "system": "System events",
    # Scheduled runbook notify blocks (interactive runs deliver raw — the
    # user Apply-clicked them in real time):
    "runbook": "Scheduled runbook notifications",
}

JOURNAL_GAP_DAYS = 3  # nudge once you've gone this many days without a journal entry


class ProactiveApp(BaseApp):
    def _root(self):
        return self.kernel.config.data_dir

    # ─── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _count_items(result) -> int:
        """Coerce an unknown call_app result into a count, fail-soft."""
        if isinstance(result, bool):
            return 0
        if isinstance(result, int):
            return result
        if isinstance(result, list):
            return len(result)
        if isinstance(result, dict):
            for k in ("count", "total", "value", "n"):
                v = result.get(k)
                if isinstance(v, int):
                    return v
            for k in ("items", "tasks", "reminders", "data", "rows", "results", "upcoming"):
                v = result.get(k)
                if isinstance(v, list):
                    return len(v)
        return 0

    @staticmethod
    def _first_title(result) -> str:
        seq = None
        if isinstance(result, list):
            seq = result
        elif isinstance(result, dict):
            for k in ("items", "tasks", "reminders", "data", "rows", "results", "upcoming"):
                if isinstance(result.get(k), list):
                    seq = result[k]
                    break
        if not seq:
            return ""
        first = seq[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            for k in ("title", "text", "name", "note", "label"):
                if first.get(k):
                    return str(first[k])
        return ""

    # ─── sources (each fail-soft → returns list of candidate dicts) ────────
    async def _src_journaling_gap(self) -> list[dict]:
        try:
            notes = self.vault_query(tags=["daily"])
        except Exception:
            return []
        latest: date | None = None
        for n in notes or []:
            d = self._parse_note_date(n)
            if d and (latest is None or d > latest):
                latest = d
        if latest is None:
            return []
        gap = (date.today() - latest).days
        if gap < JOURNAL_GAP_DAYS:
            return []
        return [{
            "kind": "journaling-gap",
            "text": f"It's been {gap} days since your last journal entry.",
            "urgency": "normal",
            "dedup_key": "journaling-gap",  # 24h TTL → at most once/day while the gap persists
            "link": {"text": "Write one", "href": "/journal/"},
        }]

    @staticmethod
    def _parse_note_date(note: dict) -> date | None:
        props = note.get("properties") or {}
        raw = props.get("date") or note.get("name") or ""
        s = str(raw)[:10]
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    async def _src_budget(self) -> list[dict]:
        try:
            from emptyos.sdk import autopilot
        except Exception:
            return []
        fn = getattr(autopilot, "all_budgets", None)
        if not fn:
            return []
        try:
            budgets = fn(self._root()) or {}
        except Exception:
            return []
        items = budgets.values() if isinstance(budgets, dict) else budgets
        out: list[dict] = []
        month = date.today().strftime("%Y-%m")
        for info in items or []:
            if not isinstance(info, dict):
                continue
            cap = info.get("monthly_cap_usd")
            spent = info.get("spent_usd")
            if cap is None or spent is None:
                continue
            try:
                cap_f, spent_f = float(cap), float(spent)
            except (TypeError, ValueError):
                continue
            # Skip trivial/unset caps (a sub-dollar "$0 of $0" cap is noise, not a
            # real overrun) and anything not actually at/over its cap.
            if cap_f < 1 or spent_f <= 0 or spent_f < cap_f:
                continue
            actor = info.get("actor_id") or info.get("actor") or "An agent"
            out.append({
                "kind": "budget",
                "text": f"{actor} is at its monthly budget (${float(spent):.0f} of ${float(cap):.0f}).",
                "urgency": "normal",
                "dedup_key": f"budget:{actor}:{month}",
                "link": {"text": "Review budgets", "href": "/agent/"},
            })
        return out

    async def _src_deadlines(self) -> list[dict]:
        try:
            res = await self.call_app("reminders", "upcoming", days=1)
        except Exception:
            return []
        n = self._count_items(res)
        if n <= 0:
            return []
        first = self._first_title(res)
        text = f"{n} reminder{'s' if n != 1 else ''} due in the next 24 hours."
        if first:
            text = f"Due soon: {first}" + (f" (+{n - 1} more)" if n > 1 else "")
        return [{
            "kind": "deadline",
            "text": text,
            "urgency": "normal",
            "dedup_key": f"deadline:{date.today().isoformat()}",
            "link": {"text": "Open reminders", "href": "/reminders/"},
        }]

    async def _src_today_load(self) -> list[dict]:
        try:
            res = await self.call_app("task", "panel_todays_tasks")
        except Exception:
            return []
        n = self._count_items(res)
        if n <= 0:
            return []
        return [{
            "kind": "today-load",
            "text": f"You have {n} task{'s' if n != 1 else ''} on today's list.",
            "urgency": "normal",
            "dedup_key": f"today-load:{date.today().isoformat()}",
            "link": {"text": "Open tasks", "href": "/task/"},
        }]

    # Evening window (system-local = Sydney per the scheduler-timezone gotcha);
    # sits before default quiet hours so the gate won't suppress it. Minutes-
    # since-midnight avoids the datetime.time class (module shadows it via
    # `import time`).
    _EOD_START_MIN = 20 * 60       # 20:00
    _EOD_END_MIN = 21 * 60 + 30    # 21:30

    async def _src_end_of_day(self) -> list[dict]:
        """Once each evening, if today's three-things box is still empty, nudge the
        user to lock in the wins the system drafted from today's activity. The link
        deep-links to the journal with ?draft=1, which auto-populates the drafts."""
        if not self.app_config("feature.eod-wins.enabled", False):
            return []
        now = datetime.now()
        hm = now.hour * 60 + now.minute
        if not (self._EOD_START_MIN <= hm < self._EOD_END_MIN):
            return []
        # Only nudge if the wins aren't already recorded (don't nag). Uses a plain
        # call_app-able helper — api_today needs a request object, call_app can't
        # supply one.
        try:
            filled = await self.call_app("journal", "three_things_filled")
        except Exception:
            return []
        if filled:
            return []
        return [{
            "kind": "eod-wins",
            "text": "I pulled a few wins from today — want to lock them in?",
            "urgency": "normal",
            "dedup_key": f"eod-wins:{date.today().isoformat()}",  # 24h TTL → once/day
            "channels": ["companion", "notify"],  # rail bubble + durable Telegram/vault
            "link": {"text": "Review", "href": "/journal/?draft=1"},
        }]

    async def _gather(self) -> list[dict]:
        """Run every source in wellbeing-lens order, flattening candidates. Order
        gives emotional/financial nudges first crack at the daily cap before
        occupational ones, so the engine doesn't only ever talk about work."""
        out: list[dict] = []
        for src in (
            self._src_journaling_gap,
            self._src_budget,
            self._src_deadlines,
            self._src_today_load,
            self._src_end_of_day,
        ):
            try:
                out.extend(await src())
            except Exception:
                continue
        return out

    async def _deliver(self, cand: dict) -> dict:
        res = await self.proactive_notify(
            cand["kind"], cand["text"],
            urgency=cand.get("urgency", "normal"),
            dedup_key=cand.get("dedup_key"),
            channels=cand.get("channels"),  # None → proactive_notify default channels
            link=cand.get("link"),
        )
        if res.get("delivered"):
            await self.emit("proactive:delivered", {
                "kind": cand["kind"], "text": cand["text"], "channels": res.get("channels", []),
            })
        return res

    async def _scan(self) -> dict:
        # Single dark gate: the policy master. Scanning while disabled is wasted
        # work, so bail early (the gate would suppress anyway).
        if not pro.load_policy(self._root()).get("enabled", False):
            return {"enabled": False, "generated": 0, "delivered": 0}
        cands = await self._gather()
        delivered = 0
        for c in cands:
            res = await self._deliver(c)
            if res.get("delivered"):
                delivered += 1
        return {"enabled": True, "generated": len(cands), "delivered": delivered}

    @scheduled("*/15 * * * *", id="proactive-scan")
    async def scheduled_scan(self):
        await self._scan()

    # ─── endpoints ─────────────────────────────────────────────────────────
    @web_route("GET", "/api/policy")
    async def api_policy(self, request):
        pol = pro.load_policy(self._root())
        pol["_kinds_catalog"] = KINDS  # so the UI can render the per-kind list
        return pol

    @web_route("POST", "/api/policy")
    async def api_policy_save(self, request):
        body = await self.safe_json(request)
        async with self.write_lock("proactive-dispatch"):
            pol = pro.load_policy(self._root())
            for k in ("enabled", "quiet_start", "quiet_end", "daily_cap",
                      "min_gap_sec", "critical_bypasses_quiet", "default_channels", "kinds"):
                if k in body:
                    pol[k] = body[k]
            pro.save_policy(self._root(), pol)
        return {"ok": True, "policy": pol}

    @web_route("POST", "/api/mute")
    async def api_mute(self, request):
        body = await self.safe_json(request)
        kind = (body.get("kind") or "").strip()
        if kind not in KINDS:
            return {"ok": False, "error": f"unknown kind: {kind}"}
        mute = bool(body.get("mute", True))
        async with self.write_lock("proactive-dispatch"):
            pol = pro.load_policy(self._root())
            kinds = pol.setdefault("kinds", {})
            kinds.setdefault(kind, {})["mute"] = mute
            pro.save_policy(self._root(), pol)
        return {"ok": True, "kind": kind, "mute": mute}

    @web_route("GET", "/api/log")
    async def api_log(self, request):
        try:
            limit = max(1, min(pro.LOG_LIMIT, int(request.query_params.get("limit") or "50")))
        except ValueError:
            limit = 50
        delivered_only = (request.query_params.get("delivered_only") or "").lower() in ("1", "true")
        return {"log": pro.read_log(self._root(), limit, delivered_only=delivered_only)}

    async def recent_delivered(self, limit: int = 3) -> list[dict]:
        """Most recent *delivered* nudges — RPC verb for ambient surfaces.

        First consumer: the devices e-ink dashboard (`builtin:nudges` section).
        Rows carry the audit-log shape: {ts, iso, kind, text, urgency, ...}.
        """
        try:
            limit = max(1, min(20, int(limit)))
        except (TypeError, ValueError):
            limit = 3
        return pro.read_log(self._root(), limit, delivered_only=True)

    @web_route("GET", "/api/state")
    async def api_state(self, request):
        st = pro.load_state(self._root())
        return {"state": st, "now": time.time()}

    @web_route("POST", "/api/scan")
    async def api_scan(self, request):
        return {"ok": True, **await self._scan()}

    @web_route("POST", "/api/test")
    async def api_test(self, request):
        """Push a synthetic nudge through the real gate so the user can verify
        their setup (and hear it, if voice + hands-free are on)."""
        body = await self.safe_json(request)
        text = (body.get("text") or "This is a test nudge from EmptyOS.").strip()
        kind = (body.get("kind") or "test").strip()
        urgency = (body.get("urgency") or "normal").strip()
        res = await self.proactive_notify(kind, text, urgency=urgency)
        return {"ok": True, **res}

    # ─── hub panel ─────────────────────────────────────────────────────────
    async def panel_recent(self):
        pol = pro.load_policy(self._root())
        log = pro.read_log(self._root(), 200, delivered_only=True)
        today = date.today().isoformat()
        today_n = sum(1 for r in log if str(r.get("iso", "")).startswith(today))
        if not pol.get("enabled"):
            if not log:
                return None  # clean install — show nothing
            return {"label": "Proactive", "value": "off", "href": "/proactive/"}
        return {"label": "Proactive", "value": f"{today_n} today", "href": "/proactive/"}

    # ─── CLI ───────────────────────────────────────────────────────────────
    @cli_command("proactive", help="Show proactive engine status")
    async def cli_status(self):
        pol = pro.load_policy(self._root())
        st = pro.load_state(self._root())
        day = st.get("day", {})
        lines = [
            f"Master: {'ON' if pol.get('enabled') else 'OFF (dark)'}",
            f"Quiet hours: {pol.get('quiet_start')}–{pol.get('quiet_end')}",
            f"Daily cap: {pol.get('daily_cap')}   Min gap: {pol.get('min_gap_sec')}s",
            f"Delivered today: {day.get('total', 0) if day.get('date') == date.today().isoformat() else 0}",
        ]
        muted = [k for k, c in (pol.get("kinds") or {}).items() if isinstance(c, dict) and c.get("mute")]
        if muted:
            lines.append(f"Muted kinds: {', '.join(muted)}")
        return "\n".join(lines)
