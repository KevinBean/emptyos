"""Quick Action — one input, agent figures out what to do.

Originally "capture" — a fast write-only inbox. Evolving into a natural-language
command surface: typed/spoken text → orchestrator plans 1-N actions → user
confirms → execute via the voice-intent registry + staff workflows.

Phase 0: app renamed; existing verbs unchanged. Orchestrator lands in Phase 3.
"""

import re
from datetime import UTC, datetime

from emptyos.sdk import BaseApp, cli_command, dimensions, parse_captures, web_route
from emptyos.sdk.capture_routes import apply_capture_direct

_INLINE_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][\w-]*)")

# ── Plan & Run routing (output-shape split) ────────────────────────────────
# An ambitious ask is one of two shapes, and they want different engines:
#   action      → discrete reversible app actions / side-effects → the
#                 voice-assistant verb planner (plan_actions / execute_plan)
#   deliverable → a document/artifact to read & judge → hand off to the `work`
#                 app, which owns the staged production pipeline + preview.
# quick-action stays the fast front door; it classifies and dispatches, never
# producing artifacts itself. Gated behind the dark flag
# `[apps.quick-action] feature.work-handoff.enabled` (default off) so the flow
# is byte-identical to today until flipped on.
PLAN_ROUTE_SYSTEM = """You route a user's request to the right engine.
Pick `deliverable` when they want a document/artifact produced to read or judge
— a report, research write-up, slide deck, web page, or a summary of notes.
Pick `action` for anything else — adding tasks, reminders, messages, logging,
or several small app side-effects. When unsure, pick `action`."""

PLAN_ROUTE_USER = "Request:\n{text}"

PLAN_ROUTE_CHOICES = {
    "action": "discrete app actions / side-effects — add a task, set a reminder, "
    "send a message, log something, or several small steps",
    "deliverable": "produce a document/artifact to read or judge — a report, "
    "research doc, slide deck, web page, or summary of notes",
}

SMART_TAG_SYSTEM = """You are a one-token classifier for inbox captures.
Pick the single best tag from this fixed set:
  idea, task, note, link, question, reminder, dev

Rules:
- Output ONLY the tag word. Nothing else.
- No punctuation, no quotes, no explanation, no "Tag:" prefix.
- Do NOT echo the user's text.
- Do NOT invent tags outside the set.
- If genuinely ambiguous, pick `note`."""

SMART_TAG_USER = "Capture:\n{text}"


def _hoist_inline_tag(text: str, tag: str) -> tuple[str, str]:
    """If tag is empty, lift the first inline ``#word`` out of text into tag.

    Keeps the on-disk capture line canonical (single trailing ``#tag``) so
    parse_captures round-trips and the API carries the tag instead of an
    empty string when the user typed ``capture 22kV idea #cables``.
    """
    if tag:
        return text, tag
    m = _INLINE_TAG_RE.search(text or "")
    if not m:
        return text, tag
    stripped = (text[: m.start()] + text[m.end() :]).strip()
    stripped = re.sub(r"\s{2,}", " ", stripped)
    return stripped, m.group(1)


def _resolve_dimension(text: str, tag: str) -> str:
    """Primary tag wins; otherwise first inline #tag in text that maps to a dimension."""
    d = dimensions.resolve(tag)
    if d:
        return d
    found = dimensions.extract(text)
    return found[0] if found else ""


