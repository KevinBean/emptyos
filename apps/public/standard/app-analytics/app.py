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

import emptyos
from emptyos.sdk import (
    BaseApp,
    TimeSeriesCounter,
    cli_command,
    days_ago_utc,
    scheduled,
    today_utc,
    web_route,
)

from .productivity import (
    CLASSES as PRODUCTIVITY_CLASSES,
    LABELS as PRODUCTIVITY_LABELS,
    classify_all,
    normalize_class,
    summarize_usage,
)
from .vault_mixin import VaultAnalyticsMixin

# The source-tree root that ships scripts/check_vault_structure.py — anchored
# to the *running* `emptyos` package's own file, NOT to `self.repo_root`
# (which is "the directory containing THIS daemon's emptyos.toml"). Those
# differ for a sandbox-pool member: its own toml sits in `sandbox-9002/`,
# while `scripts/` only exists at the shared repo root every plain-leased
# member's `emptyos` package is actually imported from. Using self.repo_root
# here built a script path that didn't exist, so the subprocess spawned,
# python printed "can't open file" to stderr (redirected to DEVNULL) and
# exited non-zero, and the daemon silently returned an all-zero envelope —
# found live while sandbox-verifying this feature (2026-08-23).
_SOURCE_ROOT = Path(emptyos.__file__).resolve().parent.parent


