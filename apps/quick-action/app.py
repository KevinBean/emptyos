"""Quick Action — one input, agent figures out what to do.

Originally "capture" — a fast write-only inbox. Evolving into a natural-language
command surface: typed/spoken text → orchestrator plans 1-N actions → user
confirms → execute via the voice-intent registry + staff workflows.

Phase 0: app renamed; existing verbs unchanged. Orchestrator lands in Phase 3.
"""

from datetime import datetime, timezone

from emptyos.sdk import BaseApp, cli_command, dimensions, parse_captures, web_route


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

        Ambient tag routing: a tag of ``canvas`` or ``canvas/<board_id>`` also
        appends the capture as a node to that board (the capture still lands in
        inbox — user can dismiss manually).
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        tag_str = f" #{tag}" if tag else ""
        line = f"- {now} — {text}{tag_str}\n"

        path = self._capture_path()
        try:
            existing = (await self.read(path)).rstrip("\n")
            content = existing + "\n" + line
        except Exception:
            content = f"# Captures\n\n{line}"

        await self.write(path, content)
        dimension = _resolve_dimension(text, tag)
        entry = {"text": text, "tag": tag, "dimension": dimension, "timestamp": now}
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
                await self.call_app("canvas", "add_node",
                                    board_id=board_id, text=text, source="capture")
            except Exception:
                pass  # canvas may not be loaded; capture still persists
        return entry

    async def voice_capture(self, text: str) -> dict:
        """Voice intent — save a one-liner to the inbox. Tag inferred later."""
        await self.add(text, "")
        return {"say": "Saved to inbox."}

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
            items.append({
                "id": f"capture-{c.get('timestamp','')}-{i}",
                "text": spoken,
                "source": "capture",
                "timestamp": c.get("timestamp"),
                "act": {
                    "label": "Save as task",
                    "method": "POST",
                    "url": "/task/api/add",
                    "body": {"text": text},
                },
            })
        return {"items": items, "source": "inbox", "count": len(items)}

    @web_route("POST", "/api/smart-add")
    async def api_smart_add(self, request):
        """AI auto-tags a capture based on content."""
        data = await request.json()
        text = data.get("text", "")
        if not text:
            return {"error": "text required"}
        result = await self.think(
            f"Classify this inbox capture into ONE tag: idea, task, note, link, question, reminder, dev. "
            f"Return ONLY the tag word, nothing else.\n\n{text}",
            domain="text", temperature=0.2,
        )
        tag = result.strip().lower().split()[0] if result.strip() else ""
        return await self.add(text, tag)

    @web_route("POST", "/api/add")
    async def api_add(self, request):
        data = await request.json()
        return await self.add(data["text"], data.get("tag", ""))

    @web_route("GET", "/api/list")
    async def api_list(self, request):
        limit = int(request.query_params.get("limit", "50"))
        return await self.list_captures(limit)

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
        data = await request.json()
        old_ts = data.get("old_timestamp", "")
        old_text = data.get("old_text", "")
        new_text = data.get("text", "")
        new_tag = data.get("tag", "")
        if not new_text:
            return {"error": "text required"}
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        tag_str = f" #{new_tag}" if new_tag else ""
        new_line = f"- {now} — {new_text}{tag_str}"
        path = self._capture_path()
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
        data = await request.json()
        removed = await self._remove_capture(data.get("timestamp", ""), data.get("text", ""))
        return {"dismissed": removed}

    # Tag → project routing for capture triage
    _TAG_PROJECT = {
        "dev": "emptyos-development",
    }

    @web_route("POST", "/api/to-task")
    async def api_to_task(self, request):
        data = await request.json()
        text = data.get("text", "")
        tag = data.get("tag", "")
        if not text:
            return {"error": "text required"}
        try:
            project_id = self._TAG_PROJECT.get(tag)
            if project_id:
                await self.call_app("projects", "add_task_to_project",
                                    project_id=project_id, text=text)
            else:
                await self.call_app("task", "add", text=text)
        except Exception as e:
            return {"error": f"task creation failed: {e}"}
        await self._remove_capture(data.get("timestamp", ""), text)
        return {"converted": True, "text": text, "project": project_id if project_id else None}

    @web_route("POST", "/api/to-done-task")
    async def api_to_done_task(self, request):
        """Capture as an already-completed task — keeps a record without adding to TODOs."""
        data = await request.json()
        text = data.get("text", "")
        tag = data.get("tag", "")
        if not text:
            return {"error": "text required"}
        try:
            project_id = self._TAG_PROJECT.get(tag)
            if project_id:
                await self.call_app("projects", "add_task_to_project",
                                    project_id=project_id, text=text, done=True)
            else:
                await self.call_app("task", "add", text=text, done=True)
        except Exception as e:
            return {"error": f"done-task creation failed: {e}"}
        await self._remove_capture(data.get("timestamp", ""), text)
        return {"converted": True, "text": text, "done": True, "project": project_id if project_id else None}

    @web_route("POST", "/api/to-journal")
    async def api_to_journal(self, request):
        data = await request.json()
        text = data.get("text", "")
        if not text:
            return {"error": "text required"}
        try:
            await self.call_app("journal", "_add_entry", d=datetime.now(timezone.utc).date(), text=text, mood="okay")
        except Exception as e:
            return {"error": f"journal write failed: {e}"}
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
        data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        keep = int(data.get("keep", 10))
        path = self._capture_path()
        try:
            content = await self.read(path)
        except Exception:
            return {"archived": 0}

        import re
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
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        new_content = "\n".join(header) + "\n\n" + "\n".join(to_keep) + "\n"
        new_content += f"\n## Archive ({now})\n\n" + "\n".join(to_archive) + "\n"

        await self.write(path, new_content)
        await self.emit("capture:archived", {"count": len(to_archive)})
        return {"archived": len(to_archive), "kept": len(to_keep)}
