"""company — scenario dispatch — run / chain / in-room over an org's AI members.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The app-level scenario orchestration: composing the org→scenario shim, dispatching to the scenarios/ package (headless), running the in-room rooms-backed path with a multi-lens digest, chaining one run's digest into the next, and appending run breadcrumbs to member notes. Source of truth for how a prompt fans out across an org..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.get_org (orgs) for the shim; self._push_member_to_rooms (members) before an in-room run; scenarios/ package + lenses + scenarios.base run-store.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from emptyos.sdk import multi_lens_analyze
from .lenses import members_to_lenses
from .scenarios import SCENARIOS
from .scenarios.base import load_run, save_run
from .shared import _now, _vault_rel_member
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import CompanyApp  # noqa: F401 — for type hints only


# ─── Bind to CompanyApp class as ────────────────────────────────
#   run_scenario         = _dispatch.run_scenario
#   _CHAIN_FRAMINGS      = _dispatch._CHAIN_FRAMINGS
#   chain_scenario       = _dispatch.chain_scenario
#   _run_in_room         = _dispatch._run_in_room
#   _append_run_memory   = _dispatch._append_run_memory
#   route_to_specialist  = _dispatch.route_to_specialist
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def run_scenario(
    self, org_id: str, scenario_type: str, prompt: str,
    mode: str = "headless",
) -> dict:
    org = await self.get_org(org_id)
    if not org:
        return {"error": f"org '{org_id}' not found"}
    ai_members = [m for m in (org.get("members") or []) if (m.get("mode") or "human") == "ai"]
    if not ai_members:
        return {"error": "org has no AI members — add one to run scenarios"}
    if scenario_type not in SCENARIOS:
        return {"error": f"unknown scenario '{scenario_type}'"}
    prompt = (prompt or "").strip()
    if not prompt:
        return {"error": "prompt required"}
    if mode not in ("headless", "in-room"):
        mode = "headless"

    # Compose a scenario-compatible "company" shape so existing scenario
    # modules keep working without rewrites. They expect company.id +
    # company.name + workers list with id/name/role/emoji/model/system_prompt.
    # Vision/values/roles are passed through so org_context_suffix can
    # inject them into every per-member system prompt.
    shim = {
        "id": org["id"],
        "name": org["name"],
        "mission": org.get("mission") or "",
        "vision": org.get("vision") or "",
        "values": org.get("values") or "",
        "culture": org.get("culture") or "",
        "roles": org.get("roles") or "",
        "workers": ai_members,
    }

    if mode == "in-room":
        return await self._run_in_room(shim, ai_members, scenario_type, prompt)

    scenario = SCENARIOS[scenario_type]
    record = await scenario.run(self, shim, ai_members, prompt, mode)
    await self._append_run_memory(shim, ai_members, record)
    return record


# Default framings when chaining one scenario's output into the next.
# Each maps next_scenario → wrapper. The full composed prompt is:
#   {framing}\n\n--- PRIOR {prior_scenario} OUTPUT ---\n{digest}\n--- END ---
_CHAIN_FRAMINGS = {
    "critique": (
        "Red-team the following output from the team's prior {prior} session. "
        "Pick it apart from your role. Where will it fail in week 1? What's the "
        "biggest unstated assumption?"
    ),
    "workshop": (
        "The team produced the output below in a prior {prior} session. Revise "
        "it from your role — address the strongest objections, keep what works, "
        "produce a sharpened concrete draft."
    ),
    "interview": (
        "Given the prior {prior} session output below, answer the standing "
        "interview question from your role with the new context in mind."
    ),
}


async def chain_scenario(
    self,
    prior_run_id: str,
    next_scenario: str,
    *,
    framing: str = "",
    mode: str = "headless",
) -> dict:
    """Run `next_scenario` against the same org as `prior_run_id`, feeding
    the prior run's digest (or concatenated responses if no digest) into
    the new prompt. The resulting record carries `parent_run_id` so the
    run history forms a chain.

    `framing` overrides the default per-target framing in `_CHAIN_FRAMINGS`.
    """
    prior = load_run(self, (prior_run_id or "").strip())
    if not prior:
        return {"error": f"prior run '{prior_run_id}' not found"}
    if next_scenario not in SCENARIOS:
        return {"error": f"unknown scenario '{next_scenario}'"}
    org_id = (prior.get("company_id") or "").strip()
    if not org_id:
        return {"error": "prior run has no company_id — cannot chain"}

    # Source text: prefer the digest; fall back to concatenated responses
    # so an in-room run (no digest yet) can still seed a chain.
    digest_text = ((prior.get("digest") or {}).get("text") or "").strip()
    if not digest_text:
        parts: list[str] = []
        for r in prior.get("responses") or []:
            resp = (r.get("response") or "").strip()
            if not resp:
                continue
            header = f"### {r.get('name') or r.get('worker_id') or '?'} ({r.get('role') or ''})"
            parts.append(header + "\n" + resp)
        digest_text = "\n\n".join(parts).strip()
    if not digest_text:
        return {"error": "prior run has no digest or responses to chain on"}

    prior_scenario = prior.get("scenario") or "session"
    framing = (framing or "").strip() or self._CHAIN_FRAMINGS.get(
        next_scenario,
        "Continue from the prior {prior} session output below.",
    ).format(prior=prior_scenario)
    composed_prompt = (
        f"{framing}\n\n"
        f"--- PRIOR {prior_scenario.upper()} OUTPUT (run {prior['id']}) ---\n"
        f"{digest_text}\n"
        f"--- END ---"
    )

    record = await self.run_scenario(
        org_id=org_id,
        scenario_type=next_scenario,
        prompt=composed_prompt,
        mode=mode,
    )
    if isinstance(record, dict) and not record.get("error") and record.get("id"):
        record["parent_run_id"] = prior["id"]
        record["chain"] = {
            "parent_run_id": prior["id"],
            "parent_scenario": prior_scenario,
            "framing": framing,
        }
        save_run(self, record)
    return record


async def _run_in_room(
    self, org: dict, members: list[dict], scenario_type: str, prompt: str,
) -> dict:
    for m in members:
        await self._push_member_to_rooms({
            "id": m.get("id"), "name": m.get("name") or "",
            "org_id": org.get("id") or "",
            "role": m.get("role") or "", "emoji": m.get("emoji") or "",
            "model": m.get("model") or "", "mode": "ai",
        })
    title = f"{org.get('name', org.get('id'))} — {scenario_type}"
    participants = [{"type": "agent", "id": m.get("id")} for m in members]
    try:
        room = await self.call_app(
            "rooms", "create_room",
            title=title,
            participants=participants,
            system_prompt=(
                f"You are debating the following proposal in front of "
                f"the team. Each of you responds from your own role.\n\n"
                f"PROPOSAL:\n{prompt}"
            ),
        )
    except Exception as e:
        record = await SCENARIOS[scenario_type].run(
            self, org, members, prompt, "headless",
        )
        record["in_room_fallback"] = (
            f"rooms unavailable; ran headless. "
            f"{type(e).__name__}: {str(e)[:120]}"
        )
        await self._append_run_memory(org, members, record)
        return record
    if not isinstance(room, dict) or "id" not in room or room.get("error"):
        record = await SCENARIOS[scenario_type].run(
            self, org, members, prompt, "headless",
        )
        err = room.get("error") if isinstance(room, dict) else "unknown"
        record["in_room_fallback"] = f"rooms refused room creation ({err}); ran headless."
        await self._append_run_memory(org, members, record)
        return record
    record = {
        "id": room["id"],
        "scenario": scenario_type,
        "company_id": org.get("id"),
        "company_name": org.get("name"),
        "prompt": prompt,
        "mode": "in-room",
        "started": _now(),
        "completed": "",
        "room": room,
        "responses": [],
        "digest": {"text": "", "provenance": {}},
    }
    # Cheap multi-lens digest — gives an immediate decision view without
    # making the user chat 9 turns. Heavy turn-by-turn debate is opt-in
    # via rooms.run_panel on the same room id; on a single underlying
    # model that 9-call panel produces ~the same content as this one call.
    lenses = members_to_lenses(members)
    if len(lenses) >= 2:
        try:
            ml = await multi_lens_analyze(self, prompt, lenses, synthesize=True)
            if not ml.get("error"):
                record["digest"]["text"] = ml.get("analysis") or ""
                record["digest"]["provenance"] = {
                    "method": "multi_lens",
                    "lens_count": len(lenses),
                }
            else:
                record["digest"]["text"] = f"(digest unavailable: {ml['error']})"
        except Exception as e:
            record["digest"]["text"] = (
                f"(digest unavailable: {type(e).__name__}: {str(e)[:120]})"
            )
    save_run(self, record)
    return record


async def _append_run_memory(self, org: dict, members: list[dict], record: dict) -> None:
    scenario = record.get("scenario", "")
    oid = org.get("id")
    run_id = record.get("id", "")
    line = f"- {record.get('completed') or _now()} — {scenario} ({run_id}) — _{(record.get('prompt') or '')[:80]}_"
    for m in members:
        mid = m.get("id")
        if not mid or not oid:
            continue
        try:
            self.vault_append_section(_vault_rel_member(oid, mid), "Scenarios", line)
        except Exception:
            continue


# Single-consumer prompt (CLAUDE.md rule 12) — stays local to dispatch.py rather
# than shared.py per .claude/rules/multi-module-apps.md Rule 5/6 (shared.py is
# for constants two or more helpers need; only route_to_specialist uses this).
ROUTE_SYSTEM = (
    "You are routing a single item to the best-fit AI specialist on a team. "
    "Read the item and each candidate's role + system prompt, then choose the "
    "one who should own it. Do not explain your reasoning."
)


async def route_to_specialist(self, org_id: str, item_text: str) -> dict:
    """Pick exactly ONE best-fit AI member of the org and dispatch the item
    to them — the "expert-pool" dispatcher `run_scenario` doesn't provide
    (that one fans a prompt out to every AI member). See
    docs/AGENT-TEAM-PATTERNS.md and .claude/rules/verb-registry.md."""
    org = await self.get_org(org_id)
    if not org:
        return {"error": f"org '{org_id}' not found"}
    ai_members = [m for m in (org.get("members") or []) if (m.get("mode") or "human") == "ai"]
    if not ai_members:
        return {"error": "org has no AI members — add one to route"}
    item_text = (item_text or "").strip()
    if not item_text:
        return {"error": "item_text required"}

    if len(ai_members) == 1:
        chosen = ai_members[0]
    else:
        choices = {
            m["id"]: f"{m.get('role') or 'member'} — {(m.get('system_prompt') or '')[:200]}"
            for m in ai_members
        }
        chosen_id = await self.select(
            item_text, choices, system=ROUTE_SYSTEM, default=ai_members[0]["id"],
        )
        chosen = next((m for m in ai_members if m.get("id") == chosen_id), ai_members[0])

    if self.over_budget("company"):
        return {"error": "over monthly budget", "member_id": chosen.get("id")}

    sys_prompt = (chosen.get("system_prompt") or "").strip()
    if not sys_prompt:
        sys_prompt = (self.vault_read_section(
            _vault_rel_member(org_id, chosen["id"]), "System Prompt",
        ) or "").strip()

    self.cite("member", chosen["id"])
    kwargs: dict = {"domain": "text", "system": sys_prompt}
    if chosen.get("model"):
        kwargs["model"] = chosen["model"]
    response = await self.think(item_text, **kwargs)

    await self.emit("company:specialist_routed", {
        "org_id": org_id, "member_id": chosen["id"], "role": chosen.get("role") or "",
    })
    try:
        self.vault_append_section(
            _vault_rel_member(org_id, chosen["id"]), "Scenarios",
            f"- {_now()} — routed — _{item_text[:80]}_",
        )
    except Exception:
        pass

    return {
        "member_id": chosen["id"],
        "member_name": chosen.get("name") or chosen["id"],
        "role": chosen.get("role") or "",
        "response": response if isinstance(response, str) else str(response),
        "provenance": self.last_provenance(),
    }
