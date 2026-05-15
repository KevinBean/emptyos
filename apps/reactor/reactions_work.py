"""Work reactions — tasks, projects, jobs, briefings, assistant, news."""

from __future__ import annotations

from emptyos.sdk import on_event


class WorkReactionsMixin:
    @on_event("task:added")
    async def on_task_added(self, event):
        self._log_action("task:added", event.data.get("text", "")[:40])

    @on_event("task:completed")
    async def on_task_done(self, event):
        text = event.data.get("text", "")[:50]
        self._log_action("task:completed", f"task done: {text}")
        await self._journal_ripple("✅", f"Completed: {text}")

    @on_event("task:snoozed")
    async def on_task_snoozed(self, event):
        self._log_action("task:snoozed", event.data.get("text", "")[:40])

    @on_event("task:reopened")
    async def on_task_reopened(self, event):
        self._log_action("task:reopened", event.data.get("text", "")[:40])

    @on_event("projects:created")
    async def on_project_created(self, event):
        self._log_action("projects:created", event.data.get("name", "")[:40])

    @on_event("projects:task_added")
    async def on_project_task_added(self, event):
        self._log_action("projects:task_added", event.data.get("text", "")[:40])

    @on_event("projects:task_toggled")
    async def on_project_task(self, event):
        self._log_action("projects:task_toggled", "project task toggled")

    @on_event("projects:status_changed")
    async def on_project_status(self, event):
        self._log_action("projects:status_changed", event.data.get("status", ""))

    @on_event("projects:stage_changed")
    async def on_project_stage(self, event):
        self._log_action(
            "projects:stage_changed",
            f"{event.data.get('project', '')}: {event.data.get('stage', '')}",
        )

    @on_event("projects:calc_attached")
    async def on_project_calc(self, event):
        self._log_action("projects:calc_attached", f"{event.data.get('project', '')}")

    @on_event("projects:feature_toggled")
    async def on_project_feature(self, event):
        self._log_action(
            "projects:feature_toggled",
            f"{event.data.get('project', '')}: {event.data.get('feature', '')}",
        )

    @on_event("projects:sprint_created")
    async def on_project_sprint(self, event):
        self._log_action("projects:sprint_created", f"{event.data.get('project', '')}")

    @on_event("projects:sprint_closed")
    async def on_project_sprint_closed(self, event):
        self._log_action("projects:sprint_closed", f"{event.data.get('project', '')}")

    @on_event("projects:milestone_created")
    async def on_project_milestone(self, event):
        self._log_action(
            "projects:milestone_created",
            f"{event.data.get('project', '')}: {event.data.get('name', '')}",
        )

    @on_event("projects:release_created")
    async def on_project_release(self, event):
        self._log_action(
            "projects:release_created",
            f"{event.data.get('project', '')}: v{event.data.get('version', '')}",
        )

    @on_event("projects:doc_created")
    async def on_project_doc(self, event):
        project = event.data.get("project", "")
        doc = event.data.get("doc", "")[:30]
        self._log_action("projects:doc_created", f"{project}: {doc}")

    @on_event("projects:refreshed")
    async def on_projects_refreshed(self, event):
        pass

    @on_event("jobs:application_added")
    async def on_jobs_app_added(self, event):
        company = event.data.get("company", "")[:30]
        self._log_action("jobs:application_added", f"applied: {company}")
        await self._journal_ripple("📨", f"Applied to {company}")

    @on_event("jobs:status_changed")
    async def on_jobs_status(self, event):
        company = event.data.get("company", "")[:30]
        status = event.data.get("status", "")
        self._log_action("jobs:status_changed", f"{company}: {status}")

    @on_event("jobs:briefing_generated")
    async def on_jobs_briefing(self, event):
        company = event.data.get("company", "")[:30]
        self._log_action("jobs:briefing_generated", f"briefing: {company}")

    @on_event("jobs:session_started")
    async def on_jobs_session_start(self, event):
        self._log_action("jobs:session_started", event.data.get("type", "")[:30])

    @on_event("jobs:session_ended")
    async def on_jobs_session_end(self, event):
        self._log_action("jobs:session_ended", event.data.get("type", "")[:30])

    @on_event("jobs:scrape_complete")
    async def on_jobs_scrape(self, event):
        count = event.data.get("count", 0)
        self._log_action("jobs:scrape_complete", f"{count} listings scraped")

    @on_event("jobs:learnings_harvested")
    async def on_jobs_learnings(self, event):
        count = event.data.get("count", 0)
        self._log_action("jobs:learnings_harvested", f"{count} insights")

    @on_event("jobs:gap_detected")
    async def on_jobs_gap_detected(self, event):
        count = len(event.data.get("gaps") or [])
        src = event.data.get("source", "")
        self._log_action("jobs:gap_detected", f"{count} gap(s) from {src}")

    @on_event("jobs:prep_actions_ready")
    async def on_jobs_prep_ready(self, event):
        count = len(event.data.get("actions") or [])
        company = event.data.get("company", "")[:30]
        self._log_action("jobs:prep_actions_ready", f"{count} prep task(s) for {company}")

    # career:gap_promoted and career:goal_created handlers live below
    # (richer log strings + dimension-tagged journal ripples). The earlier
    # stub copies were removed 2026-04-27 — they shadowed the real ones
    # silently (ruff F811).

    @on_event("career:skill_level_up")
    async def on_career_skill_level_up(self, event):
        skill = event.data.get("skill", "")[:40]
        to = event.data.get("to", "")
        self._log_action("career:skill_level_up", f"{skill} → {to}")
        await self._journal_ripple("📈", f"Levelled up: {skill} → {to}")

    @on_event("briefing:generated")
    async def on_briefing(self, event):
        self._log_action("briefing:generated", "morning briefing")

    @on_event("assistant:message")
    async def on_assistant_msg(self, event):
        self._log_action("assistant:message", f"session {event.data.get('session', '')[:8]}")

    @on_event("rooms:chat")
    async def on_rooms_chat(self, event):
        # rooms:chat stays internal-only — every turn would flood the journal.
        # Lifecycle events (created / archived / exported / distilled / action)
        # are the journal-worthy ones below.
        self._log_action("rooms:chat", f"persona: {event.data.get('persona', '')}")

    @on_event("rooms:created")
    async def on_rooms_created(self, event):
        title = event.data.get("title") or event.data.get("room_id", "")
        self._log_action("rooms:created", title[:40])
        await self._journal_ripple("🏠", f"New room: {title}")

    @on_event("rooms:archived")
    async def on_rooms_archived(self, event):
        rid = event.data.get("room_id", "")
        self._log_action("rooms:archived", rid[:40])
        await self._journal_ripple("🗄️", f"Archived room: {rid}")

    @on_event("rooms:exported")
    async def on_rooms_exported(self, event):
        path = event.data.get("path", "")
        self._log_action("rooms:exported", path[:60])
        await self._journal_ripple("📄", f"Exported room thread to {path}")

    @on_event("rooms:distilled")
    async def on_rooms_distilled(self, event):
        path = event.data.get("path", "")
        n = event.data.get("message_count", 0)
        self._log_action("rooms:distilled", f"{n} msgs → {path[:40]}")
        await self._journal_ripple("✨", f"Distilled {n} messages into {path}")

    @on_event("rooms:action_applied")
    async def on_rooms_action_applied(self, event):
        verb = f"{event.data.get('app', '?')}.{event.data.get('method', '?')}"
        rid = event.data.get("room_id", "")
        self._log_action("rooms:action_applied", verb)
        await self._journal_ripple("✓", f"Applied {verb} from room {rid}")

    @on_event("rooms:action_rejected")
    async def on_rooms_action_rejected(self, event):
        verb = f"{event.data.get('app', '?')}.{event.data.get('method', '?')}"
        self._log_action("rooms:action_rejected", verb)

    @on_event("rooms:participant_added")
    async def on_rooms_participant_added(self, event):
        pid = event.data.get("id") or event.data.get("participant", "")
        self._log_action("rooms:participant_added", str(pid)[:40])

    @on_event("rooms:pinned")
    async def on_rooms_pinned(self, event):
        rid = event.data.get("room_id", "")
        self._log_action("rooms:pinned", str(rid)[:40])

    @on_event("digest:generated")
    async def on_digest(self, event):
        self._log_action("digest:generated", "daily digest created")

    @on_event("dashboard:generated")
    async def on_dashboard(self, event):
        self._log_action("dashboard:generated", "dashboard refreshed")

    @on_event("news-center:summarized")
    async def on_news_summarized(self, event):
        self._log_action("news-center:summarized", "news digest ready")

    @on_event("news-center:fetched")
    async def on_news_fetched(self, event):
        pass

    @on_event("review:completed")
    async def on_review(self, event):
        self._log_action("review:completed", "weekly review done")
        await self._journal_ripple("📋", "Weekly review completed")

    @on_event("search:query")
    async def on_search(self, event):
        self._log_action("search:query", event.data.get("query", "")[:40])

    @on_event("reports:created")
    async def on_report_created(self, event):
        title = event.data.get("title", "")[:50]
        rtype = event.data.get("type", "report")
        self._log_action("reports:created", f"{rtype}: {title}")
        await self._journal_ripple("📄", f"Started {rtype}: {title}")

    @on_event("reports:section-updated")
    async def on_report_section_updated(self, event):
        doc_id = event.data.get("id", "")
        slug = event.data.get("slug") or event.data.get("field") or event.data.get("table") or ""
        self._log_action("reports:section-updated", f"{doc_id}: {slug}")

    @on_event("reports:exported")
    async def on_report_exported(self, event):
        fmt = event.data.get("format", "?")
        doc_id = event.data.get("id", "")
        self._log_action("reports:exported", f"{doc_id} -> {fmt}")
        await self._journal_ripple("📤", f"Exported report {doc_id} as {fmt.upper()}")

    @on_event("board:created")
    async def on_board_created(self, event):
        self._log_action("board:created", event.data.get("name", "")[:40])

    @on_event("board:config_updated")
    async def on_board_config_updated(self, event):
        self._log_action("board:config_updated", event.data.get("id", "")[:40])

    @on_event("board:column_added")
    async def on_board_column_added(self, event):
        self._log_action(
            "board:column_added", f"{event.data.get('id', '')}: +{event.data.get('col', '')}"
        )

    @on_event("board:column_updated")
    async def on_board_column_updated(self, event):
        self._log_action(
            "board:column_updated", f"{event.data.get('id', '')}: {event.data.get('col', '')}"
        )

    @on_event("board:column_deleted")
    async def on_board_column_deleted(self, event):
        self._log_action(
            "board:column_deleted", f"{event.data.get('id', '')}: -{event.data.get('col', '')}"
        )

    @on_event("board:item_created")
    async def on_board_item_created(self, event):
        self._log_action(
            "board:item_created", f"{event.data.get('board', '')}/{event.data.get('file', '')}"
        )

    @on_event("board:item_updated")
    async def on_board_item_updated(self, event):
        self._log_action(
            "board:item_updated", f"{event.data.get('board', '')}/{event.data.get('file', '')}"
        )

    @on_event("board:item_moved")
    async def on_board_item_moved(self, event):
        self._log_action(
            "board:item_moved", f"{event.data.get('board', '')}: {event.data.get('file', '')}"
        )

    @on_event("board:item_archived")
    async def on_board_item_archived(self, event):
        self._log_action(
            "board:item_archived", f"{event.data.get('board', '')}/{event.data.get('file', '')}"
        )

    @on_event("board:view_saved")
    async def on_board_view_saved(self, event):
        self._log_action(
            "board:view_saved", f"{event.data.get('board', '')}: {event.data.get('view', '')}"
        )

    @on_event("board:view_deleted")
    async def on_board_view_deleted(self, event):
        self._log_action(
            "board:view_deleted", f"{event.data.get('board', '')}: {event.data.get('view', '')}"
        )

    @on_event("career:gap_promoted")
    async def on_career_gap_promoted(self, event):
        skill = event.data.get("skill", "")[:40]
        self._log_action("career:gap_promoted", f"gap → goal: {skill}")
        await self._journal_ripple("🎯", f"Promoted skill gap to goal: {skill}", dim="occupational")

    @on_event("career:goal_created")
    async def on_career_goal_created(self, event):
        skill = event.data.get("skill", "")[:40]
        self._log_action("career:goal_created", f"goal: {skill}")
        await self._journal_ripple("🎯", f"Created career goal: {skill}", dim="occupational")

    # ── Coding agent (apps/agent/) — turn + session lifecycle signals ──
    # These fire from AgentApp.ws_turn and agent_loop.run_turn. Kept
    # observability-only by default: the agent is a power-user tool and
    # we don't want every iteration spamming the daily journal. The one
    # exception is skill_loaded — invoking a Claude-Code skill is a
    # user-initiated action worth a journal ripple.

    @on_event("agent:skill_loaded")
    async def on_agent_skill_loaded(self, event):
        name = event.data.get("name", "")[:60]
        self._log_action("agent:skill_loaded", f"/{name}")
        if name:
            await self._journal_ripple("🔧", f"Invoked agent skill: /{name}", dim="occupational")

    @on_event("agent:orient")
    async def on_agent_orient(self, event):
        plan = event.data.get("plan") or {}
        tt = (plan.get("task_type") or "")[:20]
        steps = len(plan.get("investigation_plan") or [])
        self._log_action("agent:orient", f"{tt} — {steps} steps")

    @on_event("agent:plan_mode")
    async def on_agent_plan_mode(self, event):
        on = event.data.get("on")
        self._log_action("agent:plan_mode", "ON" if on else "off")

    @on_event("agent:plan_nudge")
    async def on_agent_plan_nudge(self, event):
        iter_n = event.data.get("iter") or event.data.get("iteration") or "?"
        self._log_action("agent:plan_nudge", f"iter {iter_n}")

    @on_event("agent:compacted")
    async def on_agent_compacted(self, event):
        saved = event.data.get("chars_saved") or 0
        count = event.data.get("message_count") or 0
        self._log_action("agent:compacted", f"saved ~{saved} chars ({count} msgs)")

    @on_event("task:updated")
    async def on_task_updated(self, event):
        self._log_action("task:updated", event.data.get("text", "")[:40])

    @on_event("staff:workflow_started")
    async def on_staff_started(self, event):
        wf = event.data.get("workflow") or event.data.get("name") or "?"
        self._log_action("staff:workflow_started", str(wf)[:40])

    @on_event("staff:workflow_completed")
    async def on_staff_completed(self, event):
        wf = event.data.get("workflow") or event.data.get("name") or "?"
        self._log_action("staff:workflow_completed", str(wf)[:40])
        await self._journal_ripple("👔", f"Staff workflow completed: `{wf}`")

    @on_event("staff:workflow_failed")
    async def on_staff_failed(self, event):
        wf = event.data.get("workflow") or event.data.get("name") or "?"
        err = (event.data.get("error") or "")[:60]
        self._log_action("staff:workflow_failed", f"{wf}: {err}")
        await self._journal_ripple("⚠️", f"Staff workflow failed: `{wf}` — {err}")

    @on_event("canvas:node_added")
    async def on_canvas_node_added(self, event):
        board = event.data.get("board_id") or event.data.get("board") or ""
        self._log_action("canvas:node_added", str(board)[:40])

    @on_event("canvas:promoted")
    async def on_canvas_promoted(self, event):
        target = event.data.get("target") or event.data.get("note") or ""
        self._log_action("canvas:promoted", str(target)[:60])

    @on_event("asset-register:asset_added")
    async def on_asset_added(self, event):
        name = event.data.get("name") or event.data.get("id") or ""
        self._log_action("asset-register:asset_added", str(name)[:60])
        await self._journal_ripple("📋", f"Asset registered: `{name}`")

    @on_event("contacts:enriched")
    async def on_contacts_enriched(self, event):
        name = event.data.get("name") or event.data.get("contact") or ""
        self._log_action("contacts:enriched", str(name)[:40])

    @on_event("contacts:chat_archived")
    async def on_contacts_chat_archived(self, event):
        name = event.data.get("name") or event.data.get("contact") or ""
        self._log_action("contacts:chat_archived", str(name)[:40])

    @on_event("plan-scenarios:proposed")
    async def on_plan_proposed(self, event):
        title = event.data.get("title") or event.data.get("scenario") or ""
        self._log_action("plan-scenarios:proposed", str(title)[:60])

    @on_event("plan-scenarios:decided")
    async def on_plan_decided(self, event):
        title = event.data.get("title") or event.data.get("scenario") or ""
        self._log_action("plan-scenarios:decided", str(title)[:60])
        await self._journal_ripple("🧭", f"Decision: `{title}`")

    @on_event("routing:planned")
    async def on_routing_planned(self, event):
        stops = event.data.get("stops") or event.data.get("count") or "?"
        self._log_action("routing:planned", f"{stops} stops")

    @on_event("drone-flight:route_planned")
    async def on_drone_route(self, event):
        name = event.data.get("name") or event.data.get("id") or ""
        self._log_action("drone-flight:route_planned", str(name)[:40])

    @on_event("drone-images:defect_saved")
    async def on_drone_defect(self, event):
        kind = event.data.get("type") or event.data.get("defect") or ""
        self._log_action("drone-images:defect_saved", str(kind)[:40])

    @on_event("inspection-queue:work_added")
    async def on_inspection_added(self, event):
        title = event.data.get("title") or event.data.get("id") or ""
        self._log_action("inspection-queue:work_added", str(title)[:40])

    @on_event("inspection-queue:work_completed")
    async def on_inspection_completed(self, event):
        title = event.data.get("title") or event.data.get("id") or ""
        self._log_action("inspection-queue:work_completed", str(title)[:40])

    @on_event("cer-hosting:analysed")
    async def on_cer_analysed(self, event):
        site = event.data.get("site") or event.data.get("name") or ""
        self._log_action("cer-hosting:analysed", str(site)[:40])

    @on_event("vegetation-intrusion:detected")
    async def on_vegetation_detected(self, event):
        loc = event.data.get("location") or event.data.get("span") or ""
        self._log_action("vegetation-intrusion:detected", str(loc)[:40])

    # ── Engineering study/run completions — occupational dimension ──

    @on_event("lightning:study_created")
    async def on_lightning_study_created(self, event):
        name = event.data.get("name") or event.data.get("id") or ""
        self._log_action("lightning:study_created", str(name)[:50])

    @on_event("lightning:study_analysed")
    async def on_lightning_study_analysed(self, event):
        name = event.data.get("name") or event.data.get("id") or ""
        self._log_action("lightning:study_analysed", str(name)[:50])
        await self._journal_ripple("⚡", f"Lightning study analysed: `{name}`", dim="occupational")

    @on_event("interference:study_created")
    async def on_interference_study_created(self, event):
        name = event.data.get("name") or event.data.get("id") or ""
        self._log_action("interference:study_created", str(name)[:50])

    @on_event("interference:study_analysed")
    async def on_interference_study_analysed(self, event):
        name = event.data.get("name") or event.data.get("id") or ""
        self._log_action("interference:study_analysed", str(name)[:50])
        await self._journal_ripple("📡", f"Interference study analysed: `{name}`", dim="occupational")

    @on_event("fault-distribution:solved")
    async def on_fault_distribution_solved(self, event):
        case = event.data.get("case") or event.data.get("id") or event.data.get("name") or ""
        self._log_action("fault-distribution:solved", str(case)[:50])
        await self._journal_ripple("🔌", f"Fault distribution solved: `{case}`", dim="occupational")

    @on_event("sim:run_completed")
    async def on_sim_run_completed(self, event):
        name = event.data.get("name") or event.data.get("id") or ""
        self._log_action("sim:run_completed", str(name)[:50])

    @on_event("sim:run_failed")
    async def on_sim_run_failed(self, event):
        name = event.data.get("name") or event.data.get("id") or ""
        err = (event.data.get("error") or "")[:60]
        self._log_action("sim:run_failed", f"{name}: {err}")

    @on_event("cables:project_created")
    async def on_cables_project_created(self, event):
        name = event.data.get("name") or event.data.get("id") or ""
        self._log_action("cables:project_created", str(name)[:50])
        await self._journal_ripple("🔗", f"Cables project: `{name}`", dim="occupational")

    @on_event("cables:schedule_run")
    async def on_cables_schedule_run(self, event):
        # One breadcrumb per run (not per cable). Reads the schedule_run
        # event schema (n_cables, n_ok, n_errors, method) emitted by
        # apps/cables/rating.py:run_schedule.
        d = event.data
        proj = d.get("project") or ""
        n = d.get("n_cables") or 0
        ok = d.get("n_ok") or 0
        errs = d.get("n_errors") or 0
        skipped = d.get("n_skipped") or 0
        method = d.get("method") or "native"
        summary = f"{ok}/{n} ok"
        if errs:
            summary += f", {errs} err"
        if skipped:
            summary += f", {skipped} skip"
        self._log_action("cables:schedule_run", f"{proj} · {method} · {summary}")
        await self._journal_ripple(
            "🔌", f"Cable schedule on `{proj}` ({method}): {summary}",
            dim="occupational",
        )

    @on_event("cables:load_flow_run")
    async def on_cables_load_flow_run(self, event):
        d = event.data
        proj = d.get("project") or ""
        nodes = d.get("n_nodes") or 0
        edges = d.get("n_edges") or 0
        converged = d.get("converged")
        violations = d.get("n_violations") or 0
        iters = d.get("iterations") or 0
        status = "converged" if converged else "did not converge"
        tail = f"{nodes}n/{edges}e · {iters} iter · {status}"
        if violations:
            tail += f" · {violations} sizing violation" + ("s" if violations != 1 else "")
        self._log_action("cables:load_flow_run", f"{proj} · {tail}")
        await self._journal_ripple(
            "⚡", f"Load-flow on `{proj}`: {tail}", dim="occupational",
        )

    @on_event("cables:cable_routed")
    async def on_cables_cable_routed(self, event):
        d = event.data
        proj = d.get("project") or ""
        cable_id = d.get("id") or ""
        n_links = d.get("n_edge_links_set") or 0
        path = d.get("path") or []
        hops = max(len(path) - 1, 0)
        self._log_action("cables:cable_routed", f"{proj}/{cable_id} · {hops} hop")
        await self._journal_ripple(
            "🛣️", f"Routed `{cable_id}` on `{proj}` ({hops} hop, {n_links} edges linked)",
            dim="occupational",
        )

    @on_event("jobs:outreach_added")
    async def on_jobs_outreach_added(self, event):
        contact = event.data.get("contact") or event.data.get("name") or event.data.get("company") or ""
        self._log_action("jobs:outreach_added", str(contact)[:50])
        await self._journal_ripple("📩", f"Outreach: `{contact}`", dim="social")

    @on_event("jobs:outreach_status_changed")
    async def on_jobs_outreach_status(self, event):
        contact = event.data.get("contact") or event.data.get("name") or ""
        status = event.data.get("status", "")
        self._log_action("jobs:outreach_status_changed", f"{contact}: {status}")

    @on_event("jobs:outreach_updated")
    async def on_jobs_outreach_updated(self, event):
        contact = event.data.get("contact") or event.data.get("name") or ""
        self._log_action("jobs:outreach_updated", str(contact)[:50])

    # ── Engineering: earthing, soil, cables CRUD ──

    @on_event("earthing:soil_fit")
    async def on_earthing_soil_fit(self, event):
        n = event.data.get("n_points") or event.data.get("n_layers") or ""
        self._log_action("earthing:soil_fit", f"{n} points")

    @on_event("earthing:verdict")
    async def on_earthing_verdict(self, event):
        proj = event.data.get("project_id") or event.data.get("id", "")
        verdict = event.data.get("verdict") or ""
        self._log_action("earthing:verdict", f"{proj}: {verdict}")
        if verdict:
            await self._journal_ripple(
                "⚡", f"Earthing verdict on `{proj}`: {verdict}", dim="occupational"
            )

    @on_event("earthing:rg_computed")
    async def on_earthing_rg(self, event):
        self._log_action("earthing:rg_computed", str(event.data.get("rg_ohm",""))[:30])

    @on_event("earthing:voltages_computed")
    async def on_earthing_voltages(self, event):
        self._log_action("earthing:voltages_computed", "")

    @on_event("earthing:eg0_risk")
    async def on_earthing_eg0(self, event):
        self._log_action("earthing:eg0_risk", str(event.data.get("risk",""))[:30])

    @on_event("soil:fit_completed")
    async def on_soil_fit(self, event):
        rms = event.data.get("rms_pct") or event.data.get("rms","")
        self._log_action("soil:fit_completed", f"rms={rms}")

    @on_event("soil:project_created")
    async def on_soil_project_created(self, event):
        name = event.data.get("name") or event.data.get("id", "")
        self._log_action("soil:project_created", str(name)[:50])

    @on_event("soil:project_updated")
    async def on_soil_project_updated(self, event):
        self._log_action("soil:project_updated", str(event.data.get("id",""))[:50])

    @on_event("soil:project_deleted")
    async def on_soil_project_deleted(self, event):
        self._log_action("soil:project_deleted", str(event.data.get("id",""))[:50])

    @on_event("soil:soundings_saved")
    async def on_soil_soundings(self, event):
        self._log_action("soil:soundings_saved", str(event.data.get("n",""))[:30])

    @on_event("cables:project_updated")
    async def on_cables_project_updated(self, event):
        self._log_action("cables:project_updated", str(event.data.get("id",""))[:50])

    @on_event("cables:project_deleted")
    async def on_cables_project_deleted(self, event):
        self._log_action("cables:project_deleted", str(event.data.get("id",""))[:50])

    @on_event("earthing:project_created")
    async def on_earthing_project_created(self, event):
        name = event.data.get("name") or event.data.get("id", "")
        self._log_action("earthing:project_created", str(name)[:50])

    @on_event("earthing:project_deleted")
    async def on_earthing_project_deleted(self, event):
        self._log_action("earthing:project_deleted", str(event.data.get("id",""))[:50])

    @on_event("earthing:soundings_saved")
    async def on_earthing_soundings(self, event):
        self._log_action("earthing:soundings_saved", str(event.data.get("n",""))[:30])

    @on_event("geo-cad:layer_added")
    async def on_geocad_layer_added(self, event):
        name = event.data.get("title") or event.data.get("id", "")
        self._log_action("geo-cad:layer_added", str(name)[:50])

    @on_event("geo-cad:feature_added")
    async def on_geocad_feature_added(self, event):
        layer = event.data.get("layer_id") or event.data.get("layer", "")
        self._log_action("geo-cad:feature_added", f"layer: {str(layer)[:40]}")

    @on_event("company:created")
    async def on_company_created(self, event):
        name = event.data.get("name") or event.data.get("id", "")
        self._log_action("company:created", str(name)[:50])

    @on_event("company:worker_added")
    async def on_company_worker_added(self, event):
        worker = event.data.get("id") or event.data.get("name", "")
        self._log_action("company:worker_added", str(worker)[:50])
