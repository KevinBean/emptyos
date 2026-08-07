"""journal — AI reflection, 8-dimension signal aggregation, and the wellbeing-wheel review.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Everything LLM-backed plus the dimension/wheel surfaces — reflect, ai-reflect, reflection-prompts, dimensions signals, wheel review, and the scheduled weekly wheel-review write. Source of truth for the reflection + wheel endpoints.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._daily_path/_recent_days (spine), self.call_app('healing') for habit signals, self.kernel.vault_map.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from emptyos.runtime import wheel as _wheel
from emptyos.sdk import dimensions, parse_captures, parse_llm_json, scheduled, web_route

from .parser import parse_entries
from .prompts import PROMPTS

if TYPE_CHECKING:
    from .app import JournalApp  # noqa: F401 — for type hints only


# ─── Bind to JournalApp class as ────────────────────────────────
#   api_dimensions                 = _reflection.api_dimensions
#   _today_dimension_signals       = _reflection._today_dimension_signals
#   api_reflect                    = _reflection.api_reflect
#   api_ai_reflect                 = _reflection.api_ai_reflect
#   api_wheel_review               = _reflection.api_wheel_review
#   _wheel_review                  = _reflection._wheel_review
#   scheduled_weekly_wheel_review  = _reflection.scheduled_weekly_wheel_review
#   _reflection_prompts            = _reflection._reflection_prompts
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


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
    signals = {
        dim: {"captures": 0, "habits_done": 0, "habits_total": 0, "journal": 0}
        for dim in dimensions.DIMENSIONS
    }
    today_iso = d.isoformat()

    try:
        cap_rel = self.kernel.vault_map.get(
            "quick-action", "inbox", "00_Inbox/_captures.md"
        )  # owning app is quick-action (formerly capture)
        cap_path = (self.vault_root / cap_rel) if cap_rel else None
        if cap_path and cap_path.exists():
            content = await self.read(str(cap_path))
            for c in parse_captures(content, limit=500):
                if not c["timestamp"].startswith(today_iso):
                    continue
                dim = (
                    dimensions.resolve(c["tag"])
                    or (dimensions.extract(c["text"])[:1] or [""])[0]
                )
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

    for s in signals.values():
        s["total"] = s["captures"] + s["habits_done"] + s["journal"]
    return signals


@web_route("GET", "/api/reflect")
async def api_reflect(self, request):
    days = int(request.query_params.get("days", "7"))
    return {"prompts": await self._reflection_prompts(days)}


@web_route("POST", "/api/ai-reflect")
async def api_ai_reflect(self, request):
    """LLM generates a reflection from recent journal entries."""
    data = (
        await self.read_json(request)
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
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
                lines = [
                    f"{d.isoformat()} {e['time']} {e['emoji']} {e['text']}" for e in entries
                ]
                all_text.extend(lines)
        except Exception:
            continue

    if not all_text:
        return {
            "reflection": f"No journal entries found in the last {days} days. Start writing to get reflections!"
        }

    reflection = await self.think_safe(
        f"Journal entries from the last {days} days:\n\n" + "\n".join(all_text),
        system=PROMPTS.reflect_system,
        domain="text",
        temperature=0.6,
        fallback="AI is offline — your entries are below unchanged. Reflection will return when AI is available.",
    )
    await self.emit("journal:reflection", {"days": days, "entry_count": len(all_text)})
    return {
        "reflection": reflection,
        "days": days,
        "entries_analyzed": len(all_text),
        "provenance": self.last_provenance(),
    }


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
        f"{dimensions.LABELS[d]}: {signals[d]} (mean={mean:.1f})" for d in dimensions.DIMENSIONS
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
            user_msg,
            system=PROMPTS.wheel_review_system,
            domain="text",
            temperature=0.4,
        )
    except RuntimeError as e:
        if "No available provider for capability" in str(e):
            raise
        narrative = f"Could not generate review: {e}"
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
    if (
        not narrative
        or narrative.startswith("No behavioral signal")
        or narrative.startswith("Could not generate")
    ):
        return
    today = date.today()
    raw = self.vault_config("weekly", "50_Journal/{year}/{year}-W{week}.md")
    iso_cal = today.isocalendar()
    wp = self.vault_root / raw.replace("{year}", str(iso_cal[0])).replace(
        "{week}", f"{iso_cal[1]:02d}"
    )
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
    await self._write_note(wp, new_content)
    await self.emit("journal:wheel-review", {"period": "week", "week": iso_cal[1]})


async def _reflection_prompts(self, days: int) -> list[str]:
    recent = await self._recent_days(days)
    if not recent:
        return ["Start journaling today — write your first entry."]
    summary = ", ".join(
        f"{r['date']}: {r['entries']} entries ({r['mood']})" for r in recent[:7]
    )
    response = await self.think(
        f"Recent journal activity:\n{summary}",
        system=PROMPTS.prompt_gen_system,
        temperature=0.8,
    )
    return parse_llm_json(
        response,
        fallback=[
            "What made today different from yesterday?",
            "What's one thing you'd do differently this week?",
            "What are you most grateful for right now?",
        ],
    )
