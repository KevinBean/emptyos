"""Orient-before-Act — pre-turn classification + investigation plan.

Extracted from app.py to keep the core agent atomic. Both entrypoints
(`orient` / `orient_block`) are bound onto AgentApp as `_orient` /
`_orient_block` so existing call-sites (app.py ws_turn and repl.cmd_chat)
continue to work through `self._orient(...)`.
"""

from __future__ import annotations

import re

from apps.agent.prompts import (
    CLASSIFY_PROMPT, CLASSIFY_SYSTEM,
    ORIENT_PROMPT, ORIENT_SYSTEM,
)


async def orient(self, user_text: str, session_id: str) -> dict | None:
    """Orient-before-Act: sequential 4-stage pipeline.

    Stage 1 — Classify: understand the question (task_type, subject, scope).
    Stage 2 — Contextualize: load rules and past archives informed by classification.
    Stage 3 — Plan: produce type-aware investigation steps.
    Stage 4 — Act: caller uses the returned plan (not our responsibility).

    Returns a dict with keys:
        task_type, subject, scope,
        relevant_rules, investigation_plan, success_criteria, risk_flags
    or None on failure.

    Both think() calls are cached independently by content hash.
    """
    from emptyos.sdk import utils as _utils
    from emptyos.sdk import think_cache

    # Skip for trivial messages and for follow-up turns in live sessions
    if len(user_text.strip()) < 20:
        return None
    sess_record = self._get_session(session_id)
    if sess_record and len(sess_record.get("messages", [])) > 0:
        return None

    # ── Stage 1: Classify — understand the question ──────────────────────
    classify_prompt = CLASSIFY_PROMPT.format(user_text=user_text[:400])
    cache_path = think_cache.db_path(self)
    cls_key = think_cache.make_key(classify_prompt, system=CLASSIFY_SYSTEM, domain="text")
    cls_cached = think_cache.get(cache_path, cls_key)
    if cls_cached:
        try:
            classification = _utils.parse_llm_json(cls_cached)
        except Exception:
            classification = None
    else:
        try:
            cls_raw = await self.think(
                classify_prompt, system=CLASSIFY_SYSTEM, domain="text",
                temperature=0.1,
            )
            think_cache.put(
                cache_path, cls_key,
                prompt=classify_prompt, system=CLASSIFY_SYSTEM, model="",
                response=cls_raw, app_id=self.manifest.id, ttl_hours=24,
            )
            classification = _utils.parse_llm_json(cls_raw)
        except Exception:
            classification = None

    task_type = (classification or {}).get("task_type") or "other"
    subject    = (classification or {}).get("subject") or ""
    scope      = (classification or {}).get("scope") or "module"

    # ── Stage 2: Contextualize — load context informed by classification ─
    try:
        claude_md = (self.repo_root / "CLAUDE.md").read_text(encoding="utf-8")
    except Exception:
        return None

    def _extract_section(text: str, header: str) -> str:
        m = re.search(rf"^{re.escape(header)}\s*$", text, flags=re.MULTILINE)
        if not m:
            return ""
        start = m.end()
        nxt = re.search(r"^## ", text[start:], flags=re.MULTILINE)
        return text[start: start + nxt.start()].strip() if nxt else text[start:].strip()

    rules_text = _extract_section(claude_md, "## Development Rules")
    gotchas_text = _extract_section(claude_md, "## Development Gotchas")
    if not rules_text:
        return None

    # Past session archives — keyword filter seeded by both user_text and subject
    past_sessions_block = ""
    try:
        seed_text = f"{user_text} {subject}"
        query_keywords = {w.lower() for w in seed_text.split() if len(w) >= 5}
        archives = self.vault_query(tags=["agent-session"])
        archives.sort(key=lambda r: r.get("modified", 0), reverse=True)
        matches = []
        for r in archives[:10]:
            goal = self.vault_read_section(
                str(self.vault_root / r["path"]), "Goal"
            ).strip()
            if not goal:
                continue
            goal_words = {w.lower() for w in goal.split() if len(w) >= 5}
            if query_keywords & goal_words:
                name = r.get("properties", {}).get("session_name") or r.get("name", "")
                matches.append(f'• "{name}": {goal[:200]}')
            if len(matches) >= 2:
                break
        if matches:
            past_sessions_block = (
                "Past sessions on related tasks (for reference):\n"
                + "\n".join(matches)
            )
    except Exception:
        pass

    # ── Stage 3: Plan — type-aware investigation steps ───────────────────
    orient_prompt = ORIENT_PROMPT.format(
        task_type=task_type,
        subject=subject or user_text[:60],
        scope=scope,
        user_text=user_text[:600],
        rules_text=rules_text[:3000],
        gotchas_text=gotchas_text[:1500],
        past_sessions=past_sessions_block,
    )
    plan_key = think_cache.make_key(orient_prompt, system=ORIENT_SYSTEM, domain="text")
    plan_cached = think_cache.get(cache_path, plan_key)
    if plan_cached:
        try:
            plan = _utils.parse_llm_json(plan_cached)
        except Exception:
            return None
    else:
        try:
            plan_raw = await self.think(
                orient_prompt, system=ORIENT_SYSTEM, domain="text",
                temperature=0.2,
            )
        except Exception:
            return None
        think_cache.put(
            cache_path, plan_key,
            prompt=orient_prompt, system=ORIENT_SYSTEM, model="",
            response=plan_raw, app_id=self.manifest.id, ttl_hours=24,
        )
        try:
            plan = _utils.parse_llm_json(plan_raw)
        except Exception:
            return None

    if plan is None:
        return None
    plan["task_type"] = task_type
    plan["subject"] = subject
    plan["scope"] = scope
    return plan


def orient_block(self, plan: dict) -> str:
    """Format the orient plan as a compact prefix for the user message."""
    task_type = plan.get("task_type") or ""
    subject   = plan.get("subject") or ""
    header = "Orient — pre-turn analysis"
    if task_type:
        header += f" [{task_type}"
        if subject:
            header += f": {subject}"
        header += "]"
    lines = [f"[{header}]"]
    rules = plan.get("relevant_rules") or []
    if rules:
        lines.append("Relevant rules:")
        for r in rules[:4]:
            lines.append(f"  • {r}")
    inv = plan.get("investigation_plan") or []
    if inv:
        lines.append("Investigation plan:")
        for i, step in enumerate(inv[:5], 1):
            lines.append(f"  {i}. {step}")
    sc = (plan.get("success_criteria") or "").strip()
    if sc:
        lines.append(f"Success criteria: {sc}")
    flags = plan.get("risk_flags") or []
    if flags:
        lines.append("Risk flags:")
        for f in flags[:2]:
            lines.append(f"  ⚠ {f}")
    lines.append("[/Orient]")
    return "\n".join(lines)
