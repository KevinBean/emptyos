"""people — LLM-backed profile, suggest, chat, persona.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The four think()-backed person surfaces (profile synthesis, contact suggestion, 1:1 chat, persona). System prompts live in shared.py.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._find_file / self._shape_person / self._person_with_load (spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from emptyos.sdk import parse_llm_json, web_route
from .shared import _FILENAME_PREFIX, AI_SUGGEST_SYSTEM, AI_SUGGEST_FORMAT, CHAT_SYSTEM, PERSONA_SYSTEM
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import PeopleApp  # noqa: F401 — for type hints only


# ─── Bind to PeopleApp class as ────────────────────────────────
#   api_profile     = _ai_surfaces.api_profile
#   api_ai_suggest  = _ai_surfaces.api_ai_suggest
#   api_chat        = _ai_surfaces.api_chat
#   api_persona     = _ai_surfaces.api_persona
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/people/{id}/profile")
async def api_profile(self, request):
    ident = request.path_params.get("id", "")
    target = self._find_file(ident)
    if not target or not target.exists():
        return {"error": "Person not found"}
    content = await self.read(str(target))
    fm = self._parse_frontmatter(content)
    quick_log = self._parse_quick_log(content)
    note = self._find_note(ident) or {}
    shaped = self._enrich(
        self._person_with_load(
            self._shape_person(
                note if note else {"name": target.stem, "properties": fm, "path": str(target)}
            )
        )
    )
    # Backlinks
    backlinks: list[str] = []
    vault = self.kernel.config.notes_path or Path(".")
    try:
        results = await self.search(f"[[{target.stem}]]", path=str(vault))
        for r in results[:10]:
            rpath = r if isinstance(r, str) else r.get("path", "")
            if rpath and str(target) not in rpath:
                try:
                    backlinks.append(str(Path(rpath).relative_to(vault)))
                except Exception:
                    backlinks.append(rpath)
    except Exception:
        pass
    # Body sections
    body = content
    if body.startswith("---"):
        end = body.find("---", 3)
        if end > 0:
            body = body[end + 3 :].strip()
    sections: dict[str, list[str]] = {}
    current = "intro"
    sections[current] = []
    for line in body.split("\n"):
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        else:
            sections.setdefault(current, []).append(line)
    sections_clean = {
        k: "\n".join(v).strip() for k, v in sections.items() if "\n".join(v).strip()
    }
    return {
        **shaped,
        "frontmatter": fm,
        "quick_log": quick_log,
        "backlinks": backlinks[:30],
        "sections": sections_clean,
        "log_count": len(quick_log),
        "backlink_count": len(backlinks),
    }


@web_route("POST", "/api/ai-suggest")
async def api_ai_suggest(self, request):
    people = await self.list_people()
    overdue = self._get_overdue(people)
    summary_lines = [
        f"- {p['name']}: {p.get('relationship', '')}, energy={p.get('energy', '')}, "
        f"trust={p.get('trust_level', '')}, last={p.get('last_contact', '')}, "
        f"freq={p.get('contact_frequency', '')}, health={p['health_score']}"
        for p in people[:30]
    ]
    overdue_lines = [f"- {p['name']}: {p['days_overdue']}d overdue" for p in overdue[:10]]
    user_msg = (
        "Contacts:\n" + "\n".join(summary_lines) + "\n\n"
        "Overdue:\n"
        + ("\n".join(overdue_lines) if overdue_lines else "None")
        + "\n\n"
        + AI_SUGGEST_FORMAT
    )
    try:
        result = await self.think(
            user_msg,
            system=AI_SUGGEST_SYSTEM,
            domain="text",
            temperature=0.4,
        )
        items = parse_llm_json(result, fallback=[])
        if not isinstance(items, list):
            items = []
        by_name = {p["name"].lower(): p for p in people}
        for item in items:
            match = by_name.get((item.get("name") or "").lower(), {})
            item["id"] = match.get("id", "")
            item["health_score"] = match.get("health_score", 0)
            item["days_since"] = match.get("days_since")
            item["relationship"] = match.get("relationship", "")
        return {"suggestions": items}
    except Exception as e:
        return {"suggestions": [], "error": str(e)}


@web_route("POST", "/api/people/{id}/chat")
async def api_chat(self, request):
    ident = request.path_params.get("id", "")
    data = await request.json()
    question = (data.get("question") or "").strip()
    if not question:
        return {"error": "question required"}
    target = self._find_file(ident)
    if not target:
        return {"error": "Person not found"}
    content = await self.read(str(target))
    fm = self._parse_frontmatter(content)
    quick_log = self._parse_quick_log(content)
    log_text = "\n".join(f"  {e['date']}: {e['text']}" for e in quick_log) or "(no log entries)"
    fm_text = "\n".join(f"  {k}: {v}" for k, v in fm.items()) or "(no frontmatter)"
    body = content
    if body.startswith("---"):
        end = body.find("---", 3)
        if end > 0:
            body = body[end + 3 :].strip()
    display_name = target.stem.lstrip(_FILENAME_PREFIX).replace("-", " ")
    user_msg = (
        f"Person: {display_name}\n\n"
        f"## Contact Info\n{fm_text}\n\n## Quick Log\n{log_text}\n\n"
        f"## Full Note Content\n{body[:3000]}\n\n## Question\n{question}\n\n"
        f"Answer concisely in 2-4 sentences."
    )
    try:
        result = await self.think(
            user_msg,
            system=CHAT_SYSTEM,
            domain="text",
            temperature=0.3,
        )
        return {"id": ident, "name": display_name, "question": question, "answer": result}
    except Exception as e:
        return {"error": f"AI unavailable: {e}"}


@web_route("GET", "/api/people/{id}/persona")
async def api_persona(self, request):
    ident = request.path_params.get("id", "")
    target = self._find_file(ident)
    if not target:
        return {"error": "Person not found"}
    content = await self.read(str(target))
    quick_log = self._parse_quick_log(content)
    log_text = "\n".join(f"  {e['date']}: {e['text']}" for e in quick_log) or "(no log entries)"
    body = content
    if body.startswith("---"):
        end = body.find("---", 3)
        if end > 0:
            body = body[end + 3 :].strip()
    display_name = target.stem.lstrip(_FILENAME_PREFIX).replace("-", " ")
    user_msg = (
        f"Interactions with {display_name}:\n{log_text}\n\nAdditional notes:\n{body[:2000]}"
    )
    try:
        result = await self.think(
            user_msg,
            system=PERSONA_SYSTEM,
            domain="text",
            temperature=0.4,
        )
        return {"id": ident, "name": display_name, "persona": result}
    except Exception as e:
        return {"error": f"AI unavailable: {e}"}
