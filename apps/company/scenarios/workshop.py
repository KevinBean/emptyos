"""Workshop scenario — workers draft work products against a task.

Output: per-worker draft + extracted [DO:] proposals (review-gate cards).
Workers may emit `[DO:app.method({...})]` tokens for state-changing
follow-ups; the parser strips them from the response text and stores
them as pending actions. The user clicks Apply/Reject — never autopilot.
Mirrors the rooms review-gate contract (.claude/rules/room-review-gate.md).

The [DO:] extraction lives in scenarios/base.py so critique + interview
can use the same gate.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import (
    extract_do_actions,
    fan_out_think,
    gate_responses,
    maybe_digest,
    new_run_id,
    now_iso,
    org_context_suffix,
    pending_dir,
    render_deliverable_allowlist,
    save_pending,
    save_run,
)

WORKSHOP_USER_PROMPT = (
    "You are participating in a company workshop. The team has been asked to:\n\n"
    "{prompt}\n\n"
    "Your job: produce a concrete deliverable from your role's perspective. "
    "Depth over breadth.\n\n"
    "The work product IS a [DO:] call. Your reasoning belongs in the body of "
    "your reply; the actual artifact (draft post, deck plan, KB doc, task "
    "list) belongs inside one or more `[DO:app.method({{\"arg\":\"value\"}})]` "
    "tokens. Examples:\n"
    "  - A designer-marketer emits [DO:ppt.api_plan({{...}})] for a deck, not "
    "a slide outline typed in prose.\n"
    "  - A growth lead emits [DO:publish.api_save_draft({{...}})] for a blog "
    "post, not a draft pasted in chat.\n"
    "  - A PMM emits [DO:kb.api_doc_create({{...}})] for a positioning memo.\n"
    "The user reviews each [DO:] as a card and clicks Apply or Reject — never "
    "describe an artifact you would create and skip the token.\n\n"
    "{allowlist}"
)


DIGEST_SYSTEM = (
    "You are integrating multiple role-perspective drafts into a single "
    "coherent work product. Preserve the strongest specifics from each "
    "draft; do not paper over real disagreements. Keep under 300 words."
)


# Back-compat aliases — older test code may import these from workshop.
def _pending_dir(app) -> Path:
    return pending_dir(app)


def _save_pending(app, action: dict) -> None:
    save_pending(app, action)


def _extract_do_actions(text: str, *, run_id: str, source: dict):
    return extract_do_actions(text, run_id=run_id, source=source)


async def run(app, company: dict, workers: list[dict], prompt: str, mode: str) -> dict:
    timeout_s = float(app.setting("company.scenario_timeout_s", 120))
    parallel = bool(app.setting("company.parallel_fanout", True))

    run_id = new_run_id()
    started = now_iso()
    await app.emit("company:run_started", {
        "run_id": run_id, "scenario": "workshop",
        "company_id": company.get("id"), "worker_count": len(workers),
    })

    suffix = await org_context_suffix(app, company)

    allowlist = render_deliverable_allowlist()
    responses = await fan_out_think(
        app,
        workers,
        build_prompt=lambda w: WORKSHOP_USER_PROMPT.format(prompt=prompt, allowlist=allowlist),
        system_suffix=suffix,
        parallel=parallel,
        timeout_s=timeout_s,
        temperature=0.5,  # drafting work — some creative range
    )

    all_pending = gate_responses(app, responses, run_id=run_id)

    digest = await maybe_digest(
        app,
        title=f"Workshop — {company.get('name', company.get('id'))}",
        responses=responses,
        prompt=prompt,
        system=DIGEST_SYSTEM,
    )

    record = {
        "id": run_id,
        "scenario": "workshop",
        "company_id": company.get("id"),
        "company_name": company.get("name"),
        "prompt": prompt,
        "mode": mode,
        "started": started,
        "completed": now_iso(),
        "responses": responses,
        "digest": digest,
        "pending_count": len(all_pending),
    }
    save_run(app, record)
    await app.emit("company:run_complete", {
        "run_id": run_id, "scenario": "workshop",
        "company_id": company.get("id"), "pending_count": len(all_pending),
    })
    return record


def load_pending(app, action_id: str) -> dict | None:
    p = _pending_dir(app) / f"{action_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_pending(app, run_id: str = "", status: str = "") -> list[dict]:
    out: list[dict] = []
    for f in _pending_dir(app).glob("act-*.json"):
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if run_id and a.get("run_id") != run_id:
            continue
        if status and a.get("status") != status:
            continue
        out.append(a)
    out.sort(key=lambda x: x.get("ts", ""))
    return out


async def apply_pending(app, action_id: str) -> dict:
    action = load_pending(app, action_id)
    if not action:
        return {"error": "action not found"}
    if action.get("status") != "pending":
        return {"error": f"already {action.get('status')}"}
    try:
        result = await app.call_app(action["app"], action["method"], **(action.get("args") or {}))
    except Exception as e:
        action["status"] = "failed"
        action["error"] = str(e)[:200]
        action["resolved_ts"] = now_iso()
        _save_pending(app, action)
        return {"error": str(e)[:200], "action": action}
    action["status"] = "applied"
    action["result"] = str(result)[:500]
    action["resolved_ts"] = now_iso()
    _save_pending(app, action)
    await app.emit("company:action_applied", {
        "action_id": action_id, "run_id": action.get("run_id"),
        "app": action["app"], "method": action["method"],
    })
    return action


async def reject_pending(app, action_id: str) -> dict:
    action = load_pending(app, action_id)
    if not action:
        return {"error": "action not found"}
    if action.get("status") != "pending":
        return {"error": f"already {action.get('status')}"}
    action["status"] = "rejected"
    action["resolved_ts"] = now_iso()
    _save_pending(app, action)
    await app.emit("company:action_rejected", {
        "action_id": action_id, "run_id": action.get("run_id"),
    })
    return action
