#!/usr/bin/env python3
"""Static security scan of .claude/skills/ — the skill-intake half of skill-scan.

Skills are instructions + scripts an agent executes with real tool access; a
malicious or compromised skill is prompt injection with a delivery mechanism.
This scanner runs the vendored SkillSpector Stage-1 static pass
(``emptyos/sdk/skill_scan.py`` — regex pattern taxonomy + Python-AST behavioral
rules + additive risk score) over every skill directory and reports per-skill
risk bands. Sibling consumer: the Store marketplace scan-before-confirm step
(``apps/public/core/store/marketplace.py``). Design doc:
.claude/rules/skill-scan.md.

ADVISORY by design (registered gate=False in preflight): first-party skills
legitimately discuss API keys, subprocess calls, and "ignore X" phrasing, so
findings are a triage signal, not a verdict (per .claude/rules/audits.md —
don't ship a report where the heuristic fires on healthy targets as if it
were a bug list). Exit code = number of skills scoring in the high/critical
band (>= 51), which on a healthy tree should be 0.

Pure file I/O — does NOT import emptyos.kernel (no syslog handle; safe while
the daemon is up, .claude/rules/daemon-handling.md). ``emptyos.sdk.skill_scan``
is a pure stdlib module.

Usage::

    python scripts/check_skill_security.py             # human table
    python scripts/check_skill_security.py --json      # agent-cli envelope
    python scripts/check_skill_security.py --verbose   # per-finding detail
    python scripts/check_skill_security.py --path .claude/skills/foo  # one dir
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from emptyos.sdk.skill_scan import scan_dir  # noqa: E402 — pure module, no kernel

# Skills at/above this score (the "high" band floor) count toward the exit code.
ALERT_SCORE = 51


def _scan_skills(roots: list[Path]) -> list[dict]:
    rows = []
    for root in roots:
        if not root.is_dir():
            continue
        for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            if skill_dir.name.startswith("_"):  # _archive/ etc.
                continue
            report = scan_dir(skill_dir)
            rows.append({"skill": skill_dir.name, **report.to_dict()})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--path", default="", help="scan one directory instead of .claude/skills/")
    ap.add_argument("--json", action="store_true", help="agent-cli JSON envelope on stdout")
    ap.add_argument("--verbose", action="store_true", help="print every finding, not just the roll-up")
    args = ap.parse_args()

    if args.path:
        target = (REPO_ROOT / args.path) if not Path(args.path).is_absolute() else Path(args.path)
        report = scan_dir(target)
        rows = [{"skill": target.name, **report.to_dict()}]
    else:
        rows = _scan_skills([REPO_ROOT / ".claude" / "skills"])

    alerts = [r for r in rows if r["score"] >= ALERT_SCORE]

    if args.json:
        ok = not alerts
        print(json.dumps({
            "ok": ok,
            "code": "ok" if ok else "high_risk_skills",
            "message": f"{len(rows)} skills scanned, {len(alerts)} at/above the high band",
            "data": {"skills": rows, "alert_score": ALERT_SCORE},
        }))
        return len(alerts)

    flagged = [r for r in rows if r["findings"]]
    for r in sorted(flagged, key=lambda r: -r["score"]):
        mark = "✗" if r["score"] >= ALERT_SCORE else "·"
        print(f"{mark} {r['skill']:<40} {r['score']:>3}/100 {r['band']:<8} "
              f"{len(r['findings'])} findings")
        if args.verbose:
            for f in r["findings"]:
                print(f"    {f['severity']:<8} {f['rule_id']:<5} {f['name']} — "
                      f"{f['file']}:{f['line']}")
    print(f"{len(rows)} skills scanned · {len(flagged)} with findings · "
          f"{len(alerts)} at/above the high band (score >= {ALERT_SCORE})")
    return len(alerts)


if __name__ == "__main__":
    sys.exit(main())
