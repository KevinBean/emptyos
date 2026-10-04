"""Assistant — /research WebSocket streaming + persistence.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: bridging ``research.run_research`` async events to the
assistant WebSocket frame shape, persisting the final report into the
session, and bailing out cleanly when the WS dies mid-run so an orphaned
tab doesn't burn the full source + synthesis budget.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._sessions_lock`` / ``self._add_message``
(sessions.py mixin).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .research import (
    DEFAULT_PER_PAGE_CHARS as RESEARCH_DEFAULT_PER_PAGE_CHARS,
)
from .research import (
    DEFAULT_TOP_N as RESEARCH_DEFAULT_TOP_N,
)
from .research import run_research

if TYPE_CHECKING:
    from .app import AssistantApp  # noqa: F401 — for type hints only


# ─── Bind to AssistantApp class as ──────────────────────────────────
#   _run_research_ws = _research_ws._run_research_ws
# Module-level pure helper (no `self`):
#   _research_status_label  (used internally; not bound — call as the
#   module-level function from chat_ws via import if needed)
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _research_status_label(ev: dict) -> str:
    stage = ev.get("stage", "")
    if stage == "searching":
        return "Searching the web…"
    if stage == "found":
        return f"Found {ev.get('n', 0)} sources, reading them…"
    if stage == "reading":
        i, n = ev.get("i", 0), ev.get("n", 0)
        title = ev.get("title") or ev.get("url") or ""
        return f"Reading {i}/{n}: {title[:70]}"
    if stage == "read-failed":
        return f"Skipped: {ev.get('error', 'unreachable')}"
    if stage == "synthesizing":
        return "Synthesizing the report…"
    return stage or "Working…"


async def _run_research_ws(self, websocket, session_id: str, query: str) -> None:
    """Stream research events over the assistant WS and persist the report.

    Detects WS-closed via send failures and tells ``run_research`` to bail
    via the ``is_cancelled`` hook — keeps an orphaned tab from burning the
    full 5-source + synthesis budget after the user has navigated away.
    """
    top_n = int(self.app_config("research_top_n", RESEARCH_DEFAULT_TOP_N))
    per_page = int(
        self.app_config("research_per_page_chars", RESEARCH_DEFAULT_PER_PAGE_CHARS)
    )

    ws_dead = False

    async def safe_send(payload: dict) -> None:
        nonlocal ws_dead
        if ws_dead:
            return
        try:
            await websocket.send_json(payload)
        except Exception:
            ws_dead = True

    await safe_send({"type": "agent-thinking", "agent": "research"})
    full_text = ""
    sources: list[dict] = []
    had_error = False
    try:
        async for ev in run_research(
            self,
            query,
            top_n=top_n,
            per_page_chars=per_page,
            is_cancelled=lambda: ws_dead,
        ):
            if ws_dead:
                break
            ev_type = ev.get("type")
            if ev_type == "research-text":
                full_text += ev.get("text", "")
                await safe_send(
                    {"type": "agent-stream", "agent": "research", "text": full_text}
                )
            elif ev_type == "research-status":
                await safe_send(
                    {
                        "type": "agent-status",
                        "agent": "research",
                        "status": _research_status_label(ev),
                        "research_stage": ev.get("stage"),
                        "research_meta": ev,
                    }
                )
            elif ev_type == "research-citations":
                sources = ev.get("sources", []) or []
                await safe_send(
                    {"type": "research-citations", "sources": sources}
                )
            elif ev_type == "research-error":
                had_error = True
                await safe_send(
                    {
                        "type": "agent-reply",
                        "agent": "system",
                        "text": "⚠ " + ev.get("message", "research failed"),
                    }
                )
    except Exception as e:
        had_error = True
        await safe_send(
            {
                "type": "agent-reply",
                "agent": "system",
                "text": f"⚠ Research failed: {type(e).__name__}: {e}",
            }
        )

    # Persist even when the WS is dead — the user can reload and find the
    # report in their session history.
    if full_text and not ws_dead:
        persisted = full_text
        if sources:
            persisted += "\n\n**Sources:**\n" + "\n".join(
                f"[{s['n']}] [{s.get('title') or s['url']}]({s['url']})" for s in sources
            )
        try:
            async with self._sessions_lock:
                self._add_message(session_id, "assistant", persisted, agent="research")
        except Exception:
            pass
        await safe_send(
            {"type": "agent-reply", "agent": "research", "text": full_text}
        )
    await safe_send({"type": "agent-done"})
    if not had_error and not ws_dead:
        await self.emit("assistant:message", {"session": session_id, "provider": "research"})
