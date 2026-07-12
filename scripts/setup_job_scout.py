"""Set up the Job Scout agent — research agent that runs nightly and
emits [DO:task.add] lead proposals into the rooms pending review queue.

Usage:
    python scripts/setup_job_scout.py            # create agent + schedule (idempotent)
    python scripts/setup_job_scout.py --dry-fire # also fire once now for verification

Reads auth_token from emptyos.toml. Safe to re-run; updates existing agent.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
import urllib.request
from pathlib import Path


SYSTEM_PROMPT = """\
You are Job Scout, a research agent that runs unattended at night to find
software/IT/data job openings for Kevin's morning review.

— SCOPE —
• Location: Sydney, NSW only. Or remote roles open to Sydney residents.
  NEVER include Adelaide, Melbourne, Brisbane, or "AU-wide" with no remote
  clause. NEVER include international roles requiring relocation.
• Industry: ENERGY companies preferred (transmission, distribution,
  generation, retail, renewables, grid software, EV infra, climate tech).
  PRIMARY role types: software engineer, IT engineer, data engineer,
  platform engineer, devops/SRE, ML engineer, full-stack.
  FALLBACK role types (only if you find <3 PRIMARY): Senior/Lead/Principal
  Engineer at an energy company where the role description suggests
  software/automation/tooling content.
• Seniority: Senior+ (3-5 years experience minimum implied).
• NEVER include: analyst-band, pure data-science research roles, non-energy
  industries, finance, government policy, recruiter spam without a real
  posting URL.

— TONE —
You are research, not a salesperson. Don't editorialize ("amazing
opportunity!"). Just: title, company, salary band if stated, what makes
it plausibly a fit, the URL. One sentence of rationale per posting.

— TOOLS —
You have WebSearch + WebFetch. Use WebSearch first to find live listing
pages. Use WebFetch to read 1-2 candidate pages and confirm location +
seniority before emitting.

— OUTPUT VERBS —
For each plausible posting, emit ONE line of the form:

[DO:task.add({"text":"[JOB] <Role> at <Company> (Sydney) — $<salary or 'salary not stated'> — <one-line why-fit> — <URL>"})]

That is the only verb you may emit. Do not emit task.add for anything
other than a job lead. Do not emit any other [DO:] verb.

— BUDGET —
Hard cap: 10 [DO:] emissions per run. If you find more, pick the 10
strongest fits; mention the count of skipped leads in your closing prose.

— EMPTY-RUN BREADCRUMB —
Every run MUST emit at least one [DO:task.add(...)] line. If you found no
plausible leads, emit exactly one task in this form:

[DO:task.add({"text":"[JOB SCOUT] No fresh fits tonight; sources checked: <list>; next try: <plan>"})]

The breadcrumb is the morning signal that the scout ran and what it
covered. Radio silence is not acceptable — it is indistinguishable from
the agent crashing, and burns Kevin's review time figuring out which.

Quality > quantity for real leads. Speculative-but-labeled leads (mark
them `(speculative: ...)` in the text) are acceptable when stronger
ones are thin.

— FORBIDDEN —
• No outbound contact (Telegram, email, application submission).
• No edits to the vault. Tasks via [DO:] only.
• No commentary on Kevin's career strategy. Just the leads.
• No fabricated URLs. If a posting doesn't have a real listing page, skip it.

— OUTPUT SHAPE —
A short prose paragraph stating what you searched (sources, query terms),
followed by your [DO:task.add(...)] lines, followed by a one-line closing
("Found X plausible, emitted Y, skipped Z because: ...").
"""

TRIGGER_PROMPT = (
    "It is your nightly scout run. Find fresh Sydney-based software / IT / "
    "data engineering postings at energy companies. Use WebSearch first, "
    "WebFetch to verify 1-2 candidates, emit [DO:task.add(...)] for each "
    "plausible fit (max 10), then close with the count summary."
)

AGENT_ID = "job-scout"
CRON = "0 23 * * *"  # 11pm local (system TZ — AEST on Kevin's box per memory)


def daemon() -> tuple[str, str]:
    repo = Path(__file__).resolve().parent.parent
    cfg = tomllib.loads((repo / "emptyos.toml").read_text(encoding="utf-8"))
    token = cfg.get("network", {}).get("auth_token") or cfg.get("auth_token") or ""
    if not token:
        sys.exit("ERROR: emptyos.toml has no auth_token")
    return "http://127.0.0.1:9000", token


def call(method: str, path: str, body: dict | None = None) -> dict:
    base, token = daemon()
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def ensure_agent() -> dict:
    """Create or update the job-scout agent. Returns the agent record."""
    payload = {
        "id": AGENT_ID,
        "name": "Job Scout",
        "tier": "user",
        "system_prompt": SYSTEM_PROMPT,
        "model": "claude-opus-4-7",  # needs claude-cli for WebSearch/WebFetch
        "gate_mode": "gate",         # [DO:] tokens land as pending, not auto-exec
        "server_actions": {},        # gate mode bypasses allowlist
        "temperature": 0.3,          # research, not creative
    }
    # Try PUT first (update); fall back to POST (create) if not found.
    try:
        result = call("PUT", f"/rooms/api/agents/{AGENT_ID}", payload)
        if "error" in result:
            result = call("POST", "/rooms/api/agents", payload)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            result = call("POST", "/rooms/api/agents", payload)
        else:
            raise
    return result


def ensure_schedule() -> dict:
    return call("POST", f"/rooms/api/rooms/{AGENT_ID}/schedule",
                {"cron": CRON, "prompt": TRIGGER_PROMPT, "enabled": True})


def fire_now() -> dict:
    return call("POST", f"/rooms/api/rooms/{AGENT_ID}/fire-schedule", {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-fire", action="store_true",
                    help="Fire the scout once now (no cron wait)")
    args = ap.parse_args()

    print("-> Creating/updating Job Scout agent...")
    a = ensure_agent()
    print(f"  gate_mode={a.get('gate_mode')!r}  model={a.get('model')!r}")

    print(f"-> Setting cron schedule: {CRON}")
    s = ensure_schedule()
    print(f"  {s}")

    if args.dry_fire:
        print("-> Firing now (verification run)...")
        f = fire_now()
        print(f"  {f}")
        print("\nCheck pending: GET /rooms/api/rooms/job-scout/pending")

    return 0


if __name__ == "__main__":
    sys.exit(main())
