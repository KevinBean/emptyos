"""replay — distill a recorded session/trace into a recipe (+ optional SKILL.md).

The think() call lives here; the prompt contract + pure validator are in
``emptyos/sdk/recipe_distill.py``. Both reusable artifacts land through the
existing review gate (diff-shaped, never autopilot): the recipe note via
``rooms.write_note``, the SKILL.md via ``repo.write`` (guarded — repo is an
extension app, absent on a fresh public clone).

Bound to ReplayApp as:
    distill_from_session = _distill.distill_from_session
    distill_from_trace   = _distill.distill_from_trace
    _think_distill       = _distill._think_distill
    _finish_distill      = _distill._finish_distill
    _propose_recipe      = _distill._propose_recipe
    _propose_skill       = _distill._propose_skill
    _known_verbs         = _distill._known_verbs
    _repo_available      = _distill._repo_available
Adding a method here? Add a matching binding line in app.py.

Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

from emptyos.sdk.recipe_distill import RECIPE_DISTILL_SYSTEM, parse_recipe_draft
from emptyos.sdk.trace_reader import work_trace

from .shared import RECIPE_DIR, SKILL_DIR, render_recipe_note, render_skill_md, slugify

if TYPE_CHECKING:  # noqa: F401 — type hints only
    from .app import ReplayApp


def _known_verbs(self) -> set[str]:
    """The live verb-registry set, to pin distilled verb steps against."""
    try:
        return {e.get("verb") for e in self.kernel.apps.get_verbs().menu() if e.get("verb")}
    except Exception:
        return set()


def _repo_available(self) -> bool:
    try:
        return "repo" in self.kernel.apps.enabled_ids()
    except Exception:
        return False


async def _think_distill(self, intent: str, work: list[dict]):
    """Run the distill think() call → (draft | None, warnings). Never raises."""
    parts: list[str] = []
    if intent:
        parts.append("SESSION (what the user did and asked):\n" + intent[:8000])
    if work:
        lines = []
        for w in work[:80]:
            if w.get("kind") == "tool":
                lines.append(
                    f"{w['seq']}. tool {w.get('name')} {json.dumps(w.get('input') or {})[:120]}"
                )
            else:
                lines.append(f"{w['seq']}. think ({w.get('name')})")
        parts.append("RECORDED ACTIVITY (enrichment — internal tools, not verbs):\n" + "\n".join(lines))
    if not parts:
        return None, ["nothing to distill (no session text or recorded activity)"]
    parts.append("Distill this into a reusable recipe per the schema.")
    try:
        raw = await self.think(
            "\n\n".join(parts),
            system=RECIPE_DISTILL_SYSTEM,
            domain="text",
            temperature=0.3,
            max_tokens=3000,
        )
    except Exception as e:
        return None, [f"think failed: {e}"]
    return parse_recipe_draft(raw, known_verbs=self._known_verbs())


async def _propose_recipe(self, draft: dict, source_session: str, source_trace: str) -> dict:
    note = render_recipe_note(
        draft,
        source_session=source_session,
        source_trace=source_trace,
        created=date.today().isoformat(),
    )
    rel = f"{RECIPE_DIR}/{slugify(draft.get('name') or 'recipe')}.md"
    return await self.propose_action(
        app="rooms",
        method="write_note",
        args={"path": rel, "content": note},
        source_actor={"type": "app", "id": "replay"},
    )


async def _propose_skill(self, draft: dict, source_session: str):
    """Propose a .claude/skills SKILL.md draft via repo.write. Returns the
    pending action, or None when the repo app isn't installed."""
    if not self._repo_available():
        return None
    md = render_skill_md(draft, source_session=source_session)
    rel = f"{SKILL_DIR}/eos-{slugify(draft.get('name') or 'recipe')}/SKILL.md"
    return await self.propose_action(
        app="repo",
        method="write",
        args={"path": rel, "content": md},
        source_actor={"type": "app", "id": "replay"},
    )


async def _finish_distill(self, draft, warnings, *, target, source_session, source_trace) -> dict:
    out: dict = {"ok": True, "name": draft.get("name"), "warnings": warnings, "draft": draft}
    if target in ("recipe", "both"):
        rec = await self._propose_recipe(draft, source_session, source_trace)
        out["recipe_action_id"] = (rec or {}).get("id")
    if target in ("skill", "both"):
        sk = await self._propose_skill(draft, source_session)
        out["skill_action_id"] = (sk or {}).get("id") if sk else None
        if sk is None:
            out["skill_error"] = "skill target needs the repo app"
    await self.emit(
        "replay:distilled",
        {"name": draft.get("name"), "target": target, "source_session": source_session},
    )
    return out


async def distill_from_session(self, sid: str, *, target: str = "recipe") -> dict:
    """Distill an agent session (its conversation = intent, plus any trace
    enrichment) into a recipe / SKILL.md proposal."""
    if not self._enabled():
        return {"ok": False, "error": "replay disabled"}
    try:
        sess = await self.call_app("agent", "export_session_trace", sid=sid)
    except Exception as e:
        return {"ok": False, "error": f"could not read session: {e}"}
    if not sess or sess.get("error"):
        return {"ok": False, "error": (sess or {}).get("error") or "session not found"}
    trace_id = sess.get("trace_id") or ""
    work = work_trace(self.kernel.config.data_dir, trace_id) if trace_id else []
    draft, warnings = await self._think_distill(sess.get("intent") or "", work)
    if draft is None:
        return {"ok": False, "error": "distill produced no recipe", "warnings": warnings}
    return await self._finish_distill(
        draft, warnings, target=target, source_session=sid, source_trace=trace_id
    )


async def distill_from_trace(self, trace_id: str, *, target: str = "recipe") -> dict:
    """Best-effort distill from a raw trace_id (no session conversation)."""
    if not self._enabled():
        return {"ok": False, "error": "replay disabled"}
    work = work_trace(self.kernel.config.data_dir, trace_id)
    if not work:
        return {"ok": False, "error": "no recorded activity for that trace"}
    draft, warnings = await self._think_distill("", work)
    if draft is None:
        return {"ok": False, "error": "distill produced no recipe", "warnings": warnings}
    return await self._finish_distill(
        draft, warnings, target=target, source_session="", source_trace=trace_id
    )
