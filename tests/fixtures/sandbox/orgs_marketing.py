"""Sandbox seed: `emptyos` org with 4 AI marketing personas.

Idempotent — checks for existing entities, only creates what's missing.

Usage:
    python tests/fixtures/sandbox/orgs_marketing.py http://127.0.0.1:9002

Returns JSON describing what was created vs already-present. Designed for
Claude-driven test sessions per `.claude/rules/sandbox-driven-testing.md`.

Note the filename uses an underscore (`orgs_marketing`) for importability;
the test scenario is conceptually `orgs-marketing`.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


def _http(host: str, method: str, path: str, body: dict | None = None) -> dict:
    url = host.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode("utf-8", errors="replace")[:500]}
    except Exception as e:
        return {"error": str(e)[:200]}


ORG = {
    "name": "emptyos",
    "kind": "side-business",
    "reality": "real",
    "scope": "member",
    "mission": (
        "Build EmptyOS into the local mind-companion OS that "
        "notes-and-agent tinkerers reach for first, without paid ads."
    ),
    "vision": (
        "Personal computing as 'with you, not for you' — every user owns "
        "their vault, their kernel, their agents."
    ),
    "values": (
        "with-you-not-for-you, vault-as-truth, ship-weekly, "
        "kill-features-that-dont-earn-their-keep, no-autopilot"
    ),
    "culture": (
        "Direct, written-first. Personas disagree authentically. "
        "Marketing claims clear the Mira-bar: 'who actually feels this "
        "pain enough to switch?'"
    ),
    "roles": "narrative-strategist, indie-gtm, skeptical-pmm, designer-marketer",
}

PERSONAS = [
    {
        "name": "Iris",
        "role": "Narrative Strategist",
        "emoji": "\U0001F4D6",  # 📖
        "system_prompt": (
            "You are Iris, a narrative strategist. You frame products as "
            "stories. You obsess over positioning — 'who is this for, "
            "against what alternative, and why does it matter NOW?'. "
            "Distrust feature lists. Push back on jargon. Offer concrete "
            "tagline candidates when relevant."
        ),
    },
    {
        "name": "Reza",
        "role": "Indie GTM",
        "emoji": "\U0001F680",  # 🚀
        "system_prompt": (
            "You are Reza, an indie-hacker go-to-market lead. You ship in "
            "public — X, HN, Reddit. You believe the first 100 users decide "
            "whether anything compounds. You think in concrete distribution "
            "moves: 'this week, this channel, this asset'. A working demo + "
            "thread beats a landing page."
        ),
    },
    {
        "name": "Mira",
        "role": "Skeptical PMM",
        "emoji": "\U0001F50D",  # 🔍
        "system_prompt": (
            "You are Mira, a skeptical product marketing manager. You've "
            "shipped marketing for products that died. You red-team every "
            "claim. Ask 'who actually feels this pain enough to switch?' "
            "and 'what's the competing alternative?'. Demand sharp ICPs, "
            "not 'everyone who uses computers'. Disagree authentically."
        ),
    },
    {
        "name": "Kade",
        "role": "Designer-Marketer",
        "emoji": "\U0001F3A8",  # 🎨
        "system_prompt": (
            "You are Kade, a designer who runs marketing. The product *is* "
            "the marketing — screenshots, demo videos, the README hero, the "
            "first 10 seconds of a recording. Evaluate every plan against "
            "'what would a viewer see in the first 5 seconds?'. Push for "
            "concrete visual assets."
        ),
    },
]


def seed(host: str) -> dict:
    """Seed the `emptyos` org + 4 marketing personas. Idempotent.

    Returns a report describing what was created vs already-present.
    Aborts on the first hard error from the daemon (e.g. plugin missing).
    """
    report: dict = {"host": host, "org": None, "personas": [], "errors": []}

    # 1) Probe — confirm orgs app is reachable.
    listing = _http(host, "GET", "/orgs/api/orgs")
    if "orgs" not in listing:
        report["errors"].append({"step": "list_orgs", "detail": listing})
        return report
    existing_org_ids = {o.get("id") for o in (listing.get("orgs") or [])}

    # 2) Create the org if missing.
    if "emptyos" in existing_org_ids:
        report["org"] = {"id": "emptyos", "action": "skipped"}
    else:
        created = _http(host, "POST", "/orgs/api/orgs", ORG)
        if created.get("id") == "emptyos":
            report["org"] = {"id": "emptyos", "action": "created"}
        else:
            report["errors"].append({"step": "create_org", "detail": created})
            return report

    # 3) Add personas (skip duplicates by id).
    members_q = _http(
        host, "GET", "/orgs/api/memberships?org_id=emptyos&mode=ai",
    )
    existing_member_ids = {m.get("id") for m in (members_q.get("memberships") or [])}
    for p in PERSONAS:
        member_id = f"emptyos-{p['name'].lower()}"
        if member_id in existing_member_ids:
            report["personas"].append({"id": member_id, "action": "skipped"})
            continue
        added = _http(
            host, "POST", "/orgs/api/members",
            {
                "org_id": "emptyos",
                "mode": "ai",
                "name": p["name"],
                "role": p["role"],
                "emoji": p["emoji"],
                "system_prompt": p["system_prompt"],
                "model": "",  # default chain (caller's think_providers override decides)
            },
        )
        if added.get("id"):
            report["personas"].append(
                {"id": added["id"], "action": "created", "role": p["role"]},
            )
        else:
            report["errors"].append(
                {"step": "add_member", "name": p["name"], "detail": added},
            )

    report["ok"] = not report["errors"]
    return report


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9002"
    result = seed(host)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result.get("ok") else 1)
