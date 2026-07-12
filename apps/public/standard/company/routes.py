"""company — all @web_route HTTP handlers (/orgs/api/*).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The thin HTTP surface — every /orgs/api/* route. Each handler parses the request and delegates to a domain method on self; no business logic lives here beyond legacy-param aliasing (company_id → org_id) and pending-action wiring for workshop runs..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: every domain method via self; scenarios.base.list_runs/load_run + scenarios.workshop for run + pending endpoints.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from emptyos.sdk import web_route
from .scenarios import SCENARIO_META
from .scenarios import workshop as workshop_mod
from .scenarios.base import list_runs, load_run
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import CompanyApp  # noqa: F401 — for type hints only


# ─── Bind to CompanyApp class as ────────────────────────────────
#   api_list_orgs           = _routes.api_list_orgs
#   api_get_org             = _routes.api_get_org
#   api_add_org             = _routes.api_add_org
#   api_append_org_section  = _routes.api_append_org_section
#   api_archive_org         = _routes.api_archive_org
#   api_list_assets         = _routes.api_list_assets
#   api_add_asset           = _routes.api_add_asset
#   api_remove_asset        = _routes.api_remove_asset
#   api_add_member          = _routes.api_add_member
#   api_remove_member       = _routes.api_remove_member
#   api_list_memberships    = _routes.api_list_memberships
#   api_set_field           = _routes.api_set_field
#   api_list_scenarios      = _routes.api_list_scenarios
#   api_run_scenario        = _routes.api_run_scenario
#   api_chain_scenario      = _routes.api_chain_scenario
#   api_list_runs           = _routes.api_list_runs
#   api_get_run             = _routes.api_get_run
#   api_apply_pending       = _routes.api_apply_pending
#   api_reject_pending      = _routes.api_reject_pending
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/orgs")
async def api_list_orgs(self, request):
    reality = request.query_params.get("reality", "")
    scope = request.query_params.get("scope", "")
    kind = request.query_params.get("kind", "")
    return {"orgs": await self.list_orgs(reality=reality, scope=scope, kind=kind)}


@web_route("GET", "/api/orgs/{oid}")
async def api_get_org(self, request):
    oid = request.path_params["oid"]
    org = await self.get_org(oid)
    if not org:
        return {"error": "not found"}
    return org


@web_route("POST", "/api/orgs")
async def api_add_org(self, request):
    data = await self.read_json(request)
    return await self.add_org(
        name=data.get("name", ""),
        kind=data.get("kind", "team"),
        reality=data.get("reality", "real"),
        scope=data.get("scope", "member"),
        mission=data.get("mission", ""),
        vision=data.get("vision", ""),
        values=data.get("values", ""),
        culture=data.get("culture", ""),
        roles=data.get("roles", ""),
        parent=data.get("parent", ""),
        company_id=data.get("company_id", ""),
    )


@web_route("POST", "/api/orgs/{oid}/append")
async def api_append_org_section(self, request):
    oid = request.path_params["oid"]
    data = await self.read_json(request)
    return await self.append_org_section(
        org_id=oid,
        section=data.get("section", ""),
        text=data.get("text", ""),
        prepend_date=data.get("prepend_date", True),
    )


@web_route("DELETE", "/api/orgs/{oid}")
async def api_archive_org(self, request):
    oid = request.path_params["oid"]
    return await self.archive_org(oid)


@web_route("GET", "/api/orgs/{oid}/assets")
async def api_list_assets(self, request):
    oid = request.path_params["oid"]
    return {"assets": await self.list_assets(oid)}


@web_route("POST", "/api/orgs/{oid}/assets")
async def api_add_asset(self, request):
    oid = request.path_params["oid"]
    data = await self.read_json(request)
    return await self.add_asset(oid, data.get("ref", ""))


@web_route("DELETE", "/api/orgs/{oid}/assets")
async def api_remove_asset(self, request):
    oid = request.path_params["oid"]
    data = await self.read_json(request)
    return await self.remove_asset(oid, data.get("ref", ""))


@web_route("POST", "/api/members")
async def api_add_member(self, request):
    data = await self.read_json(request)
    return await self.add_member(
        org_id=data.get("org_id", ""),
        mode=data.get("mode", "human"),
        person_id=data.get("person_id", ""),
        name=data.get("name", ""),
        system_prompt=data.get("system_prompt", ""),
        model=data.get("model", ""),
        emoji=data.get("emoji", ""),
        role=data.get("role", ""),
        dept=data.get("dept", ""),
        reports_to=data.get("reports_to", ""),
        joined=data.get("joined", ""),
    )


@web_route("DELETE", "/api/members/{mid}")
async def api_remove_member(self, request):
    mid = request.path_params["mid"]
    return await self.remove_member(mid)


@web_route("GET", "/api/memberships")
async def api_list_memberships(self, request):
    """Cross-app endpoint — kept under /memberships (not /members) since
    it's also used by `apps/people/` person detail. Behaviour: returns
    member rows decorated with `org_name` and `org_kind` when filtering
    by `person_id`, so the people app doesn't need to round-trip."""
    org_id = request.query_params.get("org_id", "")
    person_id = request.query_params.get("person_id", "")
    mode = request.query_params.get("mode", "")
    rows = await self.list_members(org_id=org_id, person_id=person_id, mode=mode)
    if person_id:
        orgs = {o["id"]: o for o in await self.list_orgs()}
        for m in rows:
            oid = m.get("org_id") or ""
            o = orgs.get(oid) or {}
            m["org_name"] = o.get("name") or oid
            m["org_kind"] = o.get("kind") or ""
    return {"memberships": rows}


