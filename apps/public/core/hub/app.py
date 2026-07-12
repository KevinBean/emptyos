"""Hub — generic home dashboard.

Aggregates [[contributes.hub.panel]] from every installed app and renders them
in priority order. Zero hard app dependencies — works on a fresh clone with
only core apps, gracefully gains panels as more apps are installed.

The aggregator pattern (resolve_panels) is portable from the prior personal
dashboard but stripped of all life-domain logic (wellness, AI narrative, slot
framework). Personal richer variants live as separate apps that subscribe to
the same contribution slot.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.utils import parse_llm_json


LUCKY_SYSTEM = """You match a user's one-sentence goal to the right EmptyOS app from a catalog.

You are given:
- A user query in any language (English, 中文, or mixed).
- A catalog of available apps: id, name, description, dimensions, and optionally user_intent (phrases users actually say).

Pick the top 3 apps that best fit the query. Return STRICT JSON — no markdown, no prose, no leading dash:

{
  "matches": [
    {"id": "<app-id>", "score": 0.0-1.0, "reason": "<one short sentence>"}
  ]
}

Rules:
- Output exactly 0-3 matches. Empty list is fine when nothing fits.
- `score` is your confidence the app actually solves the query: 0.9+ = unambiguous, 0.7-0.9 = strong, 0.5-0.7 = plausible, <0.5 = weak.
- `reason` is ONE short sentence in the SAME language as the user query. No marketing prose.
- Do NOT invent apps. Only return ids that appear in the catalog.
- Do NOT translate descriptions — match the user's mental model.
- If the query is too vague to map to any app, return an empty matches list.
- Prefer one strong match over three weak ones; quality beats quantity.
"""


ROUTE_CHOICES = {
    "capture": "a short atom to save for later — a todo, idea, reminder, or note-to-self ('buy milk', 'call mum tomorrow')",
    "ask": "a question wanting an answer or explanation, now",
    "produce": "wants an artifact produced — a report, deck, summary, page, or plan built from their notes",
    "open": "wants to open or use a tool/app ('something to track spending', '记账')",
    "find": "wants to locate notes/files the user ALREADY has ('my notes on X', 'that article about Y')",
}

ROUTE_SYSTEM = """You route one line typed into a personal OS command bar to the right surface.
The user typed it fast; classify the INTENT SHAPE, not the topic.

