"""replay — scheduled (cron) recipe triggers.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the optional cron trigger on a recipe note (replay-no-trigger
gap) — every run is manually started today; competitors' whole pitch is
"runs unattended once set up". Reuses `emptyos.sdk.cron_rules.CronRuleScheduler`
as its 4th consumer (after workflows/staff/mail) rather than hand-rolling
scheduler wiring — see the gap analysis note's own suggested change.

A cron trigger changes *who clicks go*, never *what's allowed to fire*: the
job calls the same `run()` verb the UI's "Run" button calls, so every
state-changing step still routes through the autopilot eligibility floor +
the rooms review gate (app.py's own docstring). This module adds no new
capability, just a scheduled caller of an existing one.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self.recipes`` (spine), ``self.run`` (replay.py).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import ReplayApp  # noqa: F401 — for type hints only


# ─── Bind to ReplayApp class as ──────────────────────────────────────
#   _register_recipe_jobs = _trigger._register_recipe_jobs
#   _unregister_recipe_jobs = _trigger._unregister_recipe_jobs
#   _sync_recipe_job      = _trigger._sync_recipe_job
#   api_set_trigger       = _trigger.api_set_trigger
#   _recipe_scheduler       = _trigger._recipe_scheduler
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _trigger_job(app, rid: str):
    async def job():
        await app.run(rid, {})
    return job


def _recipe_scheduler(self):
    """Lazily-built CronRuleScheduler owning this app's replay:trigger:*
    jobs (emptyos/sdk/cron_rules.py — shared with mail/staff/workflows)."""
    sched = getattr(self, "_trigger_sched", None)
    if sched is None:
        from emptyos.sdk.cron_rules import CronRuleScheduler
        sched = CronRuleScheduler(
            self, prefix="replay:trigger:",
            id_of=lambda r: r["id"],
            cron_of=lambda r: r.get("trigger_cron") or "",
            should_schedule=lambda r: (
                self._enabled() and bool(r.get("trigger_enabled")) and bool(r.get("trigger_cron"))
            ),
            make_job=lambda r: _trigger_job(self, r["id"]),
        )
        self._trigger_sched = sched
    return sched


def _register_recipe_jobs(self) -> None:
    if self._enabled():
        self._recipe_scheduler().register_all(self.recipes.list())


def _unregister_recipe_jobs(self) -> None:
    self._recipe_scheduler().unregister_all()


def _sync_recipe_job(self, recipe: dict) -> None:
    self._recipe_scheduler().sync(recipe)


@web_route("PATCH", "/api/recipes/{rid}/trigger")
async def api_set_trigger(self, request):
    """Set/clear a recipe's scheduled trigger. Body: {cron?, enabled?}.

    `cron` is validated by attempting to register it — an invalid cron
    string fails the schedule attempt rather than a 500 (CronRuleScheduler
    logs + no-ops on an unschedulable record).
    """
    if not self._enabled():
        return {"ok": False, "error": "disabled"}
    rid = request.path_params.get("rid", "")
    body = await request.json()
    filename = f"{rid}.md"
    detail = self.recipes.detail(filename)
    if not detail:
        return {"ok": False, "error": "not found"}

    data = {}
    if "cron" in body:
        data["trigger_cron"] = (body.get("cron") or "").strip()
    if "enabled" in body:
        data["trigger_enabled"] = bool(body.get("enabled"))
    if not data:
        return {"ok": False, "error": "cron or enabled required"}

    res = self.recipes.update(filename, data)
    if res.get("error"):
        return {"ok": False, "error": res["error"]}

    record = {**detail, **data, "id": rid}
    self._sync_recipe_job(record)
    return {
        "ok": True,
        "trigger_cron": record.get("trigger_cron", ""),
        "trigger_enabled": bool(record.get("trigger_enabled")),
        "scheduled": self._recipe_scheduler().job_id(record) in self._recipe_scheduler().job_ids,
    }
