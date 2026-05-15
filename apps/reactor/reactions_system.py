"""System reactions — vault, git, staff, integrity, reflect, publish, billing."""

from __future__ import annotations

import datetime as _dt

from emptyos.sdk import on_event


class SystemReactionsMixin:
    # ── Vault ──

    @on_event("vault:changed")
    async def on_vault_change(self, event):
        path = event.data.get("path", "")
        change = event.data.get("change", "")
        inbox = self.vault_config("inbox_dir", "00_Inbox")
        if inbox in path and change == "added":
            self._log_action("vault:changed", f"new inbox item: {path}")

    @on_event("note:created")
    async def on_note_created(self, event):
        self._log_action("note:created", event.data.get("path", "")[:50])

    @on_event("note:updated")
    async def on_note_updated(self, event):
        self._log_action("note:updated", event.data.get("path", "")[:50])

    @on_event("media:updated")
    async def on_media_updated(self, event):
        self._log_action("media:updated", event.data.get("file", "")[:40])

    # ── Capture / inbox / quickref ──

    @on_event("capture:saved")
    async def on_capture(self, event):
        self._log_action("capture:saved", "new capture")

    @on_event("capture:archived")
    async def on_capture_archived(self, event):
        self._log_action("capture:archived", f"{event.data.get('count', 0)} archived")

    @on_event("bookmarks:saved")
    async def on_bookmark_saved(self, event):
        title = event.data.get("title", "")[:40]
        self._log_action("bookmarks:saved", f"saved: {title}")

    @on_event("bookmarks:archived")
    async def on_bookmark_archived(self, event):
        self._log_action("bookmarks:archived", f"{event.data.get('count', 1)} archived")

    @on_event("quickref:added")
    async def on_quickref_added(self, event):
        title = event.data.get("title", "")[:40]
        self._log_action("quickref:added", f"card: {title}")

    @on_event("quickref:updated")
    async def on_quickref_updated(self, event):
        card_id = event.data.get("id", "")
        self._log_action("quickref:updated", f"card: {card_id}")

    @on_event("quotes:shown")
    async def on_quote_shown(self, event):
        self._log_action("quotes:shown", event.data.get("quote", "")[:40])

    @on_event("quotes:generated")
    async def on_quote_generated(self, event):
        self._log_action("quotes:generated", event.data.get("topic", "")[:30])

    @on_event("tmpl:used")
    async def on_tmpl_used(self, event):
        self._log_action("tmpl:used", event.data.get("template", "")[:30])

    @on_event("timeline:event_added")
    async def on_timeline_event(self, event):
        self._log_action("timeline:event_added", event.data.get("title", "")[:40])

    @on_event("handsfree:dispatched")
    async def on_handsfree_dispatched(self, event):
        intent = event.data.get("intent", "")[:40]
        target = event.data.get("target", "")[:40]
        outcome = event.data.get("outcome", "")[:20]
        self._log_action("handsfree:dispatched", f"{intent} → {target} ({outcome})")

    # ── Journal ──

    @on_event("journal:entry")
    async def on_journal(self, event):
        mood = event.data.get("mood", "")
        if mood in ("low", "bad"):
            msg = f"😔 You logged feeling {mood}. Consider a break or grounding exercise."
            await self._notify(msg, priority="info")
            await self._telegram(msg)
            self._log_action("journal:entry", "low mood support sent")

    @on_event("journal:created")
    async def on_journal_created(self, event):
        self._log_action("journal:created", "new daily note")

    @on_event("journal:reflection")
    async def on_journal_reflection(self, event):
        self._log_action("journal:reflection", "AI reflection generated")

    @on_event("journal:milestone")
    async def on_journal_milestone(self, event):
        self._log_action("journal:milestone", event.data.get("text", "")[:40])

    @on_event("journal:three-things")
    async def on_three_things(self, event):
        self._log_action("journal:three-things", "three wins logged")

    # ── Git / GitHub ──

    @on_event("git:saved")
    async def on_git_saved(self, event):
        msg = event.data.get("message", "")[:60]
        self._log_action("git:saved", msg)
        await self._journal_ripple("💻", f"Committed: {msg}")

    @on_event("git:pushed")
    async def on_git_pushed(self, event):
        self._log_action("git:pushed", "vault pushed to remote")

    @on_event("git:pulled")
    async def on_git_pulled(self, event):
        self._log_action("git:pulled", "vault pulled from remote")

    @on_event("github:synced")
    async def on_github_synced(self, event):
        self._log_action("github:synced", f"synced: {event.data.get('repo', '')[:30]}")

    @on_event("github:pr_status")
    async def on_github_pr(self, event):
        pr = event.data.get("pr", "")
        status = event.data.get("status", "")
        self._log_action("github:pr_status", f"PR {pr}: {status}")

    # ── Staff agents ──

    @on_event("staff:shift_started")
    async def on_staff_started(self, event):
        self._log_action("staff:shift_started", f"agent: {event.data.get('agent', '')}")

    @on_event("staff:shift_completed")
    async def on_staff_shift(self, event):
        agent = event.data.get("agent_id", "")
        actions = event.data.get("actions", 0)
        self._log_action("staff:shift_completed", f"agent shift: {agent}")
        if actions and agent == "growth-agent":
            try:
                await self.call_app(
                    "journal",
                    "_add_entry",
                    d=_dt.date.today(),
                    text=f"Growth Agent: {actions} action(s) taken",
                    mood="good",
                )
            except Exception:
                pass

    # ── Integrity / reflection / system ──

    @on_event("integrity:audit_completed")
    async def on_integrity_audit(self, event):
        pct = event.data.get("pct", 0)
        self._log_action("integrity:audit_completed", f"integrity: {pct}%")
        weak = event.data.get("weak_verbs", [])
        if weak:
            msg = f"🧬 Weak lifecycle verbs: {', '.join(weak)}"
            self._log_action("integrity:verb_health", msg)
            await self._journal_ripple(
                "🧬", f"Verb health alert: {', '.join(weak)} need strengthening"
            )

    @on_event("reflect:insight")
    async def on_reflect_insight(self, event):
        self._log_action("reflect:insight", event.data.get("summary", "")[:60])

    @on_event("reflect:agent_modified")
    async def on_agent_self_modified(self, event):
        agent_id = event.data.get("agent_id", "?")
        action = event.data.get("action", "?")
        reason = event.data.get("reason", "")
        self._log_action("reflect:agent_modified", f"{agent_id}: {action} — {reason[:80]}")
        await self._telegram(f"🧬 Self-modification: {agent_id} → {action}\n{reason[:100]}")

    @on_event("system:reflected")
    async def on_system_reflected(self, event):
        health = event.data.get("health", "unknown")
        insights = event.data.get("insight_count", 0)
        mods = event.data.get("modifications", 0)
        self._log_action("system:reflected", f"health={health}, {insights} insights, {mods} mods")
        await self._journal_ripple("🪞", f"System reflected: {health} — {insights} insights")
        if health == "declining":
            await self._notify(
                f"System health declining — {insights} insights found", priority="warning"
            )
            await self._telegram(
                f"⚠️ System health declining. {insights} insights, {mods} self-modifications applied."
            )

    @on_event("settings:changed")
    async def on_settings_changed(self, event):
        key = event.data.get("key", event.data.get("keys", ""))
        self._log_action("settings:changed", f"key: {str(key)[:40]}")

    @on_event("store:installed")
    async def on_store_installed(self, event):
        what = event.data.get("kind", "app")
        ident = event.data.get("id", "")
        self._log_action("store:installed", f"{what}: {ident}")

    @on_event("store:uninstalled")
    async def on_store_uninstalled(self, event):
        what = event.data.get("kind", "app")
        ident = event.data.get("id", "")
        self._log_action("store:uninstalled", f"{what}: {ident}")

    @on_event("store:enabled")
    async def on_store_enabled(self, event):
        what = event.data.get("kind", "app")
        ident = event.data.get("id", "")
        self._log_action("store:enabled", f"{what}: {ident}")

    @on_event("store:disabled")
    async def on_store_disabled(self, event):
        what = event.data.get("kind", "app")
        ident = event.data.get("id", "")
        self._log_action("store:disabled", f"{what}: {ident}")

    @on_event("forge:scaffolded")
    async def on_forge_scaffolded(self, event):
        target = event.data.get("target") or event.data.get("name", "")
        self._log_action("forge:scaffolded", f"new target: {target}")

    @on_event("forge:built")
    async def on_forge_built(self, event):
        target = event.data.get("target") or event.data.get("name", "")
        self._log_action("forge:built", f"target: {target}")

    @on_event("run:completed")
    async def on_run_completed(self, event):
        cmd = event.data.get("command", "")[:40]
        self._log_action("run:completed", f"cmd: {cmd}")

    @on_event("app-gen:created")
    async def on_app_gen(self, event):
        app_id = event.data.get("id", "")
        self._log_action("app-gen:created", f"new mini-app: {app_id}")

    @on_event("plugin-gen:created")
    async def on_plugin_gen(self, event):
        pid = event.data.get("id", "")
        self._log_action("plugin-gen:created", f"new plugin: {pid}")

    @on_event("model-bench:completed")
    async def on_model_bench(self, event):
        scenarios = event.data.get("scenarios", 0)
        self._log_action("model-bench:completed", f"{scenarios} scenarios benchmarked")

    @on_event("model-bench:agent_run_completed")
    async def on_model_bench_agent(self, event):
        turns = event.data.get("turns")
        self._log_action(
            "model-bench:agent_run_completed", f"turns: {turns}" if turns is not None else ""
        )

    @on_event("model-bench:chain_applied")
    async def on_model_bench_chain(self, event):
        bucket = event.data.get("bucket", "")
        self._log_action("model-bench:chain_applied", f"bucket: {bucket}")

    @on_event("providers:changed")
    async def on_providers_changed(self, event):
        count = event.data.get("count")
        self._log_action("providers:changed", f"count: {count}" if count is not None else "")

    @on_event("web-analytics:hit")
    async def on_web_analytics_hit(self, event):
        pass

    @on_event("release:packaged")
    async def on_release_packaged(self, event):
        tier = event.data.get("tier", "?")
        version = event.data.get("version", "?")
        self._log_action("release:packaged", f"{tier} v{version}")

    @on_event("ai-queue:completed")
    async def on_ai_queue_done(self, event):
        text = event.data.get("text", "")[:40]
        self._log_action("ai-queue:completed", f"task automated: {text}")

    @on_event("ai-queue:classified")
    async def on_ai_classified(self, event):
        pass

    @on_event("ai-queue:queued")
    async def on_ai_queued(self, event):
        pass

    @on_event("items:added")
    async def on_item_added(self, event):
        self._log_action("items:added", "new item tracked")

    @on_event("items:updated")
    async def on_item_updated(self, event):
        self._log_action("items:updated", "item updated")

    @on_event("hub:refreshed")
    async def on_hub_refreshed(self, event):
        pass

    @on_event("link:scan_completed")
    async def on_link_scan(self, event):
        self._log_action(
            "link:scan_completed",
            f"notes: {event.data.get('total_notes', 0)}, orphans: {event.data.get('orphan_count', 0)}",
        )

    @on_event("system-log:feed_viewed")
    async def on_syslog_feed(self, event):
        self._log_action("system-log:feed_viewed", "feed viewed")

    # ── Tests ──

    @on_event("tests:run_started")
    async def on_tests_run_started(self, event):
        path = event.data.get("path", "")
        self._log_action("tests:run_started", path or "all")

    @on_event("tests:run_completed")
    async def on_tests_run_completed(self, event):
        path = event.data.get("path", "")
        passed = event.data.get("passed", 0)
        failed = event.data.get("failed", 0)
        errors = event.data.get("errors", 0)
        self._log_action("tests:run_completed", f"{path}: {passed}p/{failed}f/{errors}e")
        if failed or errors:
            await self._telegram(f"🧪 Tests failed: {path} — {failed}f / {errors}e")

    # ── Publishing ──

    @on_event("publish:built")
    async def on_publish_built(self, event):
        site = event.data.get("site", "default")
        pages = event.data.get("pages", 0)
        self._log_action("publish:built", f"site built: {site} ({pages} pages)")
        await self._journal_ripple("🏗️", f"Built site '{site}' ({pages} pages)")

    @on_event("publish:deployed")
    async def on_publish_deployed(self, event):
        site = event.data.get("site", "default")
        self._log_action("publish:deployed", f"site deployed: {site}")
        await self._journal_ripple("🚀", f"Deployed site '{site}'")
        await self._notify(f"Site deployed: {site}", priority="info")
        await self._telegram(f"🚀 Site deployed: {site}")

    # ── Billing ──

    @on_event("billing:budget_alert")
    async def on_budget_alert(self, event):
        total = event.data.get("total", 0)
        budget = event.data.get("budget", 0)
        self._log_action("billing:budget_alert", f"budget exceeded: ${total} > ${budget}")
        await self._journal_ripple("💸", f"Daily AI budget exceeded: ${total:.2f} / ${budget:.2f}")

    @on_event("billing:report_generated")
    async def on_billing_report(self, event):
        month = event.data.get("month", "")
        self._log_action("billing:report_generated", f"report: {month}")

    # ── Engineering calcs ──

    @on_event("cable-pulling:calculated")
    async def on_cable_calc(self, event):
        self._log_action(
            "cable-pulling:calculated", f"tension: {event.data.get('max_tension_kN', 0)} kN"
        )

    @on_event("cable:rating_calculated")
    async def on_cable_rating(self, event):
        self._log_action("cable:rating_calculated", "cable rating done")

    @on_event("sheath-voltage:calculated")
    async def on_sheath_calc(self, event):
        self._log_action("sheath-voltage:calculated", "cable calculation done")

    @on_event("hdd-estimator:calculated")
    async def on_hdd_calculated(self, event):
        self._log_action("hdd-estimator:calculated", "HDD footprint calculated")

    @on_event("retirement:calculated")
    async def on_retirement_calc(self, event):
        self._log_action("retirement:calculated", "retirement scenario calculated")

    @on_event("net-worth:snapshot-saved")
    async def on_net_worth_snapshot(self, event):
        self._log_action("net-worth:snapshot-saved", "net worth snapshot saved")
        await self._journal_ripple("💰", "Net worth snapshot saved")

    # ── Agent ──
    # Lifecycle events get logged; per-turn streaming telemetry registers
    # silently so the audit sees them as heard without spamming the log.

    @on_event("agent:done")
    async def on_agent_done(self, event):
        usage = event.data.get("usage", {})
        iters = event.data.get("iters")
        detail = f"iters: {iters}" if iters is not None else "done"
        if isinstance(usage, dict) and usage:
            tok = usage.get("output_tokens") or usage.get("total_tokens")
            if tok:
                detail += f", tokens: {tok}"
        self._log_action("agent:done", detail)

    @on_event("agent:error")
    async def on_agent_error(self, event):
        err = str(event.data.get("error", ""))[:80]
        self._log_action("agent:error", err)

    @on_event("agent:max_iters")
    async def on_agent_max_iters(self, event):
        iters = event.data.get("iters", "?")
        self._log_action("agent:max_iters", f"hit max: {iters}")

    @on_event("agent:cancelled")
    async def on_agent_cancelled(self, event):
        self._log_action("agent:cancelled", "turn cancelled")

    @on_event("agent:permission_requested")
    async def on_agent_permission_requested(self, event):
        tool = event.data.get("tool") or event.data.get("name", "")
        self._log_action("agent:permission_requested", f"tool: {tool}")

    @on_event("agent:permission_resolved")
    async def on_agent_permission_resolved(self, event):
        pass

    @on_event("agent:turn_start")
    async def on_agent_turn_start(self, event):
        pass

    @on_event("agent:iter_start")
    async def on_agent_iter_start(self, event):
        pass

    @on_event("agent:text")
    async def on_agent_text(self, event):
        pass

    @on_event("agent:tool_call")
    async def on_agent_tool_call(self, event):
        pass

    @on_event("agent:tool_result")
    async def on_agent_tool_result(self, event):
        pass

    # ── Dogfood agent + fix-agent loop ──
    # The test-fix-verify infrastructure emits these as bookkeeping
    # signals; the reactor logs them so the integrity audit's
    # unheard-events count stays clean.

    @on_event("dogfood:ui_walk_finished")
    async def on_dogfood_ui_walk_finished(self, event):
        preset = event.data.get("preset", "")
        steps = event.data.get("step_count")
        ok = event.data.get("ok")
        parts = []
        if preset:
            parts.append(f"preset {preset}")
        if isinstance(steps, int):
            parts.append(f"{steps} steps")
        if ok is False:
            parts.append("FAILED")
        self._log_action("dogfood:ui_walk_finished", " · ".join(parts) or "ui walk done")

    @on_event("fix-agent:repro_finished")
    async def on_fix_agent_repro_finished(self, event):
        verdict = event.data.get("verdict") or event.data.get("status") or "done"
        url = (event.data.get("url") or "")[:60]
        detail = f"verdict: {verdict}"
        if url:
            detail += f" ({url})"
        self._log_action("fix-agent:repro_finished", detail)

    @on_event("dogfood:queue_drained")
    async def on_dogfood_queue_drained(self, event):
        applied = event.data.get("applied", 0)
        stuck = event.data.get("stuck", 0)
        self._log_action("dogfood:queue_drained", f"applied: {applied}, stuck: {stuck}")

    # ── Sandbox pool ──

    @on_event("sandbox:leased")
    async def on_sandbox_leased(self, event):
        port = event.data.get("port", "?")
        purpose = (event.data.get("purpose") or "")[:60]
        self._log_action("sandbox:leased", f"port {port}" + (f" — {purpose}" if purpose else ""))

    @on_event("sandbox:released")
    async def on_sandbox_released(self, event):
        self._log_action("sandbox:released", "lease released")

    @on_event("sandbox:restarted")
    async def on_sandbox_restarted(self, event):
        port = event.data.get("port", "?")
        self._log_action("sandbox:restarted", f"port {port}")