def _iso_week(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    except Exception:
        return ""


def vault_structure_record(env: dict, *, ts: str) -> dict:
    """Pure: turn a check_vault_structure.py `--json` envelope into the state
    shape the UI reads. Extracted out of `_run_vault_structure` so the
    envelope-to-state mapping is unit-testable without a daemon — the
    subprocess call and load_state/save_state I/O stay in the async wrapper.
    A malformed/empty envelope (subprocess crash, non-JSON stdout) degrades
    to all-zero counts + `ok: None` rather than raising.
    """
    data = env.get("data") or {}
    return {
        "ts": ts,
        "ok": env.get("ok"),
        "message": env.get("message", ""),
        "safe_count": data.get("safe_count", 0),
        "review_count": data.get("review_count", 0),
        "advisory": data.get("advisory", {}),
        "purged": data.get("purged"),
    }


def should_notify_vault_structure(record: dict) -> bool:
    """The weekly-sweep notify gate: only nudge when there's something to
    act on. A clean vault (0 safe, 0 review) stays silent."""
    return bool(record.get("safe_count") or record.get("review_count"))


def vault_structure_argv(script_path, vault_path, *, purge: bool) -> list[str]:
    """Pure: the argv (minus the interpreter) for check_vault_structure.py.

    `--vault` MUST always be present — see the long comment on
    `_run_vault_structure_script`. Pinned here as a standalone function so a
    future edit that drops the flag fails a fast unit test instead of only
    being discoverable by scanning a real vault from a sandbox daemon.
    """
    args = [str(script_path), "--vault", str(vault_path), "--json"]
    if purge:
        args.append("--purge")
    return args


DIGEST_SYSTEM = (
    "You are a terse personal-usage-analytics narrator. Given a compact "
    "summary of which apps someone used this week, write 2-3 plain-prose "
    "sentences highlighting what stands out — a habit forming, a streak, "
    "or something newly unused.\n\n"
    "Do NOT:\n"
    "- Use bullet points, headings, or markdown.\n"
    "- List every app or number verbatim — pick what's actually notable.\n"
    "- Invent facts not present in the data.\n"
    "- Begin with 'This week' or 'Looking at your data'."
)


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

    async def _run_vault_structure_script(self, *, purge: bool) -> dict:
        """Run scripts/check_vault_structure.py --json as a subprocess — never
        import it into the daemon (it's a standalone fs-mutation script by
        design). `--purge` removes only the content-lossless 'safe' classes;
        `review` items are never touched by the script itself, so this can't
        act on them regardless of the caller.

        `--vault` is passed explicitly as THIS daemon's own `self.vault_root`.
        Without it, the script's own `resolve_vault()` falls back to reading
        `notes.path` out of `<repo>/emptyos.toml` — the MAIN daemon's config —
        regardless of which daemon actually spawned the subprocess. Every
        sandbox-pool member shares the same source tree (and the same
        `scripts/check_vault_structure.py` file, whose `REPO` constant is
        derived from `__file__`, not from the caller), so without this flag a
        scan/purge triggered from a throwaway sandbox vault would silently
        operate on the REAL user vault instead. Found live while verifying
        this feature on a leased sandbox member (2026-08-23) — a genuinely
        dangerous miss for a `--purge`-capable subprocess.
        """
        script = _SOURCE_ROOT / "scripts" / "check_vault_structure.py"
        args = vault_structure_argv(script, self.vault_root, purge=purge)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        try:
            return json.loads(out.decode("utf-8", errors="ignore").strip() or "{}")
        except Exception:
            return {}

    async def _run_vault_structure(self, *, purge: bool, notify: bool) -> dict:
        """Run the scan (or scan+purge), persist it as the last-known state,
        and optionally nudge through the proactive gate. Shared by the weekly
        cron sweep and the on-demand `/api/vault-structure/{scan,purge}`
        routes — one subprocess call site, one state shape."""
        env = await self._run_vault_structure_script(purge=purge)
        record = vault_structure_record(env, ts=datetime.now(UTC).isoformat())
        if purge and record.get("purged"):
            # `--purge --json` is one pass — scan, then act on what THAT scan
            # found — so its safe_count is the PRE-purge count, not what's
            # left. Re-scan so the UI shows the true current state instead of
            # "2 auto-fixable" right after those 2 were just removed.
            rescan_env = await self._run_vault_structure_script(purge=False)
            rescanned = vault_structure_record(rescan_env, ts=datetime.now(UTC).isoformat())
            rescanned["purged"] = record["purged"]
            record = rescanned
        # Same lock as the productivity-override writer — one state file,
        # two read-modify-write paths, so an unlocked save here would drop
        # whichever key the other writer had just added.
        async with self.write_lock("state"):
            state = self.load_state({})
            state["vault_structure"] = record
            self.save_state(state)
        if notify and should_notify_vault_structure(record):
            await self.proactive_notify(
                "vault-structure",
                f"Vault structure sweep: {record['review_count']} item(s) need review, "
                f"{record['safe_count']} auto-fixable — open App Analytics → Vault to purge them.",
                dedup_key=f"vault-structure-{today_utc()}",
                link={"app": "app-analytics"},
            )
        return record

    async def _vault_structure_sweep(self):
        """Weekly cron entry point — scan only, never purges automatically."""
        await self._run_vault_structure(purge=False, notify=True)

    @web_route("GET", "/api/vault-structure")
    async def api_vault_structure_status(self, request):
        """Last saved sweep result (from the cron OR a manual scan/purge),
        so the UI can render the current picture without re-running the scan
        on every page load."""
        state = self.load_state({})
        return {"sweep": state.get("vault_structure")}

    @web_route("POST", "/api/vault-structure/scan")
    async def api_vault_structure_scan(self, request):
        """On-demand, report-only scan — the "check now" button; never
        mutates the vault."""
        return await self._run_vault_structure(purge=False, notify=False)

    @web_route("POST", "/api/vault-structure/purge")
    async def api_vault_structure_purge(self, request):
        """The one-click fix (app-analytics-vault-structure-purge-action):
        removes only the auto-fixable 'safe' classes (empty dirs to
        fixpoint, byte-identical sync-conflict dupes, aged zero-byte files)
        after taking an fs_snapshot — never the 'review' items, which stay
        human-only by design (see scripts/check_vault_structure.py). The
        frontend must show the scanned safe_count and get an explicit
        confirm before calling this — it's an impact-shaped proposed action
        (.claude/rules/proposed-action.md), not a silent background purge."""
        return await self._run_vault_structure(purge=True, notify=False)

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

    def _summary_data(self, days: int = 30) -> dict:
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

    @web_route("GET", "/api/summary")
    async def api_summary(self, request):
        return self._summary_data(int(request.query_params.get("days", "30")))

    @web_route("GET", "/api/unused")
    async def api_unused(self, request):
        days = int(request.query_params.get("days", "30"))
        start = days_ago_utc(days - 1)
        end = today_utc()
        all_apps = set(self._all_app_ids())
        active = {r["key"] for r in self.usage.top("app", start=start, end=end, limit=200)}
        unused = sorted(all_apps - active)

        # One grouped statement for every app's last-seen day. This used to be
        # one full-table range() scan PER unused app on the event loop — 233
        # sync SQLite calls, ~0.4 s alone, but each one yields the GIL and
        # under fifteen CPU-bound audit threads that stretched to 37 s and the
        # watchdog killed the daemon (F-59, evidence 20260906T112242Z). Fewer
        # round-trips is the lever this handler owns; the loop still pays the
        # two remaining sync calls (top + last_bucket) — see _streaks_data and
        # api_errors_vs_usage for the same change.
        last_seen = self.usage.last_bucket("app")

        result = []
        for app_id in unused:
            last_date = last_seen.get(app_id)
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

        # Two grouped statements instead of two total() calls per app (466 sync
        # SQLite round-trips per page load) — the F-59 shape.
        views_by_app = self.usage.sums_by("app", start=start, end=end, where={"kind": "view"})
        events_by_app = self.usage.sums_by("app", start=start, end=end, where={"kind": "event"})

        result = []
        for app_id in all_apps:
            views = views_by_app.get(app_id, 0)
            events = events_by_app.get(app_id, 0)
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

    def _streaks_data(self) -> list[dict]:
        all_apps = self._all_app_ids()
        result = []
        now_week = _iso_week(today_utc())
        # One grouped statement for every app's active days; this was one
        # full-table range() scan per app (233 per page load) — the F-59 shape.
        buckets_by_app = self.usage.buckets_by("app")
        for app_id in all_apps:
            weeks = sorted({_iso_week(b) for b in buckets_by_app.get(app_id, []) if _iso_week(b)})
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

    @web_route("GET", "/api/streaks")
    async def api_streaks(self, request):
        return self._streaks_data()

    # ── Productivity split — which apps are work, which are life ────────
    # Closes the gap "no productive/distracting classification per app".
    # The classification + roll-up are pure (productivity.py); everything
    # here is the manifest walk, the override store, and the routes.
    #
    # Dark by default (project_feature_pipeline_flag_default_dark) — flip
    # [apps.app-analytics] feature.productivity-split.enabled = true. With
    # the flag off no route computes anything and the tab never renders,
    # so /api/summary and every other endpoint are byte-identical to
    # before this feature landed.

    def _productivity_enabled(self) -> bool:
        return bool(self.app_config("feature.productivity-split.enabled", False))

    def _productivity_overrides(self) -> dict[str, str]:
        """User reclassifications, keyed by app id. Stored in app state
        (``data/``) rather than a manifest: the manifest is shared
        community code, the classification is per-machine taste
        (CLAUDE.md rule 15). Malformed values are dropped on read so a
        hand-edited state file can't inject a fourth bucket."""
        raw = (self.load_state({}) or {}).get("productivity_overrides") or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(k): normalize_class(v)
            for k, v in raw.items()
            if normalize_class(v)
        }

    def _classified_apps(self) -> dict[str, dict]:
        blocks = {
            app_id: (m.raw.get("app") or {})
            for app_id, m in self.kernel.apps.manifests.items()
        }
        return classify_all(blocks, self._productivity_overrides())

    @web_route("GET", "/api/productivity")
    async def api_productivity(self, request):
        """Daily productive/neutral/personal split over `days` (default 30).

        The headline every competitor leads with, computed from the view
        counters this app already keeps — no new tracking, no new store."""
        if not self._productivity_enabled():
            return {
                "enabled": False,
                "reason": "Set [apps.app-analytics] feature.productivity-split.enabled = true",
            }
        try:
            days = int(request.query_params.get("days", "30"))
        except ValueError:
            days = 30
        days = max(1, min(days, 365))

        rows = self.usage.range(
            start=days_ago_utc(days - 1), end=today_utc(), where={"kind": "view"}
        )
        classes = self._classified_apps()
        summary = summarize_usage(rows, classes)
        names = self.kernel.apps.manifests
        for row in summary["apps"]:
            m = names.get(row["app"])
            row["name"] = (m.name if m else "") or row["app"]
        return {
            "enabled": True,
            "days": days,
            "classes": list(PRODUCTIVITY_CLASSES),
            "labels": PRODUCTIVITY_LABELS,
            **summary,
        }

    @web_route("POST", "/api/productivity/classify")
    async def api_productivity_classify(self, request):
        """Reclassify one app, or clear the override to fall back to
        inference. Inference is only a starting point — being able to
        correct it is the feature, not a nicety."""
        if not self._productivity_enabled():
            return {"error": "productivity split is disabled"}
        body = await request.json()
        app_id = str((body or {}).get("app") or "").strip()
        if not app_id:
            return {"error": "app is required"}
        if app_id not in self.kernel.apps.manifests:
            return {"error": f"no such app: {app_id}"}
        wanted = normalize_class((body or {}).get("class"))

        # Persist under the same lock the vault-structure writer takes:
        # both do read-modify-write on ONE state file, so they must share
        # a lock or the later save wipes the earlier writer's key.
        async with self.write_lock("state"):
            state = self.load_state({}) or {}
            overrides = state.get("productivity_overrides")
            if not isinstance(overrides, dict):
                overrides = {}
            if wanted:
                overrides[app_id] = wanted
            else:
                overrides.pop(app_id, None)
            state["productivity_overrides"] = overrides
            self.save_state(state)

        resolved = self._classified_apps().get(app_id, {})
        await self.emit(
            "app-analytics:productivity_classified",
            {"app": app_id, "class": resolved.get("class"), "source": resolved.get("source")},
        )
        return {"ok": True, "app": app_id, **resolved}

    # ── AI digest — the app declares `think` in its manifest but never
    # called it (found during the 2026-08 gap-analysis pass). This turns
    # the existing summary/streaks aggregation into a short weekly recap
    # instead of leaving the capability declared-but-dark.

    async def _build_digest(self) -> str | None:
        summary = self._summary_data(days=7)
        streaks = self._streaks_data()
        if not summary.get("views_7d"):
            return None
        top_streaks = ", ".join(
            f"{r['app']} ({r['current_weeks']}w)" for r in streaks[:3] if r["current_weeks"] > 0
        )
        lines = [
            f"views today: {summary['views_today']}",
            f"views last 7 days: {summary['views_7d']}",
            f"active apps (7d): {summary['active_apps_7d']} of {summary['total_apps']}",
            f"unused apps (30d): {summary['unused_apps_30d']}",
        ]
        if top_streaks:
            lines.append(f"current weekly streaks: {top_streaks}")
        return await self.think_safe(
            "\n".join(lines), system=DIGEST_SYSTEM, domain="text", temperature=0.4, fallback=""
        )

    @web_route("GET", "/api/digest")
    async def api_digest(self, request):
        digest = await self._build_digest()
        if not digest:
            return {"digest": "", "provenance": None}
        return {"digest": digest, "provenance": self.last_provenance()}

    async def panel_ai_digest(self) -> dict | None:
        """Hub: a short AI recap of this week's usage. Lazy — one `think()` call."""
        digest = await self._build_digest()
        if not digest:
            return None
        return {"title": "This week", "body": digest}

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