@web_route("POST", "/api/set-field")
async def api_set_field(self, request):
    data = await self.read_json(request)
    return await self.set_field(
        id=data.get("id", ""),
        field=data.get("field", ""),
        value=data.get("value"),
    )


@web_route("GET", "/api/scenarios")
async def api_list_scenarios(self, request):
    return {
        "scenarios": [
            {"id": k, **v} for k, v in SCENARIO_META.items()
        ],
    }


@web_route("POST", "/api/scenario/run")
async def api_run_scenario(self, request):
    data = await self.read_json(request)
    # Accept either org_id (new) or company_id (legacy clients) — the
    # scenario shim is org-shaped internally.
    org_id = data.get("org_id") or data.get("company_id", "")
    return await self.run_scenario(
        org_id=org_id,
        scenario_type=data.get("scenario_type", ""),
        prompt=data.get("prompt", ""),
        mode=data.get("mode", "headless"),
    )


@web_route("POST", "/api/scenario/chain")
async def api_chain_scenario(self, request):
    """Run `next_scenario` against the same org as `prior_run_id`,
    feeding the prior run's digest into the new prompt. Result carries
    `parent_run_id` so runs form a chain (workshop → critique → revised
    workshop is the canonical loop)."""
    data = await self.read_json(request)
    return await self.chain_scenario(
        prior_run_id=data.get("prior_run_id", ""),
        next_scenario=data.get("next_scenario") or data.get("scenario_type", ""),
        framing=data.get("framing", ""),
        mode=data.get("mode", "headless"),
    )


@web_route("GET", "/api/runs")
async def api_list_runs(self, request):
    # Run records still carry `company_id` for back-compat with scenarios.base.
    cid = request.query_params.get("org_id") or request.query_params.get("company_id") or None
    return {"runs": list_runs(self, company_id=cid)}


@web_route("GET", "/api/runs/{run_id}")
async def api_get_run(self, request):
    rid = request.path_params["run_id"]
    record = load_run(self, rid)
    if not record:
        return {"error": "not found"}
    if record.get("scenario") == "workshop":
        record["pending"] = workshop_mod.list_pending(self, run_id=rid)
    return record


@web_route("POST", "/api/pending/{action_id}/apply")
async def api_apply_pending(self, request):
    aid = request.path_params["action_id"]
    return await workshop_mod.apply_pending(self, aid)


@web_route("POST", "/api/pending/{action_id}/reject")
async def api_reject_pending(self, request):
    aid = request.path_params["action_id"]
    return await workshop_mod.reject_pending(self, aid)
