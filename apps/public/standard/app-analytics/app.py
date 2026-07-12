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

import asyncio
import json
import math
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from emptyos.sdk import (
    BaseApp,
    TimeSeriesCounter,
    cli_command,
    days_ago_utc,
    scheduled,
    today_utc,
    web_route,
)

from .vault_mixin import VaultAnalyticsMixin


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
            self.db,
            "usage",
            dims=["app", "kind", "hour"],
            granularity="day",
        )
        self.kernel.events.on_any(self._on_event)
        # Weekly vault-structure sweep (report-only — purge is always a manual,
        # user-confirmed act; see scripts/check_vault_structure.py). Dark by
        # default per project_feature_pipeline_flag_default_dark: flip
        # [apps.app-analytics] vault_structure_sweep = true to arm.
        if self.app_config("vault_structure_sweep", False):
            cron = self.app_config("vault_structure_cron", "0 8 * * 0")  # Mon 08:00 (APScheduler 0=Mon)
            self.add_cron_job_logged(
                "vault-structure-sweep", self._vault_structure_sweep,
                cron=cron, crash_event="vault_structure_crash",
            )
        if self.usage.total() == 0:
            await self._backfill()

    async def _vault_structure_sweep(self):
        """Run scripts/check_vault_structure.py --json as a subprocess (never
        import it into the daemon), record the envelope, and nudge the user
        through the proactive gate when there's anything to act on."""
        script = Path(self.config.path).parent / "scripts" / "check_vault_structure.py"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script), "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        try:
            env = json.loads(out.decode("utf-8", errors="ignore").strip() or "{}")
        except Exception:
            env = {}
        data = env.get("data") or {}
        state = self.load_state({})
        state["vault_structure"] = {
            "ts": datetime.now(UTC).isoformat(),
            "ok": env.get("ok"),
            "message": env.get("message", ""),
            "safe_count": data.get("safe_count", 0),
            "review_count": data.get("review_count", 0),
            "advisory": data.get("advisory", {}),
        }
        self.save_state(state)
        safe = data.get("safe_count", 0)
        review = data.get("review_count", 0)
        if safe or review:
            await self.proactive_notify(
                "vault-structure",
                f"Vault structure sweep: {review} item(s) need review, "
                f"{safe} auto-fixable (run check_vault_structure.py --purge).",
                dedup_key=f"vault-structure-{today_utc()}",
                link={"app": "app-analytics"},
            )

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
            "unused_apps_30d": len(
                all_apps
                - {
                    r["key"]
                    for r in self.usage.top("app", start=start, end=end, limit=200)
                    if r["key"] in all_apps
                }
            ),
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
                    diff = datetime.now(UTC) - datetime.strptime(last_date, "%Y-%m-%d").replace(
                        tzinfo=UTC
                    )
                    days_ago = diff.days
                except Exception:
                    pass
            name = ""
            m = self.kernel.apps.manifests.get(app_id)
            if m:
                name = m.name or app_id
            result.append(
                {"app_id": app_id, "name": name, "last_seen": last_date, "days_ago": days_ago}
            )

        result.sort(
            key=lambda r: r["days_ago"] if r["days_ago"] is not None else 9999, reverse=True
        )
        return result

    @web_route("GET", "/api/ranking")
    async def api_ranking(self, request):
        """Per-app score blending frequency + recency over `days` (default 90).

        Consumed by the hub apps panel and the nav drawer to float
        recently+frequently used apps to the top. Each daily view bucket
        contributes ``count * exp(-age_days / half_life)`` to the app's
        score; half_life default 14 days so an app used yesterday outranks
        one used 30× a quarter ago. Apps never opened don't appear in the
        result — consumers should fall back to alphabetical for those.
        """
        try:
            days = int(request.query_params.get("days", "90"))
        except ValueError:
            days = 90
        try:
            half_life = float(request.query_params.get("half_life", "14"))
        except ValueError:
            half_life = 14.0
        days = max(1, min(days, 365))
        half_life = max(0.5, half_life)

        end = today_utc()
        start = days_ago_utc(days - 1)
        rows = self.usage.range(start=start, end=end, where={"kind": "view"})

        try:
            end_d = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            end_d = datetime.now(UTC)

        scores: dict[str, float] = {}
        last_seen: dict[str, str] = {}
        totals: dict[str, int] = {}
        for r in rows:
            app_id = r.get("app") or ""
            bucket = r.get("bucket") or ""
            cnt = int(r.get("count") or 0)
            if not app_id or not bucket or cnt <= 0:
                continue
            try:
                d = datetime.strptime(bucket, "%Y-%m-%d").replace(tzinfo=UTC)
                age = max(0.0, (end_d - d).days)
            except Exception:
                age = float(days)
            scores[app_id] = scores.get(app_id, 0.0) + cnt * math.exp(-age / half_life)
            totals[app_id] = totals.get(app_id, 0) + cnt
            if bucket > last_seen.get(app_id, ""):
                last_seen[app_id] = bucket

        ranked = [
            {
                "app": aid,
                "score": round(s, 4),
                "views": totals.get(aid, 0),
                "last_seen": last_seen.get(aid, ""),
            }
            for aid, s in scores.items()
        ]
        ranked.sort(key=lambda r: r["score"], reverse=True)
        return {"days": days, "half_life": half_life, "ranking": ranked}

    # ── Hub panel — dormant-but-relevant apps (pull-free discovery) ─────

    async def panel_worth_a_look(self) -> list[dict] | None:
        """Hub: ≤3 apps you used before, dropped 30–120 days ago, and that
        relate to what you're active in now (cluster co-membership with a
        top-10 active app, or keyword overlap with vault interest tags).
        Deterministic — no LLM. Never-used apps are excluded: 'not adopted'
        is a different problem from 'forgotten'."""
        end = today_utc()
        rows = self.usage.range(start=days_ago_utc(179), end=end, where={"kind": "view"})
        last_seen: dict[str, str] = {}
        for r in rows:
            aid, bucket = r.get("app") or "", r.get("bucket") or ""
            if aid and bucket > last_seen.get(aid, ""):
                last_seen[aid] = bucket

        try:
            enabled = self.kernel.apps.enabled_ids()
        except Exception:
            enabled = set(self._all_app_ids())
        manifests = self.kernel.apps.manifests
        now = datetime.now(UTC)

        dormant: dict[str, int] = {}
        for aid, bucket in last_seen.items():
            if aid not in enabled:
                continue
            m = manifests.get(aid)
            if not m:
                continue
            app_block = m.raw.get("app") or {}
            if app_block.get("lucky_skip") or app_block.get("private"):
                continue
            if not ((m.raw.get("provides") or {}).get("web") or {}).get("prefix"):
                continue
            try:
                age = (now - datetime.strptime(bucket, "%Y-%m-%d").replace(tzinfo=UTC)).days
            except ValueError:
                continue
            if 30 <= age <= 120:
                dormant[aid] = age
        if not dormant:
            return None

        active = [
            r["key"]
            for r in self.usage.top("app", start=days_ago_utc(6), end=end, limit=10)
            if r["key"] in enabled and r["key"] not in dormant
        ]

        # Cluster co-membership — same affinity graph the home screen uses.
        cluster_of: dict[str, int] = {}
        try:
            from emptyos.sdk.clustering import get_clusters
            for i, c in enumerate(get_clusters(manifests)):
                for entry in c.get("apps") or []:
                    cluster_of[entry.get("id", "")] = i
        except Exception:
            pass
        active_clusters = {cluster_of[a] for a in active if a in cluster_of}

        # Vault interest tags — metadata only (rule 19), matched against
        # manifest name / description / user_intent words.
        try:
            tags = [t[0].lower() for t in (self.vault_interest_profile(max_tags=12).get("tags") or [])]
        except Exception:
            tags = []

        scored: list[tuple[float, str, str]] = []  # (score, app_id, reason)
        for aid, age in dormant.items():
            m = manifests[aid]
            app_block = m.raw.get("app") or {}
            blob = " ".join(
                [aid, m.name or "", app_block.get("description") or ""]
                + [str(p) for p in (app_block.get("user_intent") or [])]
            ).lower()
            score, reason = 0.0, ""
            if cluster_of.get(aid) in active_clusters:
                buddy = next((a for a in active if cluster_of.get(a) == cluster_of.get(aid)), "")
                buddy_name = manifests[buddy].name if buddy in manifests else buddy
                score += 2
                reason = f"pairs with {buddy_name}, which you use"
            hits = [t for t in tags if len(t) >= 3 and t in blob][:2]
            if hits:
                score += 2 * len(hits)
                if not reason:
                    reason = f"matches your focus: {', '.join(hits)}"
            if score >= 2:
                weeks = max(1, age // 7)
                scored.append((score - age / 365.0, aid, f"unused {weeks}w — {reason}"))

        if not scored:
            return None
        scored.sort(reverse=True)
        out = []
        for _, aid, sub in scored[:3]:
            m = manifests[aid]
            prefix = ((m.raw.get("provides") or {}).get("web") or {}).get("prefix", "")
            out.append({
                "title": m.name or aid,
                "subtitle": sub,
                "href": prefix + ("/" if not prefix.endswith("/") else ""),
            })
        return out

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
                result.append(
                    {
                        "app": app_id,
                        "views": views,
                        "events": events,
                        "errors": errs,
                        "error_rate": round(error_rate, 4),
                        "priority": round(priority, 2),
                    }
                )
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
                if (cur_y == prev_y and cur_w == prev_w + 1) or (
                    cur_y == prev_y + 1 and prev_w >= 52 and cur_w == 1
                ):
                    streak += 1
                else:
                    longest = max(longest, streak)
                    streak = 1
            longest = max(longest, streak)
            if weeks[-1] == now_week or (
                len(weeks) >= 2 and weeks[-1] >= _iso_week(days_ago_utc(13))
            ):
                current = streak
            else:
                current = 0
            result.append(
                {
                    "app": app_id,
                    "current_weeks": current,
                    "longest_weeks": longest,
                    "last_week": weeks[-1] if weeks else None,
                }
            )
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
        by_hour = Counter(
            e["timestamp"][11:13] for e in app_events if len(e.get("timestamp", "")) > 13
        )
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
        print(
            f"\n  {a['total_events']} events, {a['unique_types']} types, {a['unique_sources']} sources"
        )
        print("\n  Top sources:")
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
        before = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        removed = self.usage.trim(before)
        if removed:
            self.log(f"trimmed {removed} analytics rows older than {before}")
