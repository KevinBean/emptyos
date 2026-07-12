"""ppt — AI deck authoring — plan, generate, regenerate.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the LLM authoring flow (outline planning, generate-from-plan, per-slide/section regeneration) plus the elements/intents config endpoints. Source of truth for prompt assembly around deck generation.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.save_deck / self._draft_body (crud) to persist; self._path_for (spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime
from emptyos.sdk import web_route, parse_llm_json
from .parser import (
    ALL_ELEMENTS,
    DEFAULT_ELEMENTS,
    DEFAULT_VISUAL_STYLE,
    INTENTS,
    _HR_RE,
    _normalize_elements,
    _normalize_visual_style,
    _slugify,
    _split_frontmatter,
    build_gen_from_plan_system,
    build_outline_system,
    build_plan_system,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import PptApp  # noqa: F401 — for type hints only


# ─── Bind to PptApp class as ────────────────────────────────
#   plan_deck               = _generate.plan_deck
#   generate_from_plan      = _generate.generate_from_plan
#   regenerate_deck         = _generate.regenerate_deck
#   _call_think             = _generate._call_think
#   api_elements            = _generate.api_elements
#   api_intents             = _generate.api_intents
#   api_plan                = _generate.api_plan
#   api_generate_from_plan  = _generate.api_generate_from_plan
#   api_regenerate          = _generate.api_regenerate
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def plan_deck(
    self,
    title: str,
    outline: str = "",
    audience: str = "",
    duration_min: int = 5,
    allowed_elements: list[str] | None = None,
    source_path: str = "",
) -> dict:
    """Stage 1 of plan-first generation: produce a structured plan as JSON.

    The plan contains intent, audience, per-slide surface + beat + headline.
    It is *not* persisted — the user reviews/edits, then calls
    `generate_from_plan` to materialize the deck.
    """
    elements = _normalize_elements(allowed_elements)
    system = build_plan_system(elements)
    source_text = ""
    if source_path:
        try:
            source_text = await self.read(source_path)
        except Exception:
            source_text = ""
        if len(source_text) > 6000:
            source_text = source_text[:6000] + "\n\n...[truncated]"
    parts = [
        f"Title (user input, refine if useful): {title}",
        f"Audience: {audience or 'unspecified — infer from outline'}",
        f"Target duration: {duration_min} minutes",
        f"Outline / hints:\n{outline.strip() or '(none — derive structure from title)'}",
    ]
    if source_text:
        parts.append("\nSource material to plan from:")
        parts.append(source_text.strip())
    parts.append("\nReturn the plan JSON now.")
    prompt = "\n\n".join(parts)
    try:
        text = await self.think(prompt, system=system, temperature=0.4)
    except Exception as e:
        return {"error": f"think failed: {e}"}
    plan = parse_llm_json(text, fallback=None)
    if not isinstance(plan, dict) or "slides" not in plan:
        return {"error": "could not parse plan JSON", "raw": text[:1000]}
    slides = plan.get("slides") or []
    if not isinstance(slides, list) or not slides:
        return {"error": "plan has no slides", "raw": text[:1000]}
    clean_slides = []
    for s in slides:
        if not isinstance(s, dict):
            continue
        surface = str(s.get("surface", "bullets")).strip().lower()
        if surface not in elements:
            surface = "bullets"
        clean_slides.append({
            "surface": surface,
            "beat": str(s.get("beat", "")).strip(),
            "headline": str(s.get("headline", "")).strip(),
        })
    intent = str(plan.get("intent", "")).strip().lower()
    if intent not in INTENTS:
        intent = "teach"
    return {
        "title": str(plan.get("title", title)).strip() or title,
        "subtitle": str(plan.get("subtitle", "")).strip(),
        "intent": intent,
        "audience": str(plan.get("audience", audience)).strip(),
        "duration_min": int(plan.get("duration_min", duration_min) or duration_min),
        "allowed_elements": elements,
        "slides": clean_slides,
    }


async def generate_from_plan(self, plan: dict, source_path: str = "") -> dict:
    """Stage 2 of plan-first generation: render an approved plan to a deck.

    Saves a new deck note and returns its id + path.
    """
    if not isinstance(plan, dict):
        return {"error": "plan must be an object"}
    title = str(plan.get("title", "")).strip()
    if not title:
        return {"error": "plan.title is required"}
    slides = plan.get("slides") or []
    if not isinstance(slides, list) or not slides:
        return {"error": "plan.slides is required"}
    elements = _normalize_elements(plan.get("allowed_elements"))
    visual_style = _normalize_visual_style(
        plan.get("visual_style"),
        self.app_config("default_visual_style", DEFAULT_VISUAL_STYLE),
    )
    intent = str(plan.get("intent", "")).strip().lower()
    if intent not in INTENTS:
        intent = ""

    slug = _slugify(title)
    rel = f"{self._ppt_dir()}/{slug}.md"
    if self._path_for(slug):
        slug = f"{slug}-{datetime.now().strftime('%H%M%S')}"
        rel = f"{self._ppt_dir()}/{slug}.md"

    source_text = ""
    if source_path:
        try:
            source_text = await self.read(source_path)
        except Exception:
            source_text = ""
        if len(source_text) > 6000:
            source_text = source_text[:6000] + "\n\n...[truncated]"

    plan_for_llm = {
        "title": title,
        "subtitle": plan.get("subtitle", ""),
        "intent": intent,
        "audience": plan.get("audience", ""),
        "slides": [
            {
                "surface": s.get("surface", "bullets"),
                "headline": s.get("headline", ""),
                "beat": s.get("beat", ""),
            }
            for s in slides
            if isinstance(s, dict)
        ],
    }
    system = build_gen_from_plan_system(elements, intent)
    prompt_parts = [
        "Approved plan (JSON):",
        "```json",
        _json.dumps(plan_for_llm, ensure_ascii=False, indent=2),
        "```",
    ]
    if source_text:
        prompt_parts.append("\nSource material to draw content from:")
        prompt_parts.append(source_text.strip())
    prompt_parts.append("\nWrite the deck markdown now. Honor every slide's surface.")
    prompt = "\n".join(prompt_parts)
    try:
        text = await self.think(prompt, system=system, temperature=0.6)
    except Exception as e:
        return {"error": f"think failed: {e}"}
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    fm = {
        "title": title,
        "type": "deck",
        "lifecycle": "living",
        "theme": self.app_config("default_theme", "dark"),
        "aspect": self.app_config("default_aspect", "16:9"),
        "visual_style": visual_style,
        "created": datetime.now().strftime("%Y-%m-%d"),
        "updated": datetime.now().strftime("%Y-%m-%d"),
        "allowed_elements": elements,
        "intent": intent,
        "audience": plan.get("audience", ""),
        "tags": ["deck"],
    }
    self.vault_create_note(rel, fm, text)
    await self.emit("ppt:created", {"id": slug, "title": title, "from": "plan"})
    return {"id": slug, "path": rel, "title": title, "slides": len(slides)}


async def regenerate_deck(
    self,
    id: str,
    direction: str,
    allowed_elements: list[str] | None = None,
    scope: str = "whole",
    slide_index: int | None = None,
    selection: str = "",
    selection_start: int | None = None,
    selection_end: int | None = None,
) -> dict:
    """Rewrite a deck via `self.think()` guided by a `direction` string.

    `scope` selects what the model rewrites:
      - "whole"     — full deck (default)
      - "slide"     — just slide at `slide_index` (0-based)
      - "selection" — just the substring [selection_start, selection_end]
                      in the raw markdown; falls back to literal match on
                      `selection` if offsets are missing
    """
    path = self._path_for(id)
    if not path:
        return {"error": "Deck not found"}
    current = await self.read(path)
    fm, body = _split_frontmatter(current)
    title = fm.get("title") or id

    if allowed_elements is None:
        allowed_elements = fm.get("allowed_elements") or None
    elements = _normalize_elements(allowed_elements)
    system = build_outline_system(elements)

    # Scoped rewrites — same system prompt, different ask + splice strategy.
    if scope == "slide" and slide_index is not None:
        slides = _HR_RE.split(body)
        if slide_index < 0 or slide_index >= len(slides):
            return {"error": f"slide_index {slide_index} out of range (0-{len(slides) - 1})"}
        target = slides[slide_index].strip("\n")
        prompt = (
            f"Topic: {title}\n\n"
            f"Direction (apply to ONE slide only): {direction}\n\n"
            f"Slide to rewrite (slide {slide_index + 1} of {len(slides)}):\n\n"
            f"```markdown\n{target}\n```\n\n"
            "Return ONLY the rewritten slide's markdown — no `---` separators, no other slides, "
            "no frontmatter. Keep `Notes:` lines if they make sense. Same surface kind unless the "
            "direction asks otherwise."
        )
        new_chunk = await self._call_think(prompt, system)
        if new_chunk is None:
            return {"error": "think failed"}
        slides[slide_index] = new_chunk
        new_body = "\n\n---\n\n".join(s.strip("\n") for s in slides if s.strip())
    elif scope == "selection":
        if selection_start is not None and selection_end is not None and selection_end > selection_start:
            target = current[selection_start:selection_end]
            prompt = (
                f"Topic: {title}\n\n"
                f"Direction (apply to this snippet only): {direction}\n\n"
                f"Snippet to rewrite:\n\n```markdown\n{target}\n```\n\n"
                "Return ONLY the rewritten snippet — no other content, no frontmatter, "
                "no slide separators unless the snippet already had them."
            )
            new_chunk = await self._call_think(prompt, system)
            if new_chunk is None:
                return {"error": "think failed"}
            new_raw = current[:selection_start] + new_chunk + current[selection_end:]
            await self.write(path, new_raw)
            self.vault_update(path, {"updated": datetime.now().strftime("%Y-%m-%d")})
            await self.emit("ppt:updated", {"id": id, "field": "selection"})
            return {"ok": True, "raw": new_raw, "slides": len(_HR_RE.split(_split_frontmatter(new_raw)[1]))}
        elif selection and selection in current:
            prompt = (
                f"Topic: {title}\n\n"
                f"Direction (apply to this snippet only): {direction}\n\n"
                f"Snippet to rewrite:\n\n```markdown\n{selection}\n```\n\n"
                "Return ONLY the rewritten snippet."
            )
            new_chunk = await self._call_think(prompt, system)
            if new_chunk is None:
                return {"error": "think failed"}
            new_raw = current.replace(selection, new_chunk, 1)
            await self.write(path, new_raw)
            self.vault_update(path, {"updated": datetime.now().strftime("%Y-%m-%d")})
            await self.emit("ppt:updated", {"id": id, "field": "selection"})
            return {"ok": True, "raw": new_raw, "slides": len(_HR_RE.split(_split_frontmatter(new_raw)[1]))}
        else:
            return {"error": "selection scope requires selection_start/end or matching selection text"}
    else:
        # whole deck
        prompt = (
            f"Topic: {title}\n\n"
            f"Direction: {direction}\n\n"
            "Existing deck (rewrite this; keep what works, change what the direction asks):\n\n"
            f"```markdown\n{current}\n```\n\n"
            "Return only the new deck markdown body (NO frontmatter — I'll re-attach it). "
            "Slide separators stay as `---` on their own line."
        )
        text = await self._call_think(prompt, system)
        if text is None:
            return {"error": "think failed"}
        new_body = text

    # Re-attach the original frontmatter so theme/aspect/title persist.
    from emptyos.runtime.vault_index import _serialize_fm

    fm["updated"] = datetime.now().strftime("%Y-%m-%d")
    fm["allowed_elements"] = elements
    new_raw = _serialize_fm(fm) + "\n\n" + new_body.lstrip()
    await self.write(path, new_raw)
    self.vault_update(path, {"updated": fm["updated"], "allowed_elements": elements})
    await self.emit("ppt:updated", {"id": id, "field": f"regenerated:{scope}"})
    return {"ok": True, "raw": new_raw, "slides": len(_HR_RE.split(new_body))}


async def _call_think(self, prompt: str, system: str) -> str | None:
    """Single-shot think() with code-fence + frontmatter stripping."""
    try:
        text = await self.think(prompt, system=system, temperature=0.6)
    except Exception:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    text = re.sub(r"^---\s*\n.*?\n---\s*\n+", "", text, flags=re.DOTALL)
    return text.strip()


@web_route("GET", "/api/elements")
async def api_elements(self, request):
    """List the available slide-surface elements + sensible defaults."""
    return {
        "all": ALL_ELEMENTS,
        "defaults": list(DEFAULT_ELEMENTS),
        "labels": {
            "bullets": "Bullet lists",
            "quote": "Pull quotes",
            "table": "Tables",
            "code": "Code blocks",
            "image": "AI-generated images",
            "vault": "Existing vault images",
            "screenshot": "URL screenshots",
            "embed": "Live page embeds",
            "divider": "Section dividers",
            "mermaid": "Diagrams (flowchart / sequence / mindmap)",
            "chart": "Charts (bar / line / pie / doughnut)",
            "narration": "Auto-narration (TTS of slide notes)",
            "audio": "Audio clips (vault / URL)",
            "video": "Video (vault / YouTube / URL)",
        },
    }


@web_route("GET", "/api/intents")
async def api_intents(self, request):
    """List deck intents — used by the plan-first new-deck flow."""
    return {
        "intents": [
            {"id": k, "label": v["label"], "guidance": v["guidance"]}
            for k, v in INTENTS.items()
        ],
    }


@web_route("POST", "/api/plan")
async def api_plan(self, request):
    body = await request.json()
    title = str(body.get("title", "")).strip()
    if not title:
        return {"error": "title is required"}
    plan = await self.plan_deck(
        title=title,
        outline=body.get("outline", ""),
        audience=body.get("audience", ""),
        duration_min=int(body.get("duration_min", 5) or 5),
        allowed_elements=body.get("allowed_elements"),
        source_path=body.get("source_path", ""),
    )
    if not plan.get("error"):
        plan["visual_style"] = _normalize_visual_style(
            body.get("visual_style"),
            self.app_config("default_visual_style", DEFAULT_VISUAL_STYLE),
        )
    return plan


@web_route("POST", "/api/generate-from-plan")
async def api_generate_from_plan(self, request):
    body = await request.json()
    return await self.generate_from_plan(
        plan=body.get("plan") or {},
        source_path=body.get("source_path", ""),
    )


@web_route("POST", "/api/decks/{id}/regenerate")
async def api_regenerate(self, request):
    body = await request.json()
    return await self.regenerate_deck(
        request.path_params["id"],
        body.get("direction", ""),
        body.get("allowed_elements"),
    )


async def deliver_work(self, brief: dict) -> dict:
    """[[contributes.work.deliverable]] handler — editable deck note from a work brief.

    `brief`: {ask, title, brief, outline: [str], sources: str, run_id}.
    Returns {id, artifact_path, open_url} or {error}."""
    brief = brief or {}
    title = (brief.get("title") or brief.get("ask") or "Untitled deck").strip()
    context = (brief.get("brief") or "").strip()
    outline = "\n".join(str(s) for s in brief.get("outline") or [])
    if context:
        outline = f"{context}\n\n{outline}"
    res = await self.create_deck(title, outline=outline)
    if res.get("error"):
        return {"error": res["error"]}
    return {"id": res["id"], "artifact_path": res.get("path", ""), "open_url": f"/ppt/#{res['id']}"}
