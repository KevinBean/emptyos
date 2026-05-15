"""Interview scenario — Q&A with one worker at a time.

Output: per-worker answer to the same question, presented as a panel.
Useful for "ask a customer + an investor + a skeptic the same question"
shaped prompts. Optional [DO:] proposals (interview write-up, follow-up
tasks, formal interview report) flow through the same review gate as
workshop — never autopilot.
"""

from __future__ import annotations

from .base import (
    fan_out_think,
    gate_responses,
    maybe_digest,
    new_run_id,
    now_iso,
    org_context_suffix,
    render_deliverable_allowlist,
    save_run,
)

INTERVIEW_USER_PROMPT = (
    "You are answering an interview question, strictly from your role's "
    "perspective. Do not break character. Answer in 3-6 sentences.\n\n"
    "QUESTION:\n{prompt}\n\n"
    "Be specific and concrete. If a deliverable follows naturally from "
    "your answer (a follow-up task, an interview write-up, a formal "
    "report), emit it as a `[DO:]` token at the end of your reply. Don't "
    "force one — interviews are mostly prose. When you do emit, the user "
    "reviews each [DO:] as a card and clicks Apply or Reject.\n\n"
    "{allowlist}"
)

DIGEST_SYSTEM = (
    "You are summarising a panel interview. For each respondent, capture "
    "the single most-distinctive line of their answer. End with a one-line "
    "synthesis of what they agreed on. Keep under 150 words."
)


async def run(app, company: dict, workers: list[dict], prompt: str, mode: str) -> dict:
    timeout_s = float(app.setting("company.scenario_timeout_s", 120))
    # Sequential by default — interviews read better in order.
    parallel = bool(app.setting("company.parallel_fanout", True))

    run_id = new_run_id()
    started = now_iso()
    await app.emit("company:run_started", {
        "run_id": run_id, "scenario": "interview",
        "company_id": company.get("id"), "worker_count": len(workers),
    })

    suffix = await org_context_suffix(app, company)
    allowlist = render_deliverable_allowlist()

    responses = await fan_out_think(
        app,
        workers,
        build_prompt=lambda w: INTERVIEW_USER_PROMPT.format(prompt=prompt, allowlist=allowlist),
        system_suffix=suffix,
        parallel=parallel,
        timeout_s=timeout_s,
        temperature=0.4,  # in-character but consistent
    )
    all_pending = gate_responses(app, responses, run_id=run_id)

    digest = await maybe_digest(
        app,
        title=f"Interview — {company.get('name', company.get('id'))}",
        responses=responses,
        prompt=prompt,
        system=DIGEST_SYSTEM,
    )

    record = {
        "id": run_id,
        "scenario": "interview",
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
        "run_id": run_id, "scenario": "interview", "company_id": company.get("id"),
    })
    return record
