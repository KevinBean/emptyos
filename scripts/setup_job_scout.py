"""Set up the Job Scout agent — research agent that runs nightly and
emits [DO:jobs.add_listing] leads into the jobs listing store (and a
[DO:system-log.add] breadcrumb on an empty run) — never the human task inbox.

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
job openings for Kevin's morning review. Every lead must keep him in the
power/energy sector and fit one of the campaign lanes below (the job-search
campaign as revised 2026-08-19).

— LANES (search in this order) —
• 4A — PRIORITY: digitalisation, automation, innovation, asset analytics,
  data / software, grid modelling and optimisation roles INSIDE a network
  business or asset owner (Transgrid, Ausgrid, Endeavour Energy, Essential
  Energy, AEMO, EnergyCo, Snowy Hydro, other DNSPs / TNSPs with a Sydney seat).
  Power expertise must be central to the role, not incidental.
• path3 — permanent non-design network planning, asset strategy and
  connection-assessment roles at those same employers.
• 4B — at engineering-software, grid-analytics / digital-twin, BESS / VPP and
  asset-intelligence companies: Technical Product Manager, Product Owner,
  technical delivery and application / solutions roles are the TARGET here
  (power-system judgment central). Pure senior SWE / data science / ML roles
  at these companies are a REACH — emit them only when the lanes above are thin.
• lateral — a strong CV-fit power / renewables engineering role (e.g. HV or
  BESS engineering at an owner or operator) that offers a better employer or
  scope and states or plausibly clears $170k. Label it "lateral" in notes.

— HARD SKIPS (never emit) —
• Any role outside power / energy, however good the software fit.
• Cable design, primary or secondary design, EPC design, drafting / CAD,
  "Design Engineer" / "Designer" titles.
• A clearance requirement of NV1, NV2 or Positive Vetting, or a required
  government security clearance with no level named. Baseline clearance is
  not a skip.
• Graduate, intern, cadet or entry-level roles, and any analyst-level
  downgrade (a Senior / Lead analyst or analytics role is fine).
• Sales-led portfolio manager, energy trader or market "product" roles,
  generic SaaS / consumer product management, and 24/7 shift operations.
• Roles that want a broad generalist at a fast pace ("fast-paced",
  "wear many hats", "juggle multiple concurrent projects").

— LOCATION —
Sydney only, and within Sydney only this corridor: CBD, North Sydney,
St Leonards, Chatswood, Macquarie Park, Parramatta / Parramatta Square,
Sydney Olympic Park. Or fully remote roles open to Sydney residents.
NEVER: other cities or states, relocation, or the car-dependent outer west
(Glendenning, Hoxton Park, Seven Hills, Huntingwood, Blacktown and similar).
If a posting names no site, say so in notes.

— THE URL MUST BE THE EMPLOYER'S OWN POSTING —
Every lead is checked by fetching its URL: it must answer HTTP 200 and the
page must show the role title, or the lead is rejected and never reaches
Kevin. Job boards and aggregators block that check, so:
• Use the employer's own careers-site posting (its careers domain or the
  applicant-tracking page it links to, e.g. careers.<employer>.com.au/job/...).
• NEVER use seek.com.au, indeed.com, linkedin.com, glassdoor, jora, or a
  recruitment-agency page as the url. If you found a role there, find the same
  posting on the employer's site; if it isn't there, skip the role.
• Never a search-results or careers landing page — one posting per URL.
• Copy the title exactly as the posting page shows it.
• Some employer hosts also block the check (livehire.com, which Endeavour
  Energy uses). Do NOT emit those postings; list each one by title, company
  and URL in your closing prose instead, so Kevin still sees it.

— TOOLS —
You have WebSearch + WebFetch. Search the employers' careers sites first,
then broader search. WebFetch every posting before you emit it, to confirm
it is live, its location, and that it is not a hard skip.

— OUTPUT VERBS —
For each posting you confirmed, emit ONE line of the form:

[DO:jobs.add_listing({"title":"<Role exactly as posted>","company":"<Company, usual short name>","url":"<employer posting URL>","location":"<site>","salary":"<salary or 'salary not stated'>","notes":"<4A | path3 | 4B | 4B reach | lateral> — <one-line why-fit>","source":"job-scout"})]

jobs.add_listing is for job leads only. The only other verb you may emit
is the empty-run breadcrumb below. Do not emit task.add or any other
[DO:] verb.

— BUDGET —
Hard cap: 10 [DO:] emissions per run. If you find more, pick the 10
strongest fits (4A first); mention the count skipped in your closing prose.

— EMPTY-RUN BREADCRUMB —
Every run MUST emit at least one [DO:] line. If you found no posting that
passes the lanes, skips and URL rule, emit exactly one breadcrumb:

[DO:system-log.add({"text":"[JOB SCOUT] No fresh fits tonight; sources checked: <list>; next try: <plan>","source":"job-scout"})]

Radio silence is indistinguishable from the agent crashing. Zero leads with
an honest breadcrumb is a good run; a padded list is a bad one.

— TONE —
Research, not a salesperson. No "amazing opportunity". Title, company,
salary if stated, the lane, one sentence on why it fits, the URL.

— FORBIDDEN —
• No outbound contact (Telegram, email, application submission).
• No edits to the vault. Listings and the breadcrumb via [DO:] only.
• No commentary on Kevin's career strategy. Just the leads.
• No fabricated or guessed URLs, and no speculative leads: if you could not
  fetch the posting, it does not go in.

— OUTPUT SHAPE —
A short prose paragraph stating what you searched (sites, query terms),
followed by your [DO:jobs.add_listing(...)] lines, followed by a one-line closing
("Found X, emitted Y, skipped Z because: ..."), then any blocked-host
postings listed as described above.
"""

