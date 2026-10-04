"""people — capacity load + skill matching.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Computes per-person workload from assignment rows and matches people to skill/hour needs. Source of truth for the capacity-band view and the overloaded hub panel.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._load_for_person / self._person_with_load (spine) for load rows; self.list_people (spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from emptyos.sdk import web_route
from .shared import _DEFAULT_ROLE
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import PeopleApp  # noqa: F401 — for type hints only


# ─── Bind to PeopleApp class as ────────────────────────────────
#   match                = _workload.match
#   api_person_workload  = _workload.api_person_workload
#   api_workload         = _workload.api_workload
#   api_match            = _workload.api_match
#   panel_overloaded     = _workload.panel_overloaded
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def match(self, skills: list[str], need_hours: float = 0) -> list[dict]:
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    want = {s.lower() for s in (skills or [])}
    candidates = await self.list_people(active_only=True)
    ranked = []
    for p in candidates:
        have = {s.lower() for s in p.get("skills", [])}
        overlap = len(want & have)
        penalty = max(0.0, p["load_ratio"] - 0.8)
        score = overlap - penalty
        if overlap == 0 and want:
            continue
        ranked.append(
            {
                **p,
                "overlap": overlap,
                "overlap_pct": round(overlap / len(want) * 100, 1) if want else 0,
                "score": round(score, 3),
            }
        )
    ranked.sort(key=lambda r: (-r["score"], -r["overlap"], r["load_ratio"], r["name"]))
    return ranked


@web_route("GET", "/api/people/{id}/workload")
async def api_person_workload(self, request):
    pid = request.path_params.get("id", "")
    person = await self.get_person(pid)
    if not person:
        return {"error": "Person not found"}
    rows = self._load_for_person(pid)
    by_app: dict[str, float] = {}
    by_role: dict[str, float] = {}
    for r in rows:
        by_app[r["item"].get("app", "?")] = (
            by_app.get(r["item"].get("app", "?"), 0) + r["weight_hours"]
        )
        by_role[r.get("role", _DEFAULT_ROLE)] = (
            by_role.get(r.get("role", _DEFAULT_ROLE), 0) + r["weight_hours"]
        )
    # 4D-timeline contract: hand the vault path to the frontend
    # so the auto-mounted 📅 button can fire on this person.
    # Use _find_note (index-backed, slug-tolerant) rather than
    # _find_file which silently misses dash-slug filenames.
    n = self._find_note(pid)
    vault_rel = str(n["path"]).replace("\\", "/") if (n and n.get("path")) else ""
    return {
        "person": person,
        "assignments": rows,
        "by_app": {k: round(v, 2) for k, v in by_app.items()},
        "by_role": {k: round(v, 2) for k, v in by_role.items()},
        "_vault_path": vault_rel,
    }


@web_route("GET", "/api/workload")
async def api_workload(self, request):
    return await self.list_people(active_only=True)


@web_route("GET", "/api/match")
async def api_match(self, request):
    skills_q = request.query_params.get("skills", "")
    skills = [s.strip() for s in skills_q.split(",") if s.strip()]
    try:
        need_hours = float(request.query_params.get("need_hours", 0) or 0)
    except ValueError:
        need_hours = 0
    return await self.match(skills=skills, need_hours=need_hours)


async def panel_overloaded(self) -> list[dict] | None:
    roster = await self.list_people(active_only=True)
    overloaded = [p for p in roster if p["band"] == "overloaded"]
    if not overloaded:
        return None
    return [
        {
            "label": f"{p['name']} · {int(p['load_ratio'] * 100)}%",
            "href": f"/people/#{p['id']}",
            "icon": "🔴",
        }
        for p in overloaded[:6]
    ]
