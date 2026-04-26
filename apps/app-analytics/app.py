"""App Analytics — personal usage tracking for EmptyOS.

Tracks which apps you use, when, how much, and which have errors.
Informs what to build next: what to delete (unused), what to fix
(high-error), what you depend on (daily habits + streaks).

Data flows in via EventBus on_any callback:
  ui:viewed  → kind="view"  (page loads from web middleware)
  any other  → kind="event" (app-emitted events)

Aggregated into a TimeSeriesCounter (daily buckets, dims: app, kind, hour).
Error counts pulled from syslog on demand — not duplicated.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from emptyos.sdk import BaseApp, TimeSeriesCounter, cli_command, days_ago_utc, scheduled, today_utc, web_route

from .vault_mixin import VaultAnalyticsMixin

INSIGHT_SYSTEM = (
    "You are an engineering metrics analyst reviewing personal app-usage data. "
    "Give 2-3 brief, actionable observations about usage patterns. "
    "Focus on: usage peaks vs dead zones, apps that dominate vs apps never touched, "
    "and any surprising patterns. Be concise (under 100 words total). "
    "Do NOT give generic productivity advice. Do NOT suggest installing new tools. "
    "Do NOT praise the user. Just state what the data shows."
)

INSIGHT_USER = (
    "Analyze these app usage patterns:\n\n{context}\n\n"
    "Note the most/least active apps, peak hours, and anything interesting."
)


def _iso_week(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    except Exception:
        return ""


class AppAnalyticsApp(BaseApp):

    async def setup(self):
        await super().setup()
        self.vault_analytics = VaultAnalyticsMixin(self)
        self.usage = TimeSeriesCounter(
            self.db, "usage",
            dims=["app", "kind", "hour"],
            granularity="day",
        )
        self.kernel.events.on_any(self._on_event)
        if self.usage.total() == 0:
            await self._backfill()

    async def _backfill(self):
        events = await self.kernel.events.history(limit=5000)
        for e in events:
            self._bump_from_dict(e)
        if events:
            self.log(f"backfilled {len(events)} events into usage counters")

    def _on_event(self, event):
        source = getattr(event, "source", "") or ""
        evt_type = getattr(event, "type", "") or ""
        ts = getattr(event, "timestamp", "") or ""
        kind = "view" if evt_type == "ui:viewed" else "event"
        hour = ts[11:13] if len(ts) > 13 else "00"
        try:
            self.usage.bump({"app": source, "kind": kind, "hour": hour})
        except Exception:
            pass

    def _bump_from_dict(self, e: dict):
        kind = "view" if e.get("type") == "ui:viewed" else "event"
        source = e.get("source") or "_unknown"
        ts = e.get("timestamp", "")
        hour = ts[11:13] if len(ts) > 13 else "00"
        try:
            self.usage.bump({"app": source, "kind": kind, "hour": hour})
        except Exception:
            pass


    def _all_app_ids(self) -> list[str]:
        return sorted(self.kernel.apps.manifests.keys())

    def _errors_by_app(self, days: int = 30) -> dict[str, int]:
        since = time.time() - days * 86400
        rows = self.kernel.syslog.query(level="error", since=since, limit=10000)
        counts: dict[str, int] = {}
        for r in rows:
            src = r.get("source", "")
            if src:
                counts[src] = counts.get(src, 0) + 1
        return counts

    # --- New endpoints -------------------------------------------------

    @web_route("GET", "/api/summary")
    async def api_summary(self, request):
        days = int(request.query_params.get("days", "30"))
        start = days_ago_utc(days - 1)
        end = today_utc()

        views_today = self.usage.total(start=end, end=end, where={"kind": "view"})
        views_7d = self.usage.total(start=days_ago_utc(6), end=end, where={"kind": "view"})
        views_30d = self.usage.total(start=start, end=end, where={"kind": "view"})

        all_apps = set(self._all_app_ids())
        active_rows = self.usage.top("app", start=days_ago_utc(6), end=end, limit=200)
        active_apps = {r["key"] for r in active_rows if r["key"] in all_apps}

        return {
            "views_today": views_today,
            "views_7d": views_7d,
            "views_30d": views_30d,
            "active_apps_7d": len(active_apps),
            "unused_apps_30d": len(all_apps - {r["key"] for r in self.usage.top("app", start=start, end=end, limit=200) if r["key"] in all_apps}),
            "total_apps": len(all_apps),
        }

    @web_route("GET", "/api/unused")
    async def api_unused(self, request):
        days = int(request.query_params.get("days", "30"))
        start = days_ago_utc(days - 1)
        end = today_utc()
        all_apps = set(self._all_app_ids())
        active = {r["key"] for r in self.usage.top("app", start=start, end=end, limit=200)}
        unused = sorted(all_apps - active)

        result = []
        for app_id in unused:
            last = self.usage.range(where={"app": app_id})
            last_date = last[-1]["bucket"] if last else None
            days_ago = None
            if last_date:
                try:
                    diff = datetime.now(timezone.utc) - datetime.strptime(last_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    days_ago = diff.days
                except Exception:
                    pass
            name = ""
            m = self.kernel.apps.manifests.get(app_id)
            if m:
                name = m.name or app_id
            result.append({"app_id": app_id, "name": name, "last_seen": last_date, "days_ago": days_ago})

        result.sort(key=lambda r: r["days_ago"] if r["days_ago"] is not None else 9999, reverse=True)
        return result

    @web_route("GET", "/api/heatmap")
    async def api_heatmap(self, request):
        app_id = request.query_params.get("app", "")
        days = int(request.query_params.get("days", "90"))
        start = days_ago_utc(days - 1)
        end = today_utc()
        where = {"app": app_id} if app_id else None
        rows = self.usage.range(start=start, end=end, where=where, group_by="bucket")
        return {r["key"]: r["count"] for r in rows}

    @web_route("GET", "/api/errors-vs-usage")
    async def api_errors_vs_usage(self, request):
        days = int(request.query_params.get("days", "30"))
        start = days_ago_utc(days - 1)
        end = today_utc()
        all_apps = self._all_app_ids()
        errors = self._errors_by_app(days)

        result = []
        for app_id in all_apps:
            views = self.usage.total(start=start, end=end, where={"app": app_id, "kind": "view"})
            events = self.usage.total(start=start, end=end, where={"app": app_id, "kind": "event"})
            errs = errors.get(app_id, 0)
            activity = views + events
            error_rate = errs / max(1, activity)
            priority = error_rate * activity
            if errs > 0 or activity > 0:
                result.append({
                    "app": app_id, "views": views, "events": events,
                    "errors": errs, "error_rate": round(error_rate, 4),
                    "priority": round(priority, 2),
                })
        result.sort(key=lambda r: r["priority"], reverse=True)
        return result

    @web_route("GET", "/api/time-of-day")
    async def api_time_of_day(self, request):
        days = int(request.query_params.get("days", "30"))
        start = days_ago_utc(days - 1)
        end = today_utc()
        rows = self.usage.top("hour", start=start, end=end, limit=24)
        by_hour = {r["key"]: r["count"] for r in rows}
        return {f"{h:02d}": by_hour.get(f"{h:02d}", 0) for h in range(24)}

    @web_route("GET", "/api/streaks")
    async def api_streaks(self, request):
        all_apps = self._all_app_ids()
        result = []
        now_week = _iso_week(today_utc())
        for app_id in all_apps:
            rows = self.usage.range(where={"app": app_id}, group_by="bucket")
            weeks = sorted({_iso_week(r["key"]) for r in rows if _iso_week(r["key"])})
            if not weeks:
                continue
            current = 0
            longest = 0
            streak = 1
            for i in range(1, len(weeks)):
                prev_y, prev_w = int(weeks[i - 1][:4]), int(weeks[i - 1][6:])
                cur_y, cur_w = int(weeks[i][:4]), int(weeks[i][6:])
                if (cur_y == prev_y and cur_w == prev_w + 1) or (cur_y == prev_y + 1 and prev_w >= 52 and cur_w == 1):
                    streak += 1
                else:
                    longest = max(longest, streak)
                    streak = 1
            longest = max(longest, streak)
            if weeks[-1] == now_week or (len(weeks) >= 2 and weeks[-1] >= _iso_week(days_ago_utc(13))):
                current = streak
            else:
                current = 0
            result.append({
                "app": app_id,
                "current_weeks": current,
                "longest_weeks": longest,
                "last_week": weeks[-1] if weeks else None,
            })
        result.sort(key=lambda r: r["current_weeks"], reverse=True)
        return [r for r in result if r["longest_weeks"] > 0]

    # --- Legacy endpoints (backward compat) ----------------------------

    async def analytics(self, limit: int = 500) -> dict:
        events = await self.kernel.events.history(limit=limit)
        by_source = Counter(e["source"] for e in events)
        by_type = Counter(e["type"] for e in events)
        by_hour = Counter(e["timestamp"][11:13] for e in events if len(e.get("timestamp", "")) > 13)
        return {
            "total_events": len(events),
            "unique_types": len(by_type),
            "unique_sources": len(by_source),
            "top_sources": dict(by_source.most_common(15)),
            "top_types": dict(by_type.most_common(15)),
            "by_hour": dict(sorted(by_hour.items())),
        }

    @web_route("GET", "/api/analytics")
    async def api_analytics(self, request):
        limit = int(request.query_params.get("limit", "500"))
        return await self.analytics(limit)

    @web_route("GET", "/api/app/{app_id}")
    async def api_app_detail(self, request):
        app_id = request.path_params["app_id"]
        limit = int(request.query_params.get("limit", "200"))
        events = await self.kernel.events.history(limit=limit)
        app_events = [e for e in events if e.get("source") == app_id]
        by_type = Counter(e["type"] for e in app_events)
        by_hour = Counter(e["timestamp"][11:13] for e in app_events if len(e.get("timestamp", "")) > 13)
        return {
            "app": app_id,
            "total_events": len(app_events),
            "by_type": dict(by_type.most_common(10)),
            "by_hour": dict(sorted(by_hour.items())),
            "recent": app_events[-20:],
        }

    @web_route("GET", "/api/daily")
    async def api_daily(self, request):
        events = await self.kernel.events.history(limit=2000)
        by_day: dict[str, int] = {}
        for e in events:
            d = e.get("timestamp", "")[:10]
            if d:
                by_day[d] = by_day.get(d, 0) + 1
        return dict(sorted(by_day.items()))

    @web_route("GET", "/api/active-apps")
    async def api_active_apps(self, request):
        events = await self.kernel.events.history(limit=500)
        by_source = Counter(e["source"] for e in events)
        return [{"app": app, "events": count} for app, count in by_source.most_common(30)]

    @web_route("GET", "/api/insight")
    async def api_insight(self, request):
        a = await self.analytics(500)
        context = (
            f"Total events: {a['total_events']}, {a['unique_sources']} apps, {a['unique_types']} event types\n"
            f"Top apps: {', '.join(f'{k} ({v})' for k, v in list(a['top_sources'].items())[:10])}\n"
            f"Top events: {', '.join(f'{k} ({v})' for k, v in list(a['top_types'].items())[:10])}\n"
            f"Peak hours: {', '.join(f'{h}:00 ({c})' for h, c in sorted(a['by_hour'].items(), key=lambda x: -x[1])[:5])}"
        )
        insight = await self.think(
            INSIGHT_USER.format(context=context),
            system=INSIGHT_SYSTEM,
            domain="text", temperature=0.4,
        )
        return {"insight": insight, "total_events": a["total_events"], "provenance": self.last_provenance()}

    async def get_summary(self):
        return await self.analytics(200)

    # --- Vault endpoints (absorbed from vault-analytics) ----------------

    @web_route("GET", "/api/vault/stats")
    async def api_vault_stats(self, request):
        return await self.vault_analytics.stats()

    @web_route("GET", "/api/vault/uncovered")
    async def api_vault_uncovered(self, request):
        return await self.vault_analytics.scan_uncovered()

    @web_route("GET", "/api/vault/recent")
    async def api_vault_recent(self, request):
        limit = int(request.query_params.get("limit", "20"))
        return await self.vault_analytics.recent(limit)

    @web_route("GET", "/api/vault/largest")
    async def api_vault_largest(self, request):
        limit = int(request.query_params.get("limit", "20"))
        return await self.vault_analytics.largest(limit)

    @web_route("GET", "/api/vault/stale")
    async def api_vault_stale(self, request):
        days = int(request.query_params.get("days", "90"))
        limit = int(request.query_params.get("limit", "30"))
        return await self.vault_analytics.stale(days, limit)

    @web_route("GET", "/api/vault/growth")
    async def api_vault_growth(self, request):
        return await self.vault_analytics.growth()

    async def get_vault_summary(self) -> dict:
        """Vault summary for call_app callers."""
        return await self.vault_analytics.get_vault_summary()

    # --- CLI -----------------------------------------------------------

    @cli_command("analytics", help="Personal usage patterns")
    async def cmd_analytics(self, action: str = "summary"):
        if action == "unused":
            start = days_ago_utc(29)
            all_apps = set(self._all_app_ids())
            active = {r["key"] for r in self.usage.top("app", start=start, limit=200)}
            unused = sorted(all_apps - active)
            print(f"\n  {len(unused)} unused apps (30d):")
            for a in unused:
                print(f"    {a}")
            print()
            return

        a = await self.analytics()
        print(f"\n  {a['total_events']} events, {a['unique_types']} types, {a['unique_sources']} sources")
        print(f"\n  Top sources:")
        for src, count in list(a["top_sources"].items())[:10]:
            bar = "#" * min(count, 30)
            print(f"    {src:<20} {count:>4}  {bar}")
        print()

    @cli_command("vault", help="Vault health and statistics")
    async def cmd_vault(self, action: str = "stats"):
        s = await self.vault_analytics.stats()
        if "error" in s:
            print(f"  {s['error']}")
            return
        print(f"\n  Vault: {s['vault_path']}")
        print(f"  Total: {s['total_files']} files, {s['total_size_mb']} MB\n")
        for label, data in s["para"].items():
            bar = "#" * min(int(data["count"] / 50), 30)
            print(f"    {label:<14} {data['count']:>5} files  {data['size_mb']:>5.1f} MB  {bar}")
        if s["other_files"]:
            print(f"    {'Other':<14} {s['other_files']:>5} files")
        print()

    # --- Retention -----------------------------------------------------

    @scheduled("23 3 * * *", id="app-analytics-trim")
    async def nightly_trim(self):
        days = int(self.setting("app-analytics.retention_days", 365) or 365)
        before = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        removed = self.usage.trim(before)
        if removed:
            self.log(f"trimmed {removed} analytics rows older than {before}")