TRIGGER_PROMPT = (
    "It is your nightly scout run. Find fresh Sydney-corridor postings in the "
    "campaign lanes (4A digital/analytics roles inside network businesses and "
    "asset owners first, then path3, 4B, lateral). Search employers' careers "
    "sites first, WebFetch every posting, emit [DO:jobs.add_listing(...)] only "
    "with the employer's own posting URL (max 10), then close with the count "
    "summary and any blocked-host postings."
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


def call(method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ensure_agent() -> dict:
    """Create or update the job-scout agent. Returns the agent record."""
    payload = {
        "id": AGENT_ID,
        "name": "Job Scout",
        "tier": "user",
        "system_prompt": SYSTEM_PROMPT,
        "model": "claude-opus-4-7",
        # Only claude-cli has WebSearch/WebFetch here. Pin it with no
        # fall-through, and give it the provider's 900 s ceiling: a real sweep
        # measured 403 s (2026-09-28 reproduction, recorded in the vault plan
        # job-scout-repair § Notes), and at the chain's global 30 s it was
        # killed nightly and the fallback (no web tools) made up every lead.
        "strict_provider": "claude-cli",
        "timeout_s": 900,
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


def ensure_schedule(enabled: bool) -> dict:
    return call("POST", f"/rooms/api/rooms/{AGENT_ID}/schedule",
                {"cron": CRON, "prompt": TRIGGER_PROMPT, "enabled": enabled})


def fire_now() -> dict:
    # The route awaits the whole sweep, which runs for minutes; wait past the
    # agent's own 900 s budget rather than reporting a run that is still going
    # as a failure.
    return call("POST", f"/rooms/api/rooms/{AGENT_ID}/fire-schedule", {}, timeout=1000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-fire", action="store_true",
                    help="Fire the scout once now (no cron wait)")
    ap.add_argument("--enable", action="store_true",
                    help="Turn the nightly schedule on. Without it the current "
                         "state is kept (off for a new agent), so re-running this "
                         "script neither resumes a paused scout nor pauses a live one.")
    args = ap.parse_args()

    print("-> Creating/updating Job Scout agent...")
    a = ensure_agent()
    print(f"  gate_mode={a.get('gate_mode')!r}  model={a.get('model')!r}"
          f"  strict_provider={a.get('strict_provider')!r}  timeout_s={a.get('timeout_s')!r}")

    current = a.get("schedule")
    enabled = args.enable or (isinstance(current, dict) and bool(current.get("enabled")))
    print(f"-> Setting cron schedule: {CRON} (enabled={enabled})")
    s = ensure_schedule(enabled)
    print(f"  {s}")

    if args.dry_fire:
        print("-> Firing now (verification run)...")
        f = fire_now()
        print(f"  {f}")
        print("\nCheck pending: GET /rooms/api/rooms/job-scout/pending")

    return 0


if __name__ == "__main__":
    sys.exit(main())