Rules:
- 'my X' / 'that note about X' / past-tense references to existing material → find, not ask.
- Imperative self-directed actions ('buy milk', 'email Sarah back', 'fix the fence') → capture. The bar saves it; it does not do it.
- produce ONLY when they want a deliverable artifact (report, deck, page, summary). A question about a topic is ask even if the answer would be long.
- open ONLY when they want a tool, not when they name a task the tool would do.
- Works in any language (English, 中文, mixed).
- When genuinely torn, pick find — it is read-only and never wrong-destructive."""


# Smart-bar routing — tier-1 deterministic heuristics. Pure module-level fn so
# CLI / voice / unit tests can import it without booting the kernel.

_CAPTURE_PREFIXES = {
    "todo:": "task", "task:": "task", "idea:": "idea", "note:": "note",
}
_INTERROGATIVES = {
    "what", "why", "how", "when", "where", "who", "which",
    "can", "should", "is", "are", "does", "do",
}
_ZH_INTERROGATIVES = ("为什么", "怎么", "什么", "如何", "哪")
_FIND_PREFIXES = ("find ", "search ", "where is ", "where are ", "look up ", "my notes on ", "my notes about ")
_OPEN_PREFIXES = ("open ", "launch ", "go to ")
_ARTIFACT_NOUNS = {
    "report", "summary", "deck", "page", "post", "doc",
    "document", "presentation", "brief", "one-pager", "plan",
}
_ARTIFACT_NOUNS_2W = {"slide deck"}

# Leading imperative self-directed actions → capture (ROUTE_SYSTEM's first rule,
# now deterministic). Curated for PRECISION: only unambiguous "do this errand"
# verbs that aren't also question/find/produce shapes. Deliberately EXCLUDED:
# make/create/write/build (ambiguous with produce — see docstring), check/review
# (ambiguous with ask/find), sign (sign in/up = open). The bar SAVES the action;
# it never does it. Phrasal verbs (pick up / drop off / follow up / back up) only
# fire with their particle so "pick a colour" / "follow Sarah" stay ambiguous.
_IMPERATIVE_VERBS = {
    "buy", "call", "email", "text", "message", "msg", "send", "mail", "ship",
    "reply", "book", "order", "pay", "renew", "cancel", "schedule", "submit",
    "print", "fix", "repair", "install", "replace", "refill", "water", "charge",
    "wash", "clean", "feed",
}
_IMPERATIVE_PHRASALS = {"pick": "up", "drop": "off", "follow": "up", "back": "up"}


def classify_route_heuristic(text: str) -> tuple[str | None, str | None, str | None]:
    """(shape, rule, capture_tag) — shape None ⇒ defer to the LLM tier.

    Only genuinely high-precision rules live here; everything marginal goes to
    the LLM. Deliberately NOT rules: short-text length (ambiguous between
    capture/open/find) and generic produce verbs like make/create/write
    ('create a task to call mum' is a capture).

    A curated leading-imperative-verb set DOES route to capture deterministically
    ('buy milk', 'email Sarah back', 'call mum tomorrow'). This implements
    ROUTE_SYSTEM's first rule without depending on the LLM tier, which times out
    (~8s budget vs a slow claude-cli) and degrades every non-heuristic line to
    `find` — so plain tasks were dead-ending in an empty vault search."""
    t = (text or "").strip()
    low = t.lower()
    if not t:
        return None, None, None

    for prefix, tag in _CAPTURE_PREFIXES.items():
        if low.startswith(prefix) and len(t) > len(prefix):
            return "capture", "capture-prefix", tag
    if low.startswith("remind me") or t.startswith("提醒我"):
        return "capture", "capture-prefix", "reminder"

    # find-verb outranks the question-mark rule: "where is my passport?" wants
    # existing material, not an explanation.
    if low.startswith(_FIND_PREFIXES):
        return "find", "find-verb", None

    if t.endswith("?") or t.endswith("？"):
        return "ask", "question-mark", None

    first = low.split()[0] if low.split() else ""
    if first in _INTERROGATIVES:
        return "ask", "interrogative", None
    if t.startswith(_ZH_INTERROGATIVES):
        return "ask", "interrogative", None

    for prefix in _OPEN_PREFIXES:
        if low.startswith(prefix) and len(t) > len(prefix):
            return "open", "open-verb", None

    words = low.split()
    # Leading imperative errand → capture (needs an object, so ≥2 words; the
    # smart bar only fires on ≥2 words anyway). Phrasals require their particle.
    if len(words) >= 2:
        if words[0] in _IMPERATIVE_VERBS:
            return "capture", "imperative-verb", "task"
        if _IMPERATIVE_PHRASALS.get(words[0]) == words[1]:
            return "capture", "imperative-verb", "task"

    if len(words) >= 3 and words[0] in ("a", "an"):
        if words[1] in _ARTIFACT_NOUNS or " ".join(words[1:3]) in _ARTIFACT_NOUNS_2W:
            return "produce", "artifact-noun", None

    return None, None, None


# shape → (app that serves it, href template, fallback shape when that app is
# disabled). `find` is the floor — the search app ships in core.
_ROUTE_TARGETS = {
    "ask": ("assistant", "/assistant/?q={q}", "find"),
    "produce": ("work", "/work/?ask={q}", "ask"),
    "find": ("search", "/search/?q={q}", None),
    "capture": ("quick-action", None, "find"),
    "open": (None, None, "find"),
}


def resolve_route_target(shape: str, enabled: set[str]) -> tuple[str, str | None]:
    """Walk the degrade chain until the serving app is enabled.
    Returns (final_shape, href_template|None)."""
    seen = set()
    while shape not in seen:
        seen.add(shape)
        app_id, template, fallback = _ROUTE_TARGETS[shape]
        if app_id is None or app_id in enabled:
            return shape, template
        if fallback is None:
            return shape, template
        shape = fallback
    return "find", _ROUTE_TARGETS["find"][1]


COMPANION_SYSTEM = """You are Aura, the user's mind companion, writing the single "next move" on their home screen.

Voice: warm, direct, brief. Contractions are fine. You work WITH the user, never FOR them — you surface and suggest; you never imply you've done anything or will do it yourself.

You are given a compact digest of the user's current state: counts, short task/event titles, time of day, mood, journal streak, and `focus_areas` — a profile of what they're working on (active project & area names, most-used topics). You never see their actual notes — only these signals. Do not invent specifics you weren't given.

Pick exactly ONE next move worth their attention right now:
- Respect what's most time-sensitive (overdue, due today, an imminent event).
- If a category is overwhelmingly large (e.g. 100+ overdue), reframe it as a triage/cleanup — never imply they can clear it all now.
- When nothing is urgent, lean toward a neglected part of life (a walk, rest, reaching out to someone) over piling more onto work. But stay honest — if they've journaled and nothing's due, a calm "you're on top of things" is fine.
- When you do nudge forward progress, prefer something connected to their `focus_areas` (a real project/topic they care about) over a generic "do a task" — but only if the signals support it; never invent a project that isn't listed.

Return STRICT JSON only — no markdown, no prose outside it:
{"title": "<=6 words, imperative", "why": "one short HONEST clause — the real reason, rendered to the user as 'Suggested because: <why>'", "action_label": "<=3 words", "action_href": "<one route from the digest's routes list>"}

Rules:
- `action_href` MUST be one of the routes in the digest's "routes" field. Never invent a path.
- `why` is the honest reason from the signals — no flattery, no hype, no exclamation marks.
- Exactly one move. Quality over noise. If nothing notable, suggest a calm capture or a break.
"""


def _overdue_bucket(n: int) -> str:
    """Coarse bucket so the companion cache doesn't churn on every +/-1."""
    if n <= 0:
        return "0"
    if n <= 5:
        return "lo"
    if n <= 20:
        return "mid"
    return "hi"


