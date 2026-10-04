"""Journal — daily journaling with mood tracking and reflection.

Vault structure: 50_Journal/{YEAR}/{YYYY-MM-DD}.md
Sections: Journal (timestamped entries), Milestone, Three successful things
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from emptyos.sdk import (
    TASK_RE,
    BaseApp,
    cli_command,
    compute_task_decay,
    parse_frontmatter,
    set_frontmatter_field,
    web_route,
)
from emptyos.sdk.text_guards import assert_single_line
from emptyos.sdk.utils import clamp_days

from . import analytics as _analytics
from . import milestones as _milestones
from . import panels as _panels
from . import reflection as _reflection
from . import related as _related
from .parser import AUTHOR_MARKER, MOOD_EMOJI, extract_section, parse_entries, replace_section


class JournalApp(BaseApp):

    def _atomic_note_writes_enabled(self) -> bool:
        return bool(self.setting_or_config(
            "journal.feature.atomic-note-writes.enabled",
            False,
            config_key="feature.atomic-note-writes.enabled",
        ))

    async def _write_note(self, path: Path, content: str):
        """Write a journal note, optionally through atomic replacement."""
        return await self.write(
            str(path), content, atomic=self._atomic_note_writes_enabled()
        )

    async def setup(self):
        await super().setup()
        # Pre-warm the related-entries embedding index in the background so
        # the first user click on a fresh boot doesn't pay the cold-cache
        # cost (3-year corpus walk + per-chunk embed call ≈ 30–60s on
        # OpenAI). Quiet failure — the related panel handles unavailable
        # embeddings gracefully.
        if getattr(self, "embeddings_available", False):
            # spawn_background, not a bare create_task: asyncio only weak-refs a
            # running task, so this warm-up could be collected mid-flight.
            self.spawn_background(self._warm_related_index(), label="related index")

    async def _warm_related_index(self):
        try:
            items = await self._collect_journal_entries()
            if items:
                await self.embedding_index(items, text_fn=lambda it: it["text"])
        except Exception:
            pass

    async def get_summary(self, date_str: str = "") -> dict:
        """Summary for staff observers — entries, streak, mood for a date.

        ``date_str`` (ISO) anchors the summary to a specific day; default today.
        Returns ``entries`` as an alias of ``today_entries`` so date-scoped
        callers (e.g. the briefing digest) can read either key.
        """
        try:
            today = date.fromisoformat(date_str) if date_str else date.today()
        except ValueError:
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
        return {"today_entries": len(entries), "entries": len(entries),
                "streak": streak, "mood": dominant_mood}

    async def companion_context(self) -> str | None:
        """Focus-mode live context — today's journal state at a glance."""
        try:
            s = await self.get_summary()
        except Exception:
            return None
        n = s.get("today_entries", 0)
        bits = [f"{n} journal entr{'y' if n == 1 else 'ies'} today"]
        if s.get("streak"):
            bits.append(f"{s['streak']}-day streak")
        if s.get("mood"):
            bits.append(f"mood: {s['mood']}")
        return "; ".join(bits) + "."

    async def timeline_items(self, days: int = 1) -> list[dict]:
        """Life-suite timeline contribution ([[contributes.life.timeline]]).

        One item per journal entry over the last ``days`` days, most recent
        first. Item shape (suite contract, docs/suites/life-cohesion.md):
        {ts, title, kind, href} + mood/emoji extras. AI/reactor breadcrumbs
        (the ``auto`` flag from parse_entries) surface as kind
        "journal-auto" so consumers can style provenance.
        """
        n = clamp_days(days)
        items: list[dict] = []
        today = date.today()
        for i in range(n):
            d = today - timedelta(days=i)
            try:
                content = await self.read(str(self._daily_path(d)))
            except Exception:
                continue
            for e in parse_entries(content):
                t = (e.get("time") or "").strip()
                ts = f"{d.isoformat()}T{t}:00" if len(t) == 5 else f"{d.isoformat()}T00:00:00"
                items.append({
                    "ts": ts,
                    "title": e.get("text", ""),
                    "kind": "journal-auto" if e.get("auto") else "journal",
                    "href": "/journal/",
                    "mood": e.get("mood", ""),
                    "emoji": e.get("emoji", ""),
                })
        items.sort(key=lambda x: x["ts"], reverse=True)
        return items

    @web_route("GET", "/api/timeline-items")
    async def api_timeline_items(self, request):
        return {"items": await self.timeline_items(days=request.query_params.get("days"))}

    def _journal_dir(self) -> Path:
        # entries template: "50_Journal/{year}/{date}.md"
        raw = self.vault_config("entries", "50_Journal/{year}/{date}.md")
        base = raw.split("/")[0] if "/" in raw else raw
        return self.vault_root / base

    def _daily_path(self, d: date) -> Path:
        raw = self.vault_config("entries", "50_Journal/{year}/{date}.md")
        path = raw.replace("{year}", str(d.year)).replace("{date}", d.isoformat())
        return self.vault_root / path

    def _daily_lock(self, d: date) -> asyncio.Lock:
        # Serializes the read-modify-write of one daily note. Without this,
        # a user POST racing with a reactor ripple (focus:completed,
        # healing:mood-logged, publish:built — ~30 subscribed events) both
        # read the pre-write content and the later write wipes the earlier
        # writer's entry. Delegates to BaseApp.write_lock since the
        # per-key-asyncio.Lock pattern extracted 2026-05-25.
        return self.write_lock(f"daily:{d.isoformat()}")

    async def _ensure_daily(self, d: date) -> str:
        """Get or create daily note. Returns content."""
        path = self._daily_path(d)
        try:
            return await self.read(str(path))  # noqa: eos-rmw  (write callers hold _daily_lock)
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
            await self._write_note(path, content)
            await self.emit("journal:created", {"date": d.isoformat()})
            return content

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
            self.print_rich(
                "[dim]Usage: eos journal [today|add|recent] [text] [--mood great|good|okay|low|bad][/dim]"
            )

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
            # Feature-detect for the ✨ "Draft from today" button (dark-flagged),
            # so the page doesn't need a separate probe call.
            "draft_enabled": bool(self.app_config("feature.three-things-draft.enabled", False)),
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
            items.append(
                {
                    "id": f"journal-{iso}-milestone",
                    "text": f"Milestone: {milestone.strip()}",
                    "source": "journal",
                }
            )

        tt_lines = [ln.strip(" -\t") for ln in three_things.split("\n") if ln.strip(" -\t")]
        for i, ln in enumerate(tt_lines[:3]):
            items.append(
                {
                    "id": f"journal-{iso}-three-{i}",
                    "text": f"Three things, item {i + 1}: {ln}",
                    "source": "journal",
                }
            )

        for i, e in enumerate(entries or []):
            text = (e.get("text") or e.get("content") or "").strip()
            if not text:
                continue
            mood = e.get("mood") or ""
            mood_prefix = f"{mood}, " if mood else ""
            items.append(
                {
                    "id": f"journal-{iso}-entry-{i}",
                    "text": f"Entry {i + 1}, {mood_prefix}{text}",
                    "source": "journal",
                }
            )

        if not items:
            items.append(
                {
                    "id": f"journal-{iso}-empty",
                    "text": f"No journal entries for {iso} yet.",
                    "source": "journal",
                }
            )

        return {"items": items, "source": "journal", "date": iso, "count": len(items)}

    @web_route("POST", "/api/add")
    async def api_add(self, request):
        return await self.api_entry(request)

    @web_route("POST", "/api/entry")
    async def api_entry(self, request):
        data = await self.read_json(request)
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

    _RELATED_MIN_SCORE = 0.40  # entries below this aren't surfaced

    _RELATED_MIN_CHARS = 30  # skip one-liners — they embed poorly

    _RELATED_LOOKBACK_DAYS = 365 * 3  # cap the corpus walk so cold cache stays bounded

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
                overdue_days, tier = (
                    compute_task_decay(due_str, today) if due_str and not is_done else (0, "fresh")
                )
                all_tasks.append(
                    {
                        "text": text,
                        "done": is_done,
                        "file": rel_path,
                        "line": line_num,
                        "due": due_str,
                        "done_date": done_date,
                        "overdue_days": overdue_days,
                        "tier": tier,
                    }
                )
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
        path = self.vault_root / raw.replace("{year}", str(iso_cal[0])).replace(
            "{week}", f"{iso_cal[1]:02d}"
        )
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
                results.append(
                    {
                        "date": week_file.stem,
                        "path": str(week_file),
                        "content": content,
                    }
                )
        return results

    @web_route("GET", "/api/weekly")
    async def api_weekly(self, request):
        """Weekly note content."""
        d = request.query_params.get("date", "")
        return await self.get_weekly(d)

    async def _add_entry(self, d: date, text: str, mood: str = "okay", *, as_ai: bool = False):
        text = (text or "").strip()
        if not text:
            raise ValueError("entry text is empty")
        # Guards against a prior bug where the UI dumped the full rendered
        # section back into the textarea and autosave POSTed it as a new
        # entry's text, cascading into multi-MB journals.
        assert_single_line(text, label="entry text")
        if text.startswith("- **"):
            raise ValueError("entry text must not start with markdown entry prefix")
        async with self._daily_lock(d):
            content = await self._ensure_daily(d)
            now = datetime.now(UTC).strftime("%H:%M")
            emoji = MOOD_EMOJI.get(mood, "😐")
            # AI callers (reactor breadcrumbs) tag the line so the UI can
            # distinguish a system breadcrumb from a human entry. The marker is
            # an invisible HTML comment; parse_entries strips it back out.
            suffix = f" {AUTHOR_MARKER}" if as_ai else ""
            entry_line = f"- **{now}** {emoji} {text}{suffix}"

            # Reject an identical entry from the last 2 hours so a stuck
            # autosave or muscle-memory re-submit can't fatten today's note.
            cmp_text = text.strip().lower()
            now_h, now_m = (int(x) for x in now.split(":"))
            now_min = now_h * 60 + now_m
            for e in parse_entries(content):
                if (e.get("text") or "").strip().lower() != cmp_text:
                    continue
                hh, _, mm = (e.get("time") or "").partition(":")
                if hh.isdigit() and mm.isdigit() and abs(now_min - (int(hh) * 60 + int(mm))) <= 120:
                    raise ValueError(
                        "duplicate of an entry from the last 2 hours — "
                        "edit the existing line instead of re-adding"
                    )

            journal_section = extract_section(content, "### Journal")
            new_section = (
                (journal_section + "\n" + entry_line).strip() if journal_section else entry_line
            )
            new_content = replace_section(content, "### Journal", new_section)
            # Authorship-boundary rule: when reactor (or any AI caller) appends
            # to a daily note, declare co-authorship. user→both, both/ai→stay.
            # 50_Journal/ defaults to user, so absence == user.
            if as_ai:
                fm = parse_frontmatter(new_content)
                current = (fm.get("author") or "user").strip().lower()
                if current == "user":
                    new_content = set_frontmatter_field(new_content, "author", "both")
            await self._write_note(self._daily_path(d), new_content)
        # Emit outside the lock — handlers that recurse back into journal
        # would deadlock otherwise.
        await self.emit("journal:entry", {"date": d.isoformat(), "mood": mood, "text": text})

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
        return {
            "say": "Logged.",
            "link": {"text": "Open today's journal", "href": "/journal/"},
        }

    async def voice_today_summary(self) -> dict:
        """Voice verb — today's journal state (entries, streak, mood)."""
        try:
            s = await self.get_summary()
        except Exception:
            return {"say": "Couldn't read your journal just now."}
        n = s.get("today_entries", 0)
        streak = s.get("streak", 0)
        mood = s.get("mood", "")
        if not n:
            say = "You haven't journaled yet today."
            if streak:
                say += f" Your streak is {streak} day(s) — write one to keep it going."
        else:
            say = f"{n} journal entr{'y' if n == 1 else 'ies'} today"
            say += f", a {streak}-day streak" if streak else ""
            say += f", feeling {mood}." if mood else "."
        fields = [{"label": "Entries today", "value": n}]
        if streak:
            fields.append({"label": "Streak", "value": f"{streak} day(s)"})
        if mood:
            fields.append({"label": "Mood", "value": mood})
        return {
            "say": say,
            "card": {"renderer": "entity-card", "title": "Journal",
                     "data": {"title": "Today", "fields": fields}},
            "link": {"text": "Open journal", "href": "/journal/"},
        }

    slot_today = _panels.slot_today

    slot_recent_thinking = _panels.slot_recent_thinking

    panel_yesterday = _panels.panel_yesterday

    panel_journal_today = _panels.panel_journal_today

    panel_month_compare = _panels.panel_month_compare

    async def _recent_days(self, n: int) -> list[dict]:
        # Walk back past empty / missing days so callers asking for "last
        # 3 days" get 3 days WITH entries, not 3 calendar days mostly empty.
        # Cap the walk so a fresh vault doesn't scan the dawn of time.
        results: list[dict] = []
        today = date.today()
        max_walk = max(n * 30, 60)
        for i in range(max_walk):
            if len(results) >= n:
                break
            d = today - timedelta(days=i)
            path = self._daily_path(d)
            try:
                content = await self.read(str(path))
            except Exception:
                continue
            entries = parse_entries(content)
            if not entries:
                continue
            dominant = max(
                set(e["mood"] for e in entries),
                key=lambda m: sum(1 for e in entries if e["mood"] == m),
            )
            # First substantive entry text as a one-line preview so the recent
            # list shows what the day was about (the UI's .rd-preview slot read
            # a `preview` key that was never populated).
            # `or ""` not `get(k, "")`: the default only applies to a MISSING
            # key, so a present-but-None value still reaches .strip(). Safe today
            # (parse_entries builds these in code) — cheap insurance if the
            # entry ever comes from frontmatter instead.
            preview = next(
                ((e.get("text") or "").strip() for e in entries if (e.get("text") or "").strip()),
                "",
            )
            results.append(
                {
                    "date": d.isoformat(),
                    "entries": len(entries),
                    "mood": dominant,
                    "emoji": MOOD_EMOJI.get(dominant, ""),
                    "preview": preview[:120],
                }
            )
        return results

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

    # ── Analytics (extracted to analytics.py) ──
    api_heatmap    = _analytics.api_heatmap
    api_mood_trend = _analytics.api_mood_trend
    api_streak     = _analytics.api_streak
    api_word_count = _analytics.api_word_count
    api_search     = _analytics.api_search
    api_export     = _analytics.api_export
    _streak_as_of  = _analytics._streak_as_of
    _nudge_enabled = _analytics._nudge_enabled
    scheduled_no_entry_nudge = _analytics.scheduled_no_entry_nudge
    _heatmap       = _analytics._heatmap
    _mood_trend    = _analytics._mood_trend

    # ── Milestones (extracted to milestones.py) ──
    api_milestone      = _milestones.api_milestone
    api_three_things   = _milestones.api_three_things
    api_draft_three_things = _milestones.api_draft_three_things
    three_things_filled = _milestones.three_things_filled
    api_milestones     = _milestones.api_milestones
    api_pin            = _milestones.api_pin
    api_pins           = _milestones.api_pins
    _load_pins         = _milestones._load_pins
    _save_pins         = _milestones._save_pins
    _log_activity      = _milestones._log_activity
    api_activity       = _milestones.api_activity
    voice_log_activity = _milestones.voice_log_activity

    # ── Reflection (extracted to reflection.py) ──
    api_dimensions                = _reflection.api_dimensions
    _today_dimension_signals      = _reflection._today_dimension_signals
    api_reflect                   = _reflection.api_reflect
    api_ai_reflect                = _reflection.api_ai_reflect
    _reflection_prompts           = _reflection._reflection_prompts
    api_wheel_review              = _reflection.api_wheel_review
    _wheel_review                 = _reflection._wheel_review
    scheduled_weekly_wheel_review = _reflection.scheduled_weekly_wheel_review

    # ── Related (extracted to related.py) ──
    api_related              = _related.api_related
    _collect_journal_entries = _related._collect_journal_entries
