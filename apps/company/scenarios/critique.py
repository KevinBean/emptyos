"""Critique scenario — workers comment on a proposal from their role.

Output: per-worker comment + a synthesised digest + a simple vote tally.
Optional [DO:] proposals (e.g. a critique memo as a KB doc, or revision
tasks) flow through the same review gate as workshop — never autopilot.
"""

from __future__ import annotations

import re

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

CRITIQUE_USER_PROMPT = (
    "You are participating in a company-internal critique session.\n\n"
    "PROPOSAL UNDER REVIEW:\n"
    "{prompt}\n\n"
    "Respond strictly from your role. Cover:\n"
    "1) Your honest take (1-3 sentences).\n"
    "2) The strongest objection from where you sit.\n"
    "3) One specific change that would unblock your support.\n"
    "4) A single line at the very end: `VOTE: support` | `VOTE: against` | `VOTE: abstain`.\n\n"
    "If the critique naturally produces a deliverable (a revision task, a "
    "formal critique memo, a counter-draft), emit it as a `[DO:]` token. "
    "Don't force one — most critiques are pure commentary. When you do emit, "
    "the user reviews each [DO:] as a card and clicks Apply or Reject.\n\n"
    "{allowlist}"
)

DIGEST_SYSTEM = (
    "You are synthesising a multi-role critique of a proposal. Surface "
    "real disagreements, not consensus theater. End with one clear "
    "recommendation: SHIP, REVISE, or KILL — plus the single most "
    "important reason. Keep it under 200 words."
)

_VOTE_RE = re.compile(r"VOTE:\s*(support|against|abstain)", re.IGNORECASE)


def _extract_vote(text: str) -> str:
    if not text:
        return "abstain"
    m = _VOTE_RE.search(text)
    return (m.group(1).lower() if m else "abstain")


async def run(app, company: dict, workers: list[dict], prompt: str, mode: str) -> dict:
    timeout_s = float(app.setting("company.scenario_timeout_s", 120))
    parallel = bool(app.setting("company.parallel_fanout", True))

    run_id = new_run_id()
    started = now_iso()
    await app.emit("company:run_started", {
        "run_id": run_id, "scenario": "critique",
        "company_id": company.get("id"), "worker_count": len(workers),
    })

    suffix = await org_context_suffix(app, company)
    allowlist = render_deliverable_allowlist()

    responses = await fan_out_think(
        app,
        workers,
        build_prompt=lambda w: CRITIQUE_USER_PROMPT.format(prompt=prompt, allowlist=allowlist),
        system_suffix=suffix,
        parallel=parallel,
        timeout_s=timeout_s,
        temperature=0.4,  # analytical w/ personality — disagree authentically
    )
    # Strip [DO:] tokens BEFORE extracting the vote — the vote line lives
    # at the end of prose, and tokens elsewhere shouldn't bleed into it.
    all_pending = gate_responses(app, responses, run_id=run_id)
    for r in responses:
        r["vote"] = _extract_vote(r.get("response", ""))

    digest = await maybe_digest(
        app,
        title=f"Critique — {company.get('name', company.get('id'))}",
        responses=responses,
        prompt=prompt,
        system=DIGEST_SYSTEM,
    )

    tally = {"support": 0, "against": 0, "abstain": 0}
    for r in responses:
        tally[r.get("vote", "abstain")] = tally.get(r.get("vote", "abstain"), 0) + 1

    record = {
        "id": run_id,
        "scenario": "critique",
        "company_id": company.get("id"),
        "company_name": company.get("name"),
        "prompt": prompt,
        "mode": mode,
        "started": started,
        "completed": now_iso(),
        "responses": responses,
        "digest": digest,
        "tally": tally,
        "pending_count": len(all_pending),
    }
    save_run(app, record)
    await app.emit("company:run_complete", {
        "run_id": run_id, "scenario": "critique",
        "company_id": company.get("id"), "tally": tally,
    })
    return record