class QuickActionApp(BaseApp):
    def _capture_path(self) -> str:
        p = self.vault_config_path("inbox", "00_Inbox/_captures.md")
        if p:
            return str(p)
        return str(self.kernel.config.data_dir / "captures.md")

    async def add(self, text: str, tag: str = "") -> dict:
        """Add a capture entry. Returns the entry dict.

        Direct project routing: tags in ``_TAG_PROJECT`` (currently ``dev`` →
        emptyos-development, ``dogfood`` → emptyos-dogfood) skip the inbox and
        create a task on the target project directly. Falls back to the inbox
        if the projects app errors so the capture is never lost.

        Ambient tag routing: a tag of ``canvas`` or ``canvas/<board_id>`` also
        appends the capture as a node to that board (the capture still lands in
        inbox — user can dismiss manually).
        """
        text, tag = _hoist_inline_tag(text, tag)
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        dimension = _resolve_dimension(text, tag)
        entry = {"text": text, "tag": tag, "dimension": dimension, "timestamp": now}

        route = self._TAG_ROUTE.get(tag)
        # The reminder route creates a real reminders record instead of an inbox
        # note (closes the detect→note leak: smart_add can classify `reminder`
        # but a note is not a reminder). Dark-flagged (feature.reminder-route.enabled,
        # default off) so a reminder-tagged capture is byte-identical to today
        # until flipped on — flag off falls through to the inbox below.
        if route and route[0] == "reminder" and not self._reminder_route_enabled():
            route = None
        if route:
            try:
                kind, target, text_transform = route
                routed_text = text_transform(text) if text_transform else text
                if kind == "project":
                    await self.call_app(
                        "projects", "add_task_to_project",
                        project_id=target, text=routed_text,
                    )
                elif kind == "task":
                    await self.call_app("task", "add", text=routed_text)
                elif kind == "reminder":
                    # No due extracted in v1 — creates an active reminder the
                    # user dates on /reminders/. reminders is a personal app;
                    # the outer try/except falls back to inbox if it's absent.
                    await self.call_app("reminders", "add", text=routed_text, due="")
                entry["routed_to"] = target
                entry["project_name"] = self._ROUTE_NAMES.get(target, target)
                await self.emit("capture:saved", entry)
                return entry
            except Exception:
                # Target app missing/erroring — fall through to inbox so
                # the capture is never lost.
                pass

        tag_str = f" #{tag}" if tag else ""
        line = f"- {now} — {text}{tag_str}\n"

        path = self._capture_path()
        # Serialize the read-modify-write on the shared capture file: two
        # concurrent captures (or a reactor handler writing the same file)
        # would otherwise both read the pre-write content and the later write
        # would wipe the earlier capture — silent data loss (CLAUDE.md vault
        # read-modify-write race). Emit stays OUTSIDE the lock so handlers can
        # recurse through call_app without deadlocking.
        async with self.note_lock(path):
            try:
                existing = (await self.read(path)).rstrip("\n")
                content = existing + "\n" + line
            except Exception:
                content = f"# Captures\n\n{line}"
            await self.write(path, content)
        entry["path"] = path
        await self.emit("capture:saved", entry)

        # Ambient canvas routing — #canvas[/<board>] appends to that board
        norm = (tag or "").strip().lower()
        if norm == "canvas" or norm.startswith("canvas/"):
            board_id = "inbox"
            if "/" in norm:
                suffix = norm.split("/", 1)[1].strip()
                if suffix:
                    board_id = suffix
            try:
                await self.call_app(
                    "canvas", "add_node", board_id=board_id, text=text, source="capture"
                )
            except Exception:
                pass  # canvas may not be loaded; capture still persists
        return entry

    async def voice_capture(self, text: str) -> dict:
        """Voice intent — save a one-liner to the inbox. Tag inferred later."""
        await self.add(text, "")
        return {
            "say": "Saved to inbox.",
            "link": {"text": "Open inbox", "href": "/quick-action/"},
        }

    @cli_command("quick-action", help="Quick-action: type/speak intent, agent does it")
    async def cmd_quick_action(self, text: str, tag: str = ""):
        entry = await self.add(text, tag)
        self.print_rich(f"[green]Captured:[/green] {entry['text']}")

    @cli_command("capture", help="(alias of quick-action)")
    async def cmd_capture(self, text: str, tag: str = ""):
        entry = await self.add(text, tag)
        self.print_rich(f"[green]Captured:[/green] {entry['text']}")

    async def list_captures(self, limit: int = 50) -> list[dict]:
        """Parse captures file into structured list."""
        path = self._capture_path()
        try:
            content = await self.read(path)
        except Exception:
            return []
        entries = parse_captures(content, limit=limit)
        for e in entries:
            e["dimension"] = _resolve_dimension(e["text"], e["tag"])
        return entries

    @web_route("GET", "/api/read-feed")
    async def api_read_feed(self, request):
        """Hands-free read-aloud adapter. Returns the most recent captures as read-
        aloud items. Each item carries an `act` descriptor the overlay fires via
        Victory gesture — for captures, "Save as task" promotes the text into the
        task inbox so you can triage by voice + gesture.
        """
        try:
            limit = max(1, min(30, int(request.query_params.get("limit") or "10")))
        except ValueError:
            limit = 10
        captures = await self.list_captures(limit)
        items = []
        for i, c in enumerate(captures):
            text = c.get("text") or ""
            tag = c.get("tag") or ""
            spoken = (f"{tag}: " if tag else "") + text
            items.append(
                {
                    "id": f"capture-{c.get('timestamp', '')}-{i}",
                    "text": spoken,
                    "source": "capture",
                    "timestamp": c.get("timestamp"),
                    "act": {
                        "label": "Save as task",
                        "method": "POST",
                        "url": "/task/api/add",
                        "body": {"text": text},
                    },
                }
            )
        return {"items": items, "source": "inbox", "count": len(items)}

    async def smart_add(self, text: str) -> dict:
        """AI auto-tags a capture based on content. Kwargs-callable so other
        apps (hub smart-bar routing) can reuse it via call_app."""
        if not text:
            return {"error": "text required"}
        # Fall back to a plain capture (with inline-#tag extraction) when
        # no think provider is available, so AI-offline doesn't 500 the
        # whole inbox surface.
        try:
            result = await self.think(
                SMART_TAG_USER.format(text=text),
                system=SMART_TAG_SYSTEM,
                domain="text",
                temperature=0.2,
            )
            tag = result.strip().lower().split()[0] if result.strip() else ""
            if tag not in {"idea", "task", "note", "link", "question", "reminder", "dev"}:
                tag = "note"
        except Exception:
            res = await self.add(text, "")
            res["ai_offline"] = True
            return res
        return await self.add(text, tag)

    @web_route("POST", "/api/smart-add")
    async def api_smart_add(self, request):
        data = await self.read_json(request)
        return await self.smart_add(data.get("text", ""))

    @web_route("POST", "/api/add")
    async def api_add(self, request):
        data = await self.read_json(request)
        return await self.add(data["text"], data.get("tag", ""))

    @web_route("POST", "/api/capture")
    async def api_capture(self, request):
        return await self.api_add(request)

    @web_route("POST", "/api/save")
    async def api_save(self, request):
        return await self.api_add(request)

    @web_route("GET", "/api/list")
    async def api_list(self, request):
        limit = int(request.query_params.get("limit", "50"))
        return await self.list_captures(limit)

    @web_route("GET", "/api/has")
    async def api_has(self, request):
        """Quick "is this URL already captured?" lookup.

        Used by the chrome-extension badge poller so the toolbar icon can
        show a checkmark on URLs already in the capture inbox. Scans the
        recent capture log for an exact URL substring match. Cheap by
        design — no DB index, capped at 500 recent captures.
        """
        url = (request.query_params.get("url") or "").strip()
        if not url:
            return {"has": False}
        captures = await self.list_captures(500)
        for c in captures:
            text = (c.get("text") or "") + " " + (c.get("note") or "")
            if url in text:
                return {"has": True, "ts": c.get("ts", "")}
        return {"has": False}

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        captures = await self.list_captures(1000)
        tags: dict[str, int] = {}
        by_dim = dimensions.empty_counts()
        for c in captures:
            t = c.get("tag", "") or "untagged"
            tags[t] = tags.get(t, 0) + 1
            d = c.get("dimension", "")
            if d in by_dim:
                by_dim[d] += 1
        return {"total": len(captures), "by_tag": tags, "by_dimension": by_dim}

    @web_route("GET", "/api/recent")
    async def api_recent(self, request):
        """Last N captures (default 5)."""
        limit = int(request.query_params.get("limit", "5"))
        return await self.list_captures(limit)

    async def _remove_capture(self, ts: str, text: str) -> bool:
        """Remove a capture line by timestamp + text match."""
        if not ts:
            return False
        path = self._capture_path()
        async with self.note_lock(path):
            try:
                content = await self.read(path)
            except Exception:
                return False
            lines = content.split("\n")
            new_lines = []
            removed = False
            for line in lines:
                if not removed and ts in line and text[:40] in line:
                    removed = True
                    continue
                new_lines.append(line)
            if removed:
                await self.write(path, "\n".join(new_lines))
        return removed

    @web_route("POST", "/api/update")
    async def api_update(self, request):
        """Update an existing capture (atomic: remove old + add new in one write)."""
        data = await self.read_json(request)
        old_ts = data.get("old_timestamp", "")
        old_text = data.get("old_text", "")
        new_text = data.get("text", "")
        new_tag = data.get("tag", "")
        if not new_text:
            return {"error": "text required"}
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        tag_str = f" #{new_tag}" if new_tag else ""
        new_line = f"- {now} — {new_text}{tag_str}"
        path = self._capture_path()
        async with self.note_lock(path):
            try:
                content = await self.read(path)
            except Exception:
                content = "# Captures\n"
            lines = content.split("\n")
            filtered = []
            removed = False
            for line in lines:
                if not removed and old_ts and old_ts in line and old_text[:40] in line:
                    removed = True
                    continue
                filtered.append(line)
            filtered.append(new_line)
            await self.write(path, "\n".join(filtered))
        dimension = _resolve_dimension(new_text, new_tag)
        entry = {"text": new_text, "tag": new_tag, "dimension": dimension, "timestamp": now}
        await self.emit("capture:saved", entry)
        return entry

    @web_route("POST", "/api/dismiss")
    async def api_dismiss(self, request):
        data = await self.read_json(request)
        removed = await self._remove_capture(data.get("timestamp", ""), data.get("text", ""))
        return {"dismissed": removed}

    # ── Plan & Run (output-shape routing) ────────────────────────────────
    # The page POSTs here instead of raw-fetching a sibling app's private API
    # path. We declare voice-assistant + work as optional_apps so the loader
    # knows the soft deps; both calls are guarded so a missing planner degrades
    # to a clear error rather than a silent dead button.

    def _work_handoff_enabled(self) -> bool:
        return bool(self.app_config("feature.work-handoff.enabled", False))

    def _reminder_route_enabled(self) -> bool:
        return bool(self.app_config("feature.reminder-route.enabled", False))

    @web_route("POST", "/api/plan")
    async def api_plan(self, request):
        """Classify an ambitious ask and route it.

        Deliverable-shaped asks (with the dark flag on and `work` reachable)
        are pre-planned via ``work.plan`` and handed off to the work surface for
        production. Everything else routes to the voice-assistant verb planner.
        """
        data = await self.read_json(request)
        text = (data.get("text") or "").strip()
        if not text:
            return {"error": "text required"}

        if self._work_handoff_enabled():
            try:
                route = await self.select(
                    PLAN_ROUTE_USER.format(text=text),
                    choices=PLAN_ROUTE_CHOICES,
                    system=PLAN_ROUTE_SYSTEM,
                    default="action",
                )
            except Exception:
                route = "action"
            if route == "deliverable":
                # work.plan runs stop_after="plan" — a single bounded think,
                # not the multi-minute generation (that stays inside work). On
                # any failure (work disabled / errors) fall through to action.
                try:
                    res = await self.call_app("work", "plan", ask=text)
                    if res and not res.get("error") and res.get("run_id"):
                        return {
                            "route": "deliverable",
                            "ask": text,
                            "run_id": res["run_id"],
                            "plan": res.get("plan"),
                            "href": "/work/#" + res["run_id"],
                        }
                except Exception:
                    pass  # work unavailable — degrade to the action planner

        try:
            plan = await self.call_app("voice-assistant", "plan_actions", user_text=text)
        except Exception as e:
            return {"error": f"planner unavailable: {e}"}
        return {"route": "action", **(plan or {})}

    @web_route("POST", "/api/execute")
    async def api_execute(self, request):
        """Run selected steps of an action-shaped plan via the voice-assistant
        executor. Deliverable plans never reach here — they run inside work."""
        data = await self.read_json(request)
        plan = data.get("plan")
        if not isinstance(plan, dict):
            return {"error": "plan required"}
        only = data.get("only_indices")
        try:
            results = await self.call_app(
                "voice-assistant", "execute_plan", plan=plan, only_indices=only
            )
        except Exception as e:
            return {"error": f"executor unavailable: {e}"}
        return {"results": results or []}

    # Tag → destination routing. ``add()`` consults this to skip the inbox
    # when the user typed an explicit destination tag. Triage (``api_to_task``)
    # uses ``_TAG_PROJECT`` for project-targeted routes only.
    #
    # Shape: tag → (kind, target, text_transform | None)
    #   kind="project" → call_app("projects", "add_task_to_project", project_id=target, text=...)
    #   kind="task"    → call_app("task", "add", text=...)
    #   kind="app"     → call_app(target, "add_entry_from_capture", text=...)
    #                    (target app must expose an add_entry_from_capture(text) method)
    #   kind="reminder"→ call_app("reminders", "add", text=..., due="")
    #                    (dark-flagged behind feature.reminder-route.enabled)
    _TAG_ROUTE = {
        "dev":      ("project",  "emptyos-development", None),
        "dogfood":  ("project",  "emptyos-dogfood",    None),
        "bug":      ("project",  "emptyos-development", lambda t: f"[bug] {t}"),
        "task":     ("task",     "inbox",              None),
        "reminder": ("reminder", "reminders",          None),
        "dharma":   ("app",      "dharma-log",         None),
        "study":    ("app",      "dharma-log",         None),
    }
    _ROUTE_NAMES = {
        "emptyos-development": "EmptyOS Development",
        "emptyos-dogfood":     "EmptyOS Dogfood",
        "inbox":               "Tasks",
        "reminders":           "Reminders",
        "dharma-log":          "Dharma Log",
    }
    # Triage helper still needs the project-only subset
    _TAG_PROJECT = {
        tag: target
        for tag, (kind, target, _) in _TAG_ROUTE.items()
        if kind == "project"
    }

    @web_route("POST", "/api/to-task")
    async def api_to_task(self, request):
        data = await self.read_json(request)
        text = data.get("text", "")
        tag = data.get("tag", "")
        if not text:
            return {"error": "text required"}
        # Check kind="app" routes first — these send the capture to a
        # specific app's add_entry_from_capture method rather than to
        # task/project. Used for #dharma, #study, etc.
        route = self._TAG_ROUTE.get(tag)
        if route and route[0] == "app":
            _kind, target_app, _xform = route
            try:
                result = await self.call_app(target_app, "add_entry_from_capture", text=text)
                if result and result.get("error"):
                    return {"error": f"{target_app} declined: {result['error']}"}
            except Exception as e:
                return {"error": f"{target_app} routing failed: {e}"}
            await self._remove_capture(data.get("timestamp", ""), text)
            return {
                "converted": True,
                "text": text,
                "routed_to": target_app,
                "entry": (result or {}).get("id"),
            }
        project_id = self._TAG_PROJECT.get(tag)
        if project_id:
            # Project routing is quick-action's own concern (single consumer) —
            # the shared destination table only covers the plain task/journal/kb
            # landing zones.
            try:
                await self.call_app(
                    "projects", "add_task_to_project", project_id=project_id, text=text
                )
            except Exception as e:
                return {"error": f"task creation failed: {e}"}
        else:
            r = await apply_capture_direct(self, "task", {"text": text})
            if r.get("error"):
                return {"error": f"task creation failed: {r['error']}"}
        await self._remove_capture(data.get("timestamp", ""), text)
        return {"converted": True, "text": text, "project": project_id if project_id else None}

    @web_route("POST", "/api/to-done-task")
    async def api_to_done_task(self, request):
        """Capture as an already-completed task — keeps a record without adding to TODOs."""
        data = await self.read_json(request)
        text = data.get("text", "")
        tag = data.get("tag", "")
        if not text:
            return {"error": "text required"}
        try:
            project_id = self._TAG_PROJECT.get(tag)
            if project_id:
                await self.call_app(
                    "projects", "add_task_to_project", project_id=project_id, text=text, done=True
                )
            else:
                await self.call_app("task", "add", text=text, done=True)
        except Exception as e:
            return {"error": f"done-task creation failed: {e}"}
        await self._remove_capture(data.get("timestamp", ""), text)
        return {
            "converted": True,
            "text": text,
            "done": True,
            "project": project_id if project_id else None,
        }

    @web_route("POST", "/api/to-journal")
    async def api_to_journal(self, request):
        data = await self.read_json(request)
        text = data.get("text", "")
        if not text:
            return {"error": "text required"}
        r = await apply_capture_direct(self, "journal", {"text": text})
        if r.get("error"):
            return {"error": f"journal write failed: {r['error']}"}
        await self._remove_capture(data.get("timestamp", ""), text)
        return {"converted": True, "target": "journal"}

    @web_route("GET", "/api/pending")
    async def api_pending(self, request):
        """Count of unprocessed captures (for badges/notifications)."""
        captures = await self.list_captures(1000)
        return {"pending": len(captures)}

    @web_route("POST", "/api/dedupe")
    async def api_dedupe(self, request):
        """Remove duplicate capture lines, keeping the earliest occurrence per text."""
        path = self._capture_path()
        async with self.note_lock(path):
            try:
                content = await self.read(path)
            except Exception:
                return {"removed": 0}
            lines = content.split("\n")
            seen: set[str] = set()
            kept: list[str] = []
            removed = 0
            for line in lines:
                stripped = line.strip()
                if not stripped.startswith("- ") or " — " not in stripped:
                    kept.append(line)
                    continue
                try:
                    body = stripped.split(" — ", 1)[1].strip()
                except IndexError:
                    kept.append(line)
                    continue
                key = body.lower()
                if key in seen:
                    removed += 1
                    continue
                seen.add(key)
                kept.append(line)
            if removed:
                await self.write(path, "\n".join(kept))
        return {"removed": removed}

    @web_route("POST", "/api/clear")
    async def api_clear(self, request):
        """Archive old captures — moves all but the most recent `keep` entries to an archive section."""
        data = (
            await self.read_json(request)
            if request.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        keep = int(data.get("keep", 10))
        path = self._capture_path()
        import re

        async with self.note_lock(path):
            try:
                content = await self.read(path)
            except Exception:
                return {"archived": 0}

            lines = content.split("\n")
            entries = []
            other = []
            for line in lines:
                if re.match(r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2} —", line.strip()):
                    entries.append(line)
                elif not line.strip().startswith("# Archive"):
                    other.append(line)

            # entries are oldest-first in file; keep the last `keep`
            if len(entries) <= keep:
                return {"archived": 0}

            to_archive = entries[:-keep]
            to_keep = entries[-keep:]

            header = [l for l in other if l.strip()]
            now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
            new_content = "\n".join(header) + "\n\n" + "\n".join(to_keep) + "\n"
            new_content += f"\n## Archive ({now})\n\n" + "\n".join(to_archive) + "\n"

            await self.write(path, new_content)
        await self.emit("capture:archived", {"count": len(to_archive)})
        return {"archived": len(to_archive), "kept": len(to_keep)}