class HubApp(BaseApp):
    # ── Panel aggregator ──

    async def resolve_panels(self, *, include_lazy: bool = False) -> list[dict]:
        """Gather every [[contributes.hub.panel]], call its method, return
        a list of {id, title, renderer, group, priority, source, data, lazy} items.

        Fail-soft per panel: a contributor that raises drops out of the list
        (logged to syslog) rather than breaking the whole page.

        Lazy panels are emitted as placeholders unless include_lazy=True.
        """
        contributions = self.kernel.apps.get_contributions("hub", "panel")
        if not contributions:
            return []

        def _placeholder(contrib: dict) -> dict:
            app_id = contrib.get("_app_id")
            method = contrib.get("method")
            return {
                "id": contrib.get("id") or f"{app_id}:{method}",
                "title": contrib.get("title") or "",
                "renderer": contrib.get("renderer") or "plain-list",
                "group": contrib.get("group") or "",
                "priority": int(contrib.get("priority", 100) or 100),
                "source": app_id,
                "data": None,
                "lazy": True,
            }

        async def _call_one(contrib: dict) -> dict | None:
            app_id = contrib.get("_app_id")
            method = contrib.get("method")
            if not app_id or not method:
                return None
            try:
                data = await self.call_app(app_id, method)
            except Exception as e:
                self.kernel.syslog.warn(
                    "hub",
                    f"panel '{contrib.get('id')}' ({app_id}.{method}) failed: {e}",
                )
                return None
            if data is None:
                return None
            cap = contrib.get("limit")
            if isinstance(data, list) and isinstance(cap, int) and cap > 0:
                data = data[:cap]
            return {
                "id": contrib.get("id") or f"{app_id}:{method}",
                "title": contrib.get("title") or "",
                "renderer": contrib.get("renderer") or "plain-list",
                "group": contrib.get("group") or "",
                "priority": int(contrib.get("priority", 100) or 100),
                "source": app_id,
                "data": data,
                "lazy": False,
            }

        eager: list[dict] = []
        lazy_placeholders: list[dict] = []
        eager_contribs: list[dict] = []
        for c in contributions:
            if c.get("lazy") and not include_lazy:
                lazy_placeholders.append(_placeholder(c))
            else:
                eager_contribs.append(c)

        results = await asyncio.gather(
            *[_call_one(c) for c in eager_contribs], return_exceptions=False
        )
        eager = [r for r in results if r is not None]

        panels = eager + lazy_placeholders
        panels.sort(key=lambda p: (p["priority"], p["id"]))
        return panels

    # ── HTTP API ──

    @web_route("GET", "/api/panels")
    async def api_panels(self, request):
        """All panels in layout order, grouped where applicable."""
        panels = await self.resolve_panels()
        blocks: list[dict] = []
        seen_groups: dict[str, dict] = {}
        for p in panels:
            g = p["group"]
            if g and g in seen_groups:
                # Defense-in-depth: a panel can only join a group if its
                # renderer matches. Mixed-renderer groups silently render the
                # wrong shape (e.g. stat-tile data flattened into chip text).
                if p["renderer"] != seen_groups[g]["renderer"]:
                    self.log(
                        f"hub: panel '{p['id']}' renderer '{p['renderer']}' "
                        f"does not match group '{g}' renderer "
                        f"'{seen_groups[g]['renderer']}' — dropping",
                        level="warn",
                    )
                    continue
                seen_groups[g]["items"].append(p)
                continue
            block = {
                "kind": "group" if g else "panel",
                "id": g or p["id"],
                "title": p["title"],
                "renderer": p["renderer"],
                "priority": p["priority"],
                "items": [p],
            }
            blocks.append(block)
            if g:
                seen_groups[g] = block
        await self.emit("hub:refreshed", {"blocks": len(blocks)})
        return {"blocks": blocks}

    @web_route("GET", "/api/panel/{panel_id}")
    async def api_panel(self, request):
        """Refresh a single panel by id. Forces lazy panels to execute."""
        panel_id = request.path_params.get("panel_id", "")
        panels = await self.resolve_panels(include_lazy=True)
        for p in panels:
            if p["id"] == panel_id:
                return p
        return {"error": "not found", "id": panel_id}

    @web_route("GET", "/api/panels/all")
    async def api_panels_all(self, request):
        """Like /api/panels but runs lazy contributors too. Used for debug."""
        panels = await self.resolve_panels(include_lazy=True)
        return {"panels": panels}

    # ── Daily command surface — deterministic state digest (Phase 1) ──
    #
    # Powers the home page's Now strip / Today lane / Next move. Pure
    # aggregation, no LLM — fail-soft per source. Phase 2 swaps the
    # template Next move for Aura synthesis (signals + short titles to
    # the model, never note bodies). See docs/HOME-COMPANION-REDESIGN.md.

    async def _safe_call(self, app_id: str, method: str):
        """call_app one source, swallowing + logging any failure.

        A missing or broken digest source drops that slice of the page
        rather than breaking the whole home surface.
        """
        try:
            return await self.call_app(app_id, method)
        except Exception as e:  # noqa: BLE001 — fail-soft is the design
            self.kernel.syslog.warn("hub", f"digest source {app_id}.{method} failed: {e}")
            return None

    async def _build_digest(self) -> dict:
        """Deterministic 'today' digest — no LLM, no next_move.

        Shared by /api/digest (which adds the fast template move) and
        /api/next-move (which adds the slow Aura move). Single-user daemon →
        system local time is the user's local time.
        """
        now = _dt.datetime.now()
        hour = now.hour
        if hour < 5:
            greeting = "Still up"
        elif hour < 12:
            greeting = "Good morning"
        elif hour < 17:
            greeting = "Good afternoon"
        elif hour < 22:
            greeting = "Good evening"
        else:
            greeting = "Winding down"

        # ── tasks ──
        overdue = due_today = 0
        pulse = await self._safe_call("task", "panel_pulse_stats")
        if isinstance(pulse, list):
            for s in pulse:
                if not isinstance(s, dict):
                    continue
                if s.get("label") == "Overdue":
                    overdue = int(s.get("value") or 0)
                elif s.get("label") == "Today":
                    due_today = int(s.get("value") or 0)
        task_titles: list[str] = []
        today_rows = await self._safe_call("task", "panel_todays_tasks")
        if isinstance(today_rows, list):
            for r in today_rows[:5]:
                if not isinstance(r, dict):
                    continue
                t = str(r.get("text") or r.get("title") or "").strip()
                if t:
                    task_titles.append(t)

        # ── journal ──
        journaled = False
        streak = 0
        mood = ""
        js = await self._safe_call("journal", "get_summary")
        if isinstance(js, dict):
            journaled = int(js.get("today_entries") or 0) > 0
            streak = int(js.get("streak") or 0)
            mood = str(js.get("mood") or "")

        # ── now (calendar agenda) ──
        now_items: list[dict] = []
        agenda = await self._safe_call("calendar", "panel_agenda")
        if isinstance(agenda, list):
            for it in agenda[:4]:
                if not isinstance(it, dict):
                    continue
                now_items.append(
                    {"time": str(it.get("tag") or ""), "title": str(it.get("text") or "")}
                )

        digest = {
            "greeting": greeting,
            "hour": hour,
            "now": now_items,
            "today": {
                "overdue": overdue,
                "due_today": due_today,
                "tasks": task_titles,
                "journaled": journaled,
                "streak": streak,
                "mood": mood,
            },
        }
        return digest

    @web_route("GET", "/api/digest")
    async def api_digest(self, request):
        """Deterministic digest + template Next move — fast, no LLM call.

        The Aura move hydrates separately via /api/next-move so the Now /
        Today lanes never block on a slow think() (claude-cli runs ~20s cold).
        """
        digest = await self._build_digest()
        move = self._template_next_move(digest)
        move["source"] = "template"
        digest["next_move"] = move
        digest["smart_route"] = bool(self.setting("hub.smart_route", True))
        return digest

    @web_route("GET", "/api/next-move")
    async def api_next_move(self, request):
        """Aura-synthesized Next move (may be slow; cached per cadence).

        Returns ``{"move": null}`` when the companion is disabled or the model
        is too weak — the frontend keeps the template move from /api/digest.
        """
        digest = await self._build_digest()
        move = await self._aura_next_move(digest)
        return {"move": move}

    async def companion_line(self) -> dict:
        """The companion's one-breath message for ambient surfaces (RPC verb).

        First consumer: the devices e-ink dashboard (`builtin:companion`
        section). Same brain as the home screen — deterministic digest +
        the cached Aura next-move, falling back to the template ladder when
        the companion is disabled or the model is too weak. Always returns
        a complete dict; never raises.
        """
        import asyncio as _asyncio

        digest = await self._build_digest()
        try:
            # Own time budget: a cold Aura synthesis runs 10-20s, but ambient
            # consumers (the e-ink panel poll) can't wait — serve the template
            # instantly and let the Aura cache warm from the hub page / a later
            # poll. shield() so the timeout doesn't cancel the in-flight think
            # (a cancelled call never fills the cache).
            task = _asyncio.ensure_future(self._aura_next_move(digest))
            move = await _asyncio.wait_for(_asyncio.shield(task), timeout=6.0)
        except _asyncio.TimeoutError:
            move = None   # task keeps running → cache fills for the next call
        except Exception:  # noqa: BLE001 — ambient surface, fail to template
            move = None
        if not move:
            move = self._template_next_move(digest)
            move["source"] = "template"
        return {
            "greeting": digest["greeting"],
            "title": move.get("title", ""),
            "why": move.get("why", ""),
            "action_href": move.get("action_href", ""),
            "action_label": move.get("action_label", ""),
            "tone": move.get("tone", "calm"),
            "source": move.get("source", ""),
        }

    def _template_next_move(self, digest: dict) -> dict:
        """Deterministic Next move — a priority ladder over the digest.

        Phase 1 stand-in for the Aura synthesis (Phase 2). The `why` line
        is the honest "Suggested because:" reasoning the UI renders inline.
        """
        t = digest["today"]
        hour = digest["hour"]
        now_items = digest["now"]
        overdue = t["overdue"]
        due_today = t["due_today"]
        journaled = t["journaled"]
        streak = t["streak"]

        if overdue > 20:
            # A huge overdue pile is a triage problem, not a "clear them now"
            # problem — framing it as cleanup is honest and less demoralising
            # than implying they're all live work.
            return {
                "title": "Your task list needs a triage",
                "why": f"{overdue} tasks are overdue — most are likely stale; a 10-min cleanup beats clearing them one by one",
                "action_href": "/task/",
                "action_label": "Triage tasks",
                "tone": "overdue",
            }
        if overdue > 0:
            plural = "s" if overdue != 1 else ""
            return {
                "title": "Clear what slipped",
                "why": f"{overdue} task{plural} slipped past due",
                "action_href": "/task/",
                "action_label": "Open tasks",
                "tone": "overdue",
            }
        if due_today > 0:
            why = f"{due_today} due today"
            if now_items:
                why += f" · next: {now_items[0]['title']}"
            return {
                "title": "Knock out today's tasks",
                "why": why,
                "action_href": "/task/",
                "action_label": "Open tasks",
                "tone": "today",
            }
        if hour >= 18 and not journaled:
            why = "no journal entry yet today"
            if streak:
                why += f" · {streak}-day streak going"
            return {
                "title": "Close out the day",
                "why": why,
                "action_href": "/journal/",
                "action_label": "Journal tonight",
                "tone": "calm",
            }
        if hour < 12 and not journaled:
            return {
                "title": "Plan the day",
                "why": "no journal entry yet — set an intention",
                "action_href": "/journal/",
                "action_label": "Open journal",
                "tone": "calm",
            }
        ahead = len(now_items)
        if ahead:
            plural = "s" if ahead != 1 else ""
            why = f"{ahead} thing{plural} on deck today"
        else:
            why = "nothing urgent — a good moment to capture or create"
        return {
            "title": "All clear",
            "why": why,
            "action_href": "/quick-action/",
            "action_label": "Capture a thought",
            "tone": "calm",
        }

    async def _aura_next_move(self, digest: dict) -> dict | None:
        """Aura-synthesized Next move (Phase 2). Returns None → use the template.

        Lazy + cached in memory per a coarse signal signature so it's not
        regenerated on every page load (cadence default 60 min). Ability-gated:
        a weak model garbles, so we fall back to the deterministic ladder.
        Privacy: the prompt carries signals + SHORT TITLES only — never note
        bodies (CLAUDE.md rule 19). `include_titles=false` drops titles too.
        """
        if not self.app_config("companion.enabled", True):
            return None
        try:
            if not await self.ability_meets("standard", "text"):
                return None
        except Exception:
            return None

        include_titles = bool(self.app_config("companion.include_titles", True))
        cadence_min = int(self.app_config("companion.cadence", 60) or 60)

        t = digest["today"]
        sig = "|".join(
            str(x)
            for x in [
                _overdue_bucket(t["overdue"]),
                t["due_today"],
                int(t["journaled"]),
                digest["hour"] // 3,
                len(digest["now"]),
                int(include_titles),
            ]
        )
        import time as _time

        cache = getattr(self, "_companion_cache", None)
        if (
            cache
            and cache.get("sig") == sig
            and (_time.time() - cache.get("ts", 0)) < cadence_min * 60
        ):
            return cache.get("move")

        routes = ["/task/", "/journal/", "/calendar/", "/quick-action/", "/people/", "/assistant/"]
        payload = {
            "time_of_day": digest["greeting"],
            "hour": digest["hour"],
            "overdue": t["overdue"],
            "due_today": t["due_today"],
            "journaled_today": t["journaled"],
            "journal_streak": t["streak"],
            "mood": t["mood"],
            "next_events": [it["title"] for it in digest["now"]] if include_titles else len(digest["now"]),
            "due_titles": t["tasks"] if include_titles else len(t["tasks"]),
            "routes": routes,
        }
        # Ground the suggestion in what the user actually works on — metadata
        # only (project/area names + top topics), same privacy class as titles,
        # so gate it behind the same include_titles switch. Computed here (after
        # the cache check) so it costs nothing on a cache hit.
        if include_titles:
            try:
                prof = self.vault_interest_profile(max_tags=12)
                focus = {
                    "projects": prof.get("projects", [])[:8],
                    "areas": prof.get("areas", []),
                    "topics": [tag for tag, _ in prof.get("tags", [])],
                }
                if any(focus.values()):
                    payload["focus_areas"] = focus
            except Exception as e:  # noqa: BLE001 — best-effort, never block the move
                self.kernel.syslog.warn("hub", f"focus profile failed: {e}")
        import json as _json

        try:
            raw = await self.think(
                "Digest:\n" + _json.dumps(payload, ensure_ascii=False),
                system=COMPANION_SYSTEM,
                domain="text",
                temperature=0.5,
                min_ability="standard",
            )
        except Exception as e:  # noqa: BLE001 — fail-soft to the template
            self.kernel.syslog.warn("hub", f"companion think failed: {e}")
            return None

        # fallback={} → parse_llm_json returns {} instead of RAISING on an
        # unparseable reply, so a garbage model response falls back to the
        # template (move=None) rather than 500-ing /api/next-move.
        parsed = parse_llm_json(raw, fallback={}) or {}
        if not isinstance(parsed, dict) or not str(parsed.get("title") or "").strip():
            return None
        href = str(parsed.get("action_href") or "").strip()
        if href not in routes:
            href = "/quick-action/"
        if t["overdue"] > 0:
            tone = "overdue"
        elif t["due_today"] > 0:
            tone = "today"
        else:
            tone = "calm"
        move = {
            "title": str(parsed.get("title") or "").strip()[:80],
            "why": str(parsed.get("why") or "").strip()[:200],
            "action_href": href,
            "action_label": str(parsed.get("action_label") or "Open").strip()[:24],
            "tone": tone,
            "source": "aura",
            "provenance": self.last_provenance(),
        }
        self._companion_cache = {"sig": sig, "ts": _time.time(), "move": move}
        return move

    # ── Feeling Lucky — one-sentence app router ──

    def _collect_app_catalog(self) -> list[dict]:
        """Compact catalog of enabled apps for the LLM matcher.

        Reads `[app]` from every enabled manifest, keeping only the fields
        the recommender needs (id, name, description, dimensions, user_intent).
        Skips apps that have opted out via `[app] lucky_skip = true` — same
        carve-out as tour-step skip: aggregators / chrome / single-shot tools
        the user never names directly.
        """
        catalog: list[dict] = []
        manifests = self.kernel.apps.enabled_manifests()
        for aid, m in manifests.items():
            app_block = (m.raw.get("app") or {})
            if app_block.get("lucky_skip"):
                continue
            entry = {
                "id": aid,
                "name": app_block.get("name") or aid,
                "description": app_block.get("description") or "",
                "dimensions": app_block.get("dimensions") or [],
            }
            ui = app_block.get("user_intent") or []
            if ui:
                entry["user_intent"] = ui
            catalog.append(entry)
        return catalog

    async def _lucky_match(self, query: str) -> list[dict]:
        """LLM-match a one-sentence goal against the enabled-app catalog.
        Returns ≤3 clean matches `{id, name, score, reason}`, hallucination-
        filtered to catalog ids. Shared by api_lucky and the smart-bar's
        `open` route branch. Raises on think failure — callers decide."""
        catalog = self._collect_app_catalog()
        if not catalog:
            return []

        import json as _json
        prompt = (
            f"User query:\n{query}\n\n"
            f"Catalog ({len(catalog)} apps):\n"
            f"{_json.dumps(catalog, ensure_ascii=False)}"
        )
        raw = await self.think(
            prompt,
            system=LUCKY_SYSTEM,
            domain="text",
            temperature=0.2,
        )

        parsed = parse_llm_json(raw) or {}
        matches = parsed.get("matches") if isinstance(parsed, dict) else None
        if not isinstance(matches, list):
            matches = []

        # Filter to ids that actually exist in the catalog (LLMs occasionally
        # hallucinate ids despite the rule).
        known_ids = {e["id"] for e in catalog}
        clean: list[dict] = []
        for m in matches:
            if not isinstance(m, dict):
                continue
            aid = str(m.get("id") or "").strip()
            if aid not in known_ids:
                continue
            try:
                score = float(m.get("score") or 0)
            except (TypeError, ValueError):
                score = 0.0
            score = max(0.0, min(1.0, score))
            clean.append({
                "id": aid,
                "name": next((e["name"] for e in catalog if e["id"] == aid), aid),
                "score": score,
                "reason": str(m.get("reason") or "").strip(),
            })

        clean.sort(key=lambda x: x["score"], reverse=True)
        return clean[:3]

    @web_route("POST", "/api/lucky")
    async def api_lucky(self, request):
        """Match a one-sentence user goal to the right app.

        Reads the user's query, builds a compact catalog from every enabled
        app's manifest, asks the LLM to pick top-3 matches with score + reason,
        returns ranked results. The frontend renders cards with [Open] and —
        when all matches score <0.4 — a "build a new app for this" route to
        apps/app-builder/. See `.claude/rules/user-intent.md` for the data
        layer and graduation criteria.
        """
        body = await self.safe_json(request)
        query = (body.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query required"}
        if len(query) > 500:
            return {"ok": False, "error": "query too long (>500 chars)"}
        try:
            matches = await self._lucky_match(query)
        except Exception as e:
            return {"ok": False, "error": f"think failed: {e}"}
        return {
            "ok": True,
            "query": query,
            "catalog_size": len(self._collect_app_catalog()),
            "matches": matches,
        }

    # ── Smart bar — one bar, routed by intent shape ──

    def _app_prefix(self, app_id: str) -> str:
        m = self.kernel.apps.enabled_manifests().get(app_id)
        if not m:
            return ""
        prefix = ((m.raw.get("provides") or {}).get("web") or {}).get("prefix", "")
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        return prefix

    def _route_alternatives(self, text: str, chosen: str, enabled: set[str]) -> list[dict]:
        """The other shapes as ready-to-use actions — misroute recovery
        without a second classification round trip."""
        from urllib.parse import quote
        q = quote(text, safe="")
        out: list[dict] = []
        for shape, label in (("ask", "Ask"), ("find", "Find"), ("produce", "Produce")):
            if shape == chosen:
                continue
            final, template = resolve_route_target(shape, enabled)
            if final != shape or not template:
                continue  # shape unavailable on this deployment — don't offer it
            out.append({"shape": shape, "label": label, "href": template.format(q=q)})
        if chosen != "capture" and "quick-action" in enabled:
            out.append({
                "shape": "capture", "label": "Capture",
                "post": {"url": "/hub/api/route", "body": {"text": text, "force": "capture"}},
            })
        return out

    async def _route_capture(self, text: str, tag: str | None) -> dict:
        """Execute the capture server-side (reversible-internal class — the
        safety net is the undo handle, not a pre-approval form)."""
        if tag:
            # Prefix capture: strip the "todo:"-style prefix, skip the LLM tagger.
            low = text.lower()
            for prefix in _CAPTURE_PREFIXES:
                if low.startswith(prefix):
                    text = text[len(prefix):].strip()
                    break
            entry = await self.call_app("quick-action", "add", text=text, tag=tag)
        else:
            entry = await self.call_app("quick-action", "smart_add", text=text)
        if not isinstance(entry, dict) or entry.get("error"):
            raise RuntimeError(str((entry or {}).get("error") or "capture failed"))
        action = {
            "kind": "captured",
            "entry": {k: entry.get(k) for k in ("text", "tag", "timestamp")},
        }
        if entry.get("routed_to"):
            # Tag-routed past the inbox (e.g. tag `task` → task app) — dismiss
            # can't reach it there, so report the destination instead of a
            # broken Undo.
            action["routed_to"] = entry.get("project_name") or entry["routed_to"]
        else:
            action["undo"] = {
                "url": "/quick-action/api/dismiss",
                "body": {"timestamp": entry.get("timestamp", ""), "text": entry.get("text", "")},
            }
        return action

    @web_route("POST", "/api/route")
    async def api_route(self, request):
        """Classify one line of free text by INTENT SHAPE and return the action.

        Tier 1: deterministic heuristics (classify_route_heuristic). Tier 2:
        select() over ROUTE_CHOICES; `open` additionally runs the lucky matcher.
        Failure policy: degrade to `find` — never capture on uncertainty
        (capture mutates state; search doesn't). One round trip carries the
        action, an executed capture's undo handle, and the alternatives.
        """
        from urllib.parse import quote

        body = await self.safe_json(request)
        text = (body.get("text") or "").strip()
        force = (body.get("force") or "").strip() or None
        if not text:
            return {"ok": False, "error": "text required"}
        if len(text) > 500:
            return {"ok": False, "error": "text too long (>500 chars)"}
        if force and force not in _ROUTE_TARGETS:
            return {"ok": False, "error": f"unknown shape '{force}'"}

        enabled = self.kernel.apps.enabled_ids()
        shape, source, rule, tag = None, "", None, None
        if force:
            shape, source = force, "forced"
            if force == "capture":
                # Reuse the prefix detection so "todo: x" forced via the chip
                # still gets the deterministic tag; don't let an unrelated
                # heuristic rule leak into the response telemetry.
                _, h_rule, h_tag = classify_route_heuristic(text)
                if h_rule == "capture-prefix":
                    rule, tag = h_rule, h_tag
        else:
            shape, rule, tag = classify_route_heuristic(text)
            if shape:
                source = "heuristic"

        matches: list[dict] = []
        if shape is None:
            try:
                shape = await asyncio.wait_for(
                    self.select(text, ROUTE_CHOICES, system=ROUTE_SYSTEM,
                                default="find", temperature=0.1),
                    timeout=8.0,
                )
                source = "llm"
            except Exception:
                shape, source = "find", "degraded"

        if shape == "open" and source != "degraded":
            try:
                matches = await asyncio.wait_for(self._lucky_match(text), timeout=10.0)
            except Exception:
                matches = []
            if not matches:
                shape, source = "find", "degraded"

        final_shape, template = resolve_route_target(shape, enabled)
        if final_shape != shape:
            shape, source = final_shape, "degraded"

        confidence = {"forced": 1.0, "heuristic": 0.9, "llm": 0.6, "degraded": 0.3}.get(source, 0.5)

        action: dict
        if shape == "capture":
            try:
                action = await self._route_capture(text, tag)
            except Exception as e:
                self.log(f"route capture failed: {e}", "warning")
                shape, source, confidence = "find", "degraded", 0.3
                action = {"kind": "navigate", "href": f"/search/?q={quote(text, safe='')}"}
        elif shape == "open":
            for m in matches:
                m["href"] = self._app_prefix(m["id"]) or "/"
            best = matches[0]
            if best["score"] >= 0.7 and (len(matches) == 1 or best["score"] - matches[1]["score"] >= 0.25):
                action = {"kind": "navigate", "href": best["href"]}
            else:
                action = {"kind": "choose", "matches": matches}
        else:
            action = {"kind": "navigate", "href": (template or "/search/?q={q}").format(q=quote(text, safe=""))}

        asyncio.create_task(self.emit("hub:routed", {"shape": shape, "source": source, "rule": rule or ""}))
        return {
            "ok": True,
            "shape": shape,
            "source": source,
            "rule": rule,
            "confidence": confidence,
            "action": action,
            "alternatives": self._route_alternatives(text, shape, enabled),
        }

    @web_route("GET", "/debug/panels")
    async def debug_panels(self, request):
        from fastapi.responses import HTMLResponse

        debug_file = Path(self.manifest.path) / "pages" / "debug.html"
        if not debug_file.exists():
            return HTMLResponse(
                "<h1>debug.html missing</h1><p>See /hub/api/panels/all for raw data.</p>",
                status_code=404,
            )
        return HTMLResponse(debug_file.read_text(encoding="utf-8"))

    # ── Hub's own panels ──

    async def panel_welcome(self) -> dict | None:
        """Welcome card — only shown when there are no other contributing apps.

        We can't know that at panel-method time, so we always return it; the
        chips launcher below renders the actual app list. The welcome card
        becomes self-evidently extra context once other panels are present.
        """
        if not self.app_config("show_welcome", True):
            return None
        return {
            "label": "EmptyOS",
            "text": "A mind companion. Capture a thought, write a note, or pick an app below.",
            "url": "/quick-action/",
            "button_label": "Capture",
        }

    async def panel_launcher(self) -> list[dict] | None:
        """Grid launcher — loaded apps grouped into declared store_category sections.

        Hub itself and any app with no web prefix are skipped. Returns one entry
        per non-empty section ({key, label, icon, count, apps:[...]}) ordered by
        SECTION_META; apps within a section are alphabetical. The `app-grid`
        renderer (hub.js) draws each section as a collapsible block. See
        `emptyos/sdk/app_sections.py` + `.claude/rules/store.md`.
        """
        from emptyos.sdk.app_sections import category_of, sections_from_buckets

        buckets: dict[str, list[dict]] = {}
        for app_id, app in self.kernel.apps.instances.items():
            if app_id == "hub":
                continue
            prefix = (app.manifest.provides.get("web") or {}).get("prefix", "")
            if not prefix:
                continue
            href = prefix + "/" if not prefix.endswith("/") else prefix
            buckets.setdefault(category_of(app.manifest), []).append(
                {
                    "id": app_id,
                    "title": app.manifest.name or app_id,
                    "href": href,
                    "description": (app.manifest.description or "").strip(),
                }
            )

        sections = sections_from_buckets(
            buckets, app_sort_key=lambda c: (c["title"] or c["id"]).lower()
        )
        return sections or None

    async def panel_memory_fidelity(self) -> dict | None:
        """Memory fidelity dial — % of machine-touched memory reading `trusted`.

        Attested Memory's read-only loop-closer (`docs/MEMORY.md` §8): a glance
        at how much of the system's own memory is trusted+fresh vs
        stale/unconfirmed. Backed by ``BaseApp.memory_audit()`` (derived, never
        writes). Dark by default — returns None when the feature is off, and
        also when there's no machine-touched memory yet (fresh install) so the
        panel stays invisible until it has signal.
        """
        if not self.app_config("feature.memory-fidelity.enabled", False):
            return None
        try:
            report = self.memory_audit()
        except Exception:  # noqa: BLE001 — panels fail soft (hub-panels rule)
            return None
        total = int(report.get("total", 0) or 0)
        if not total:
            return None
        dial = int(report.get("dial", 0) or 0)
        need = len(report.get("stale", []) or [])
        detail = f"{dial}% trusted · {total} claims"
        if need:
            detail += f" · {need} to review"
        return {"name": "Memory fidelity", "pct": dial, "detail": detail}

    async def panel_tailnet(self) -> dict | None:
        """Reachability tile — this node's MagicDNS name when the tailnet is up.

        Glanceable "you can reach EmptyOS at X from your other devices". Reaches
        the read-only ``tailscale`` *service* (not an app) via get_optional, so
        it's fail-soft: returns None (panel drops silently) when the plugin is
        absent, the CLI is missing, or the backend isn't Running — never a dead
        card. See ``.claude/rules/hub-panels.md``.
        """
        ts = self.kernel.services.get_optional("tailscale")
        if not ts:
            return None
        try:
            if not await ts.available():
                return None
            s = await ts.status()
        except Exception:  # noqa: BLE001 — panels fail soft (hub-panels rule)
            return None
        name = (s.get("self_dns") or s.get("self_ip") or "").strip()
        if not name:
            return None
        sub = s.get("self_ip", "") if s.get("self_dns") else ""
        return {"label": "Reachable at", "value": name, "sub": sub, "href": "/system"}

    # ── CLI ──

    @cli_command("hub")
    def cmd_hub(self):
        """Print panel summary."""
        import asyncio

        panels = asyncio.run(self.resolve_panels())
        if not panels:
            print("No panels contributed yet. Install more apps to populate the dashboard.")
            return
        print(f"{len(panels)} panels:")
        for p in panels:
            print(f"  [{p['priority']:>4}] {p['source']:>20}  {p['id']}  ({p['renderer']})")
