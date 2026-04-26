"""Journal — daily journaling with mood tracking and reflection.

Vault structure: 50_Journal/{YEAR}/{YYYY-MM-DD}.md
Sections: Journal (timestamped entries), Milestone, Three successful things
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from functools import cached_property
from pathlib import Path

from emptyos.sdk import TASK_RE, BaseApp, cli_command, compute_task_decay, dimensions, parse_captures, parse_llm_json, scheduled, web_route
from emptyos.runtime import wheel as _wheel

from .parser import MOOD_EMOJI, MOOD_SCORE, extract_section, parse_entries, replace_section
from .prompts import PROMPT_GEN_SYSTEM, REFLECT_SYSTEM, WHEEL_REVIEW_SYSTEM
from . import panels as _panels


class JournalApp(BaseApp):

    async def get_summary(self) -> dict:
        """Summary for staff observers — today's entries, streak, mood."""
        today = date.today()
        path = self._daily_path(today)
        entries = []
        try:
            content = await self.read(str(path))
            entries = parse_entries(content)
        except Exception:
            pass
        streak = 0
        d = today
        while True:
            try:
                c = await self.read(str(self._daily_path(d)))
                if parse_entries(c):
                    streak += 1
                    d -= timedelta(days=1)
                    continue
            except Exception:
                pass
            break
        dominant_mood = ""
        if entries:
            moods = [e.get("mood", "") for e in entries if e.get("mood")]
            if moods:
                dominant_mood = max(set(moods), key=moods.count)
        return {"today_entries": len(entries), "streak": streak, "mood": dominant_mood}

    def _journal_dir(self) -> Path:
        # entries template: "50_Journal/{year}/{date}.md"
        raw = self.vault_config("entries", "50_Journal/{year}/{date}.md")
        base = raw.split("/")[0] if "/" in raw else raw
        return self.vault_root / base

    def _daily_path(self, d: date) -> Path:
        raw = self.vault_config("entries", "50_Journal/{year}/{date}.md")
        path = raw.replace("{year}", str(d.year)).replace("{date}", d.isoformat())
        return self.vault_root / path

    @cached_property
    def _write_locks(self) -> dict[str, asyncio.Lock]:
        return {}

    def _daily_lock(self, d: date) -> asyncio.Lock:
        # Serializes the read-modify-write of one daily note. Without this,
        # a user POST racing with a reactor ripple (focus:completed,
        # healing:mood-logged, publish:built — ~30 subscribed events) both
        # read the pre-write content and the later write wipes the earlier
        # writer's entry.
        key = d.isoformat()
        lock = self._write_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._write_locks[key] = lock
        return lock

    async def _ensure_daily(self, d: date) -> str:
        """Get or create daily note. Returns content."""
        path = self._daily_path(d)
        try:
            return await self.read(str(path))
        except Exception:
            weekday = d.strftime("%A")
            content = (
                f"---\ndate: {d.isoformat()}\ntags:\n  - daily\n---\n\n"
                f"# {d.isoformat()} {weekday}\n\n"
                f"### Journal\n\n\n"
                f"### Milestone\n\n\n"
                f"#### Three successful things\n\n"
                f"1. \n2. \n3. \n"
            )
            await self.write(str(path), content)
            await self.emit("journal:created", {"date": d.isoformat()})
            return content

    # --- CLI ---

    @cli_command("journal", help="Daily journal operations")
    async def cmd_journal(self, action: str = "today", text: str = "", mood: str = "okay"):
        today = date.today()
        if action == "today":
            content = await self._ensure_daily(today)
            entries = parse_entries(content)
            if entries:
                for e in entries:
                    self.print_rich(f"  {e['time']} {e['emoji']} {e['text']}")
            else:
                self.print_rich("[dim]No entries today.[/dim]")
        elif action == "add" and text:
            await self._add_entry(today, text, mood)
            self.print_rich(f"[green]Added:[/green] {MOOD_EMOJI.get(mood, '😐')} {text}")
        elif action == "recent":
            recent = await self._recent_days(7)
            for r in recent:
                self.print_rich(f"  {r['date']}  {r['entries']} entries  {r.get('mood', '')}")
        else:
            self.print_rich("[dim]Usage: eos journal [today|add|recent] [text] [--mood great|good|okay|low|bad][/dim]")

    # --- Web API ---

    @web_route("GET", "/api/today")
    async def api_today(self, request):
        d = request.query_params.get("date", date.today().isoformat())
        try:
            target = date.fromisoformat(d)
        except ValueError:
            target = date.today()
        content = await self._ensure_daily(target)
        entries = parse_entries(content)
        milestone = extract_section(content, "### Milestone")
        three_things = extract_section(content, "#### Three successful things")
        journal_section = extract_section(content, "### Journal")
        by_dimension = await self._today_dimension_signals(target)
        return {
            "date": target.isoformat(),
            "content": journal_section,
            "entries": entries,
            "milestone": milestone,
            "three_things": three_things,
            "by_dimension": by_dimension,
        }

    @web_route("GET", "/api/read-feed")
    async def api_read_feed(self, request):
        """Hands-free read-aloud adapter. Returns today's journal sections as
        individual items — milestone, three-things list (each item separately),
        then timestamped entries. Read-only: no `act` — Victory is a no-op here.
        """
        d_raw = request.query_params.get("date", date.today().isoformat())
        try:
            target = date.fromisoformat(d_raw)
        except ValueError:
            target = date.today()
        content = await self._ensure_daily(target)
        entries = parse_entries(content)
        milestone = extract_section(content, "### Milestone") or ""
        three_things = extract_section(content, "#### Three successful things") or ""

        items = []
        iso = target.isoformat()

        if milestone.strip():
            items.append({
                "id": f"journal-{iso}-milestone",
                "text": f"Milestone: {milestone.strip()}",
                "source": "journal",
            })

        tt_lines = [ln.strip(" -\t") for ln in three_things.split("\n") if ln.strip(" -\t")]
        for i, ln in enumerate(tt_lines[:3]):
            items.append({
                "id": f"journal-{iso}-three-{i}",
                "text": f"Three things, item {i+1}: {ln}",
                "source": "journal",
            })

        for i, e in enumerate(entries or []):
            text = (e.get("text") or e.get("content") or "").strip()
            if not text:
                continue
            mood = e.get("mood") or ""
            mood_prefix = f"{mood}, " if mood else ""
            items.append({
                "id": f"journal-{iso}-entry-{i}",
                "text": f"Entry {i+1}, {mood_prefix}{text}",
                "source": "journal",
            })

        if not items:
            items.append({
                "id": f"journal-{iso}-empty",
                "text": f"No journal entries for {iso} yet.",
                "source": "journal",
            })

        return {"items": items, "source": "journal", "date": iso, "count": len(items)}

    @web_route("GET", "/api/dimensions")
    async def api_dimensions(self, request):
        """Today's 8-dimension signal aggregation (captures + habits + journal entries)."""
        d = request.query_params.get("date", date.today().isoformat())
        try:
            target = date.fromisoformat(d)
        except ValueError:
            target = date.today()
        return {
            "date": target.isoformat(),
            "by_dimension": await self._today_dimension_signals(target),
            "dimensions": list(dimensions.DIMENSIONS),
            "icons": dimensions.ICONS,
            "labels": dimensions.LABELS,
        }

    async def _today_dimension_signals(self, d: date) -> dict[str, dict]:
        """Aggregate today's dimension signals across captures + habits + journal text."""
        signals = {dim: {"captures": 0, "habits_done": 0, "habits_total": 0, "journal": 0} for dim in dimensions.DIMENSIONS}
        today_iso = d.isoformat()

        try:
            cap_rel = self.kernel.vault_map.get("quick-action", "inbox", "00_Inbox/_captures.md")  # owning app is quick-action (formerly capture)
            cap_path = (self.vault_root / cap_rel) if cap_rel else None
            if cap_path and cap_path.exists():
                content = await self.read(str(cap_path))
                for c in parse_captures(content, limit=500):
                    if not c["timestamp"].startswith(today_iso):
                        continue
                    dim = dimensions.resolve(c["tag"]) or (dimensions.extract(c["text"])[:1] or [""])[0]
                    if dim in signals:
                        signals[dim]["captures"] += 1
        except Exception:
            pass

        if d == date.today():
            try:
                summary = await self.call_app("healing", "habits_today_summary")
                by = (summary or {}).get("by_dimension", {})
                for dim, v in by.items():
                    if dim in signals:
                        signals[dim]["habits_done"] = v.get("done", 0)
                        signals[dim]["habits_total"] = v.get("total", 0)
            except Exception:
                pass

        try:
            path = self._daily_path(d)
            content = await self.read(str(path))
            entries = parse_entries(content)
            for e in entries:
                for dim in dimensions.extract(e.get("text", "")):
                    if dim in signals:
                        signals[dim]["journal"] += 1
        except Exception:
            pass

        for dim, s in signals.items():
            s["total"] = s["captures"] + s["habits_done"] + s["journal"]
        return signals

    @web_route("POST", "/api/entry")
    async def api_entry(self, request):
        data = await request.json()
        text = data.get("text", "")
        mood = data.get("mood", "okay")
        d = date.today()
        if data.get("date"):
            try:
                d = date.fromisoformat(data["date"])
            except ValueError:
                pass
        if not text:
            return {"error": "text required"}
        try:
            await self._add_entry(d, text, mood)
        except ValueError as e:
            return {"error": str(e)}
        return {"status": "ok", "date": d.isoformat(), "mood": mood}

    @web_route("GET", "/api/recent")
    async def api_recent(self, request):
        days = int(request.query_params.get("days", "14"))
        return await self._recent_days(days)

    @web_route("GET", "/api/heatmap")
    async def api_heatmap(self, request):
        months = int(request.query_params.get("months", "3"))
        return await self._heatmap(months)

    @web_route("GET", "/api/mood-trend")
    async def api_mood_trend(self, request):
        days = int(request.query_params.get("days", "30"))
        return await self._mood_trend(days)

    @web_route("GET", "/api/reflect")
    async def api_reflect(self, request):
        days = int(request.query_params.get("days", "7"))
        return {"prompts": await self._reflection_prompts(days)}

    @web_route("POST", "/api/ai-reflect")
    async def api_ai_reflect(self, request):
        """LLM generates a reflection from recent journal entries."""
        data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        days = int(data.get("days", 7))

        today = date.today()
        all_text = []
        for i in range(days):
            d = today - timedelta(days=i)
            path = self._daily_path(d)
            try:
                content = await self.read(str(path))
                entries = parse_entries(content)
                if entries:
                    lines = [f"{d.isoformat()} {e['time']} {e['emoji']} {e['text']}" for e in entries]
                    all_text.extend(lines)
            except Exception:
                continue

        if not all_text:
            return {"reflection": "No journal entries found in the last {} days. Start writing to get reflections!".format(days)}

        reflection = await self.think_safe(
            f"Journal entries from the last {days} days:\n\n" + "\n".join(all_text),
            system=REFLECT_SYSTEM, domain="text", temperature=0.6,
            fallback="AI is offline — your entries are below unchanged. Reflection will return when AI is available.",
        )
        await self.emit("journal:reflection", {"days": days, "entry_count": len(all_text)})
        return {"reflection": reflection, "days": days, "entries_analyzed": len(all_text), "provenance": self.last_provenance()}

    @web_route("GET", "/api/search")
    async def api_search(self, request):
        """Search journal entries across dates."""
        q = request.query_params.get("q", "").strip().lower()
        if not q:
            return {"results": [], "error": "q parameter required"}
        days = int(request.query_params.get("days", "90"))
        today = date.today()
        results = []
        for i in range(days):
            d = today - timedelta(days=i)
            path = self._daily_path(d)
            try:
                content = await self.read(str(path))
            except Exception:
                continue
            entries = parse_entries(content)
            matching = [e for e in entries if q in e.get("text", "").lower()]
            if matching:
                results.append({"date": d.isoformat(), "entries": matching})
        return {"results": results, "total_matches": sum(len(r["entries"]) for r in results), "query": q}

    @web_route("GET", "/api/templates")
    async def api_templates(self, request):
        """List available journal templates."""
        return {
            "templates": [
                {
                    "id": "morning",
                    "name": "Morning Check-in",
                    "prompts": [
                        "How did I sleep?",
                        "What's my energy level? (1-10)",
                        "Top 3 priorities for today:",
                        "One thing I'm grateful for:",
                    ],
                },
                {
                    "id": "evening",
                    "name": "Evening Reflection",
                    "prompts": [
                        "What went well today?",
                        "What was challenging?",
                        "What did I learn?",
                        "How am I feeling right now?",
                    ],
                },
                {
                    "id": "weekly",
                    "name": "Weekly Review",
                    "prompts": [
                        "Biggest achievement this week:",
                        "What didn't get done? Why?",
                        "Relationships: who did I connect with?",
                        "Health: exercise, sleep, nutrition check",
                        "One adjustment for next week:",
                    ],
                },
            ]
        }

    @web_route("POST", "/api/pin")
    async def api_pin(self, request):
        """Pin/unpin a journal entry as highlight."""
        data = await request.json()
        pins = self._load_pins()
        entry = {"date": data.get("date", ""), "time": data.get("time", ""), "text": data.get("text", "")}
        key = f"{entry['date']}_{entry['time']}"
        if key in pins:
            del pins[key]
            self._save_pins(pins)
            return {"pinned": False, "key": key}
        pins[key] = entry
        self._save_pins(pins)
        return {"pinned": True, "key": key}

    @web_route("GET", "/api/pins")
    async def api_pins(self, request):
        """Get all pinned journal entries."""
        return list(self._load_pins().values())

    @web_route("GET", "/api/export")
    async def api_export(self, request):
        """Export journal entries as markdown text."""
        days = int(request.query_params.get("days", "30"))
        today = date.today()
        lines = [f"# Journal Export ({days} days)\n"]
        for i in range(days):
            d = today - timedelta(days=i)
            path = self._daily_path(d)
            try:
                content = await self.read(str(path))
                entries = parse_entries(content)
                if entries:
                    lines.append(f"\n## {d.isoformat()} {d.strftime('%A')}\n")
                    for e in entries:
                        lines.append(f"- **{e['time']}** {e['emoji']} {e['text']}")
            except Exception:
                continue
        return {"markdown": "\n".join(lines), "days": days}

    @web_route("GET", "/api/streak")
    async def api_streak(self, request):
        """Journaling streak — consecutive days with entries."""
        streak = 0
        d = date.today()
        while True:
            path = self._daily_path(d)
            try:
                content = await self.read(str(path))
                entries = parse_entries(content)
                if entries:
                    streak += 1
                    d -= timedelta(days=1)
                    continue
            except Exception:
                pass
            break
        return {"streak": streak}

    @web_route("GET", "/api/word-count")
    async def api_word_count(self, request):
        """Word count stats for recent journal entries."""
        days = int(request.query_params.get("days", "30"))
        today = date.today()
        daily = []
        total = 0
        for i in range(days):
            d = today - timedelta(days=i)
            try:
                content = await self.read(str(self._daily_path(d)))
                wc = len(content.split())
                daily.append({"date": d.isoformat(), "words": wc})
                total += wc
            except Exception:
                continue
        return {"total_words": total, "days_written": len(daily), "avg_words": round(total / len(daily)) if daily else 0, "daily": daily}

    @web_route("POST", "/api/milestone")
    async def api_milestone(self, request):
        """Add a milestone entry to today's journal under ### Milestones."""
        data = await request.json()
        text = data.get("text", "").strip()
        if not text:
            return {"error": "text required"}

        today = date.today()
        async with self._daily_lock(today):
            content = await self._ensure_daily(today)

            entry = f"- 🏆 {text}"
            milestone_section = extract_section(content, "### Milestones")
            new_section = (milestone_section + "\n" + entry).strip() if milestone_section else entry
            new_content = replace_section(content, "### Milestones", new_section)
            await self.write(str(self._daily_path(today)), new_content)
        await self.emit("journal:milestone", {"date": today.isoformat(), "text": text})
        return {"ok": True, "date": today.isoformat()}

    @web_route("POST", "/api/three-things")
    async def api_three_things(self, request):
        """Save three-things reflection: grateful, accomplished, learned."""
        data = await request.json()
        grateful = data.get("grateful", "").strip()
        accomplished = data.get("accomplished", "").strip()
        learned = data.get("learned", "").strip()
        if not any([grateful, accomplished, learned]):
            return {"error": "at least one field required"}

        today = date.today()
        async with self._daily_lock(today):
            content = await self._ensure_daily(today)

            lines = []
            if grateful:
                lines.append(f"- 🙏 **Grateful**: {grateful}")
            if accomplished:
                lines.append(f"- ✅ **Accomplished**: {accomplished}")
            if learned:
                lines.append(f"- 💡 **Learned**: {learned}")

            entry = "\n".join(lines)
            section = extract_section(content, "### Three Things")
            new_section = (section + "\n" + entry).strip() if section else entry
            new_content = replace_section(content, "### Three Things", new_section)
            await self.write(str(self._daily_path(today)), new_content)
        await self.emit("journal:three-things", {"date": today.isoformat()})
        return {"ok": True, "date": today.isoformat()}

    async def get_tasks(self, days: int = 90) -> list[dict]:
        """All tasks (- [ ] / - [x]) from journal notes."""
        today = date.today()
        vault = self.vault_root
        all_tasks = []
        for i in range(days):
            d = today - timedelta(days=i)
            path = self._daily_path(d)
            try:
                content = await self.read(str(path))
            except Exception:
                continue
            rel_path = str(path.relative_to(vault))
            for line_num, line in enumerate(content.split("\n"), 1):
                m = TASK_RE.match(line.strip())
                if not m:
                    continue
                is_done = m.group(1) in ("x", "X")
                text = m.group(2).strip()
                due_str = m.group(3) or ""
                done_date = m.group(4) or ""
                overdue_days, tier = compute_task_decay(due_str, today) if due_str and not is_done else (0, "fresh")
                all_tasks.append({
                    "text": text,
                    "done": is_done,
                    "file": rel_path,
                    "line": line_num,
                    "due": due_str,
                    "done_date": done_date,
                    "overdue_days": overdue_days,
                    "tier": tier,
                })
        return all_tasks

    @web_route("GET", "/api/tasks")
    async def api_tasks(self, request):
        """All tasks from journal notes."""
        days = int(request.query_params.get("days", "90"))
        return await self.get_tasks(days)

    async def get_weekly(self, d: str = "") -> dict:
        """Weekly note content."""
        try:
            target = date.fromisoformat(d) if d else date.today()
        except ValueError:
            target = date.today()
        raw = self.vault_config("weekly", "50_Journal/{year}/{year}-W{week}.md")
        iso_cal = target.isocalendar()
        path = self.vault_root / raw.replace("{year}", str(iso_cal[0])).replace("{week}", f"{iso_cal[1]:02d}")
        try:
            content = await self.read(str(path))
            return {"date": d or target.isoformat(), "path": str(path), "content": content}
        except Exception:
            return {"date": d or target.isoformat(), "path": str(path), "content": ""}

    async def get_daily_raw(self, d: str = "") -> dict:
        """Raw daily-note content for a date (no ensure/create). Returns {date, path, content}."""
        try:
            target = date.fromisoformat(d) if d else date.today()
        except ValueError:
            target = date.today()
        path = self._daily_path(target)
        try:
            content = await self.read(str(path))
        except Exception:
            content = ""
        return {"date": target.isoformat(), "path": str(path), "content": content}

    async def get_yearly_raw(self, year: int = 0) -> dict:
        """Raw yearly-plan content. Returns {year, path, content}."""
        y = int(year) if year else date.today().year
        base = self._journal_dir()
        path = base / str(y) / f"{y}.md"
        try:
            content = await self.read(str(path))
        except Exception:
            content = ""
        return {"year": y, "path": str(path), "content": content}

    async def list_weekly_notes(self) -> list[dict]:
        """All weekly notes in vault, newest first. Returns [{date, path, content}]."""
        results = []
        base = self._journal_dir()
        if not base.exists():
            return results
        for year_dir in sorted(base.iterdir(), reverse=True):
            if not year_dir.is_dir():
                continue
            for week_file in sorted(year_dir.iterdir(), reverse=True):
                if not week_file.is_file() or "-W" not in week_file.name:
                    continue
                try:
                    content = await self.read(str(week_file))
                except Exception:
                    continue
                results.append({
                    "date": week_file.stem,
                    "path": str(week_file),
                    "content": content,
                })
        return results

    @web_route("GET", "/api/weekly")
    async def api_weekly(self, request):
        """Weekly note content."""
        d = request.query_params.get("date", "")
        return await self.get_weekly(d)

    @web_route("GET", "/api/wheel-review")
    async def api_wheel_review(self, request):
        """LLM-generated wheel review. period=week (7d) or month (30d)."""
        period = request.query_params.get("period", "week").lower()
        days = 30 if period == "month" else 7
        return await self._wheel_review(days=days, period=period)

    async def _wheel_review(self, days: int, period: str) -> dict:
        signals = _wheel.collect_signals(self.kernel, days)
        reading = dimensions.balance_score(signals)
        total = reading["total"]
        if total == 0:
            return {
                "period": period,
                "days": days,
                "signals": signals,
                "narrative": f"No behavioral signal in the last {days} days. Write in your journal and the wheel will have something to reflect.",
            }

        mean = reading["mean"]
        data_block = "\n".join(
            f"{dimensions.LABELS[d]}: {signals[d]} (mean={mean:.1f})"
            for d in dimensions.DIMENSIONS
        )
        label = "This Week" if period == "week" else "This Month"
        user_msg = (
            f"Period: {label} (last {days} days)\n"
            f"Total signals: {total} | Thin: {reading.get('thin') or 'none'} | "
            f"Dominant: {reading.get('dominant') or 'none'} | Grade: {reading.get('grade')}\n\n"
            f"Per-dimension counts:\n{data_block}"
        )
        try:
            narrative = await self.think(
                user_msg, system=WHEEL_REVIEW_SYSTEM,
                domain="text", temperature=0.4,
            )
        except Exception as e:
            narrative = f"Could not generate review: {e}"
        return {
            "period": period,
            "days": days,
            "signals": signals,
            "reading": reading,
            "narrative": narrative,
        }

    @scheduled("0 21 * * 0", id="weekly-wheel-review")
    async def scheduled_weekly_wheel_review(self):
        """Sunday 9pm — write a wheel review into the weekly note."""
        result = await self._wheel_review(days=7, period="week")
        narrative = result.get("narrative", "")
        if (not narrative
                or narrative.startswith("No behavioral signal")
                or narrative.startswith("Could not generate")):
            return
        today = date.today()
        raw = self.vault_config("weekly", "50_Journal/{year}/{year}-W{week}.md")
        iso_cal = today.isocalendar()
        wp = self.vault_root / raw.replace("{year}", str(iso_cal[0])).replace("{week}", f"{iso_cal[1]:02d}")
        header = f"\n\n## Wheel Review — Week {iso_cal[1]}\n\n"
        try:
            existing = await self.read(str(wp))
        except Exception:
            existing = f"# {iso_cal[0]}-W{iso_cal[1]:02d}\n"
        marker = f"## Wheel Review — Week {iso_cal[1]}"
        if marker in existing:
            parts = existing.split(marker, 1)
            before = parts[0].rstrip()
            after = parts[1]
            next_h = after.find("\n## ")
            rest = after[next_h:] if next_h != -1 else ""
            new_content = before + header + narrative + ("\n" + rest if rest else "\n")
        else:
            new_content = existing.rstrip() + header + narrative + "\n"
        await self.write(str(wp), new_content)
        await self.emit("journal:wheel-review", {"period": "week", "week": iso_cal[1]})

    @web_route("GET", "/api/milestones")
    async def api_milestones(self, request):
        """List milestones from recent journal entries."""
        days = int(request.query_params.get("days", "90"))
        today = date.today()
        milestones = []
        for i in range(days):
            d = today - timedelta(days=i)
            try:
                content = await self.read(str(self._daily_path(d)))
                section = extract_section(content, "### Milestones")
                if section:
                    for line in section.split("\n"):
                        line = line.strip()
                        if line.startswith("- "):
                            milestones.append({"date": d.isoformat(), "text": line[2:].strip()})
            except Exception:
                continue
        return milestones

    def _load_pins(self) -> dict:
        import json
        p = self.data_dir / "pins.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _save_pins(self, pins: dict):
        import json
        (self.data_dir / "pins.json").write_text(json.dumps(pins, indent=2, default=str))

    # --- Core Logic ---

    async def _add_entry(self, d: date, text: str, mood: str = "okay"):
        text = (text or "").strip()
        if not text:
            raise ValueError("entry text is empty")
        # Guards against a prior bug where the UI dumped the full rendered
        # section back into the textarea and autosave POSTed it as a new
        # entry's text, cascading into multi-MB journals.
        # TODO(extract): when a second "append one line to a markdown section"
        # app needs this (likely capture.add, task.add, or reactor ripples),
        # lift to BaseApp.assert_single_line(text) in emptyos/sdk/base_app.py.
        if "\n" in text or text.startswith("- **"):
            raise ValueError("entry text must be a single plain line (no markdown entry prefix)")
        async with self._daily_lock(d):
            content = await self._ensure_daily(d)
            now = datetime.now(timezone.utc).strftime("%H:%M")
            emoji = MOOD_EMOJI.get(mood, "😐")
            entry_line = f"- **{now}** {emoji} {text}"

            journal_section = extract_section(content, "### Journal")
            new_section = (journal_section + "\n" + entry_line).strip() if journal_section else entry_line
            new_content = replace_section(content, "### Journal", new_section)
            await self.write(str(self._daily_path(d)), new_content)
        # Emit outside the lock — handlers that recurse back into journal
        # would deadlock otherwise.
        await self.emit("journal:entry", {"date": d.isoformat(), "mood": mood, "text": text})

    # ── Voice intent (manifest: [[contributes.voice-assistant.intent]]) ──

    async def voice_add_entry(self, text: str, mood: str = "okay") -> dict:
        """Voice intent → append a single-line entry to today's journal."""
        text = (text or "").strip()
        if not text:
            return {"say": "I didn't catch the entry."}
        # Normalize accidental newlines from the LLM into single-line form so
        # the _add_entry single-line guard doesn't reject natural speech.
        text = " ".join(text.split())
        valid_moods = {"great", "good", "okay", "low", "bad"}
        mood = (mood or "okay").lower()
        if mood not in valid_moods:
            mood = "okay"
        try:
            await self._add_entry(date.today(), text, mood)
        except Exception as e:
            return {"say": f"Couldn't save that — {e}"}
        return {"say": "Logged."}

    # ------------------------------------------------------------------
    # Hub slot contributions — manifest: [[contributes.hub.<slot>]]
    # ------------------------------------------------------------------

    # ── Hub slots + panels — bound from panels.py ──

    slot_today = _panels.slot_today
    slot_recent_thinking = _panels.slot_recent_thinking
    panel_yesterday = _panels.panel_yesterday
    panel_journal_today = _panels.panel_journal_today
    panel_month_compare = _panels.panel_month_compare

    async def _recent_days(self, n: int) -> list[dict]:
        results = []
        today = date.today()
        for i in range(n):
            d = today - timedelta(days=i)
            path = self._daily_path(d)
            try:
                content = await self.read(str(path))
                entries = parse_entries(content)
                dominant = max(
                    set(e["mood"] for e in entries),
                    key=lambda m: sum(1 for e in entries if e["mood"] == m),
                ) if entries else ""
                results.append({
                    "date": d.isoformat(),
                    "entries": len(entries),
                    "mood": dominant,
                    "emoji": MOOD_EMOJI.get(dominant, ""),
                })
            except Exception:
                continue
        return results

    async def _heatmap(self, months: int) -> list[dict]:
        today = date.today()
        start = today - timedelta(days=months * 30)
        data = []
        d = start
        while d <= today:
            path = self._daily_path(d)
            try:
                content = await self.read(str(path))
                entries = parse_entries(content)
                data.append({"date": d.isoformat(), "count": len(entries)})
            except Exception:
                data.append({"date": d.isoformat(), "count": 0})
            d += timedelta(days=1)
        return data

    async def _mood_trend(self, days: int) -> list[dict]:
        results = []
        today = date.today()
        for i in range(days):
            d = today - timedelta(days=i)
            path = self._daily_path(d)
            try:
                content = await self.read(str(path))
                entries = parse_entries(content)
                if entries:
                    avg = sum(MOOD_SCORE.get(e["mood"], 3) for e in entries) / len(entries)
                    results.append({"date": d.isoformat(), "score": round(avg, 1)})
            except Exception:
                continue
        return list(reversed(results))

    async def _reflection_prompts(self, days: int) -> list[str]:
        recent = await self._recent_days(days)
        if not recent:
            return ["Start journaling today — write your first entry."]
        summary = ", ".join(f"{r['date']}: {r['entries']} entries ({r['mood']})" for r in recent[:7])
        response = await self.think(
            f"Recent journal activity:\n{summary}",
            system=PROMPT_GEN_SYSTEM, temperature=0.8,
        )
        return parse_llm_json(response, fallback=[
            "What made today different from yesterday?",
            "What's one thing you'd do differently this week?",
            "What are you most grateful for right now?",
        ])

    # ------------------------------------------------------------------
    # Voice Assistant contribution
    # ------------------------------------------------------------------

    async def assistant_context(self) -> str | None:
        """Contributes today's journal entries to the Voice Assistant context."""
        try:
            today = date.today()
            content = await self._ensure_daily(today)
            from .parser import parse_entries
            entries = parse_entries(content)
            
            if not entries:
                return None
                
            out = "Today's Journal Entries (what the user has noted so far today):\n"
            for e in entries:
                out += f"- {e.get('time', '')}: {e.get('text', '')}\n"
            return out
        except Exception:
            return None
