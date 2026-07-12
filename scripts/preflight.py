#!/usr/bin/env python3
"""scripts/preflight.py — run EmptyOS's static self-audit suite by scope.

The runtime aggregator for the graduated `check-*.py` / `*_audit.py` family
(see .claude/rules/self-audit-loops.md). Each check is treated as a black-box
subprocess (`python scripts/<script> [args]`) so the runner is robust to their
differing interfaces; this file owns only the *registry* (which scope each check
belongs to, whether its non-zero exit is a hard gate) and the orchestration.

Consumers, one source of truth:
  - the `/preflight` skill calls this for the static scans (judgment steps —
    git / daemon / bus / env — stay in the skill),
  - `scripts/release-public.py` can run `--scope release --gate-only`,
  - CI can run `--all`.

Usage:
  python scripts/preflight.py --scope ui,kb     # run the ui + kb checks
  python scripts/preflight.py --scope always    # default safe set
  python scripts/preflight.py --all             # every registered check
  python scripts/preflight.py --list            # show the registry
  python scripts/preflight.py --scope ui --gate-only   # only hard-gate checks

Exit code = number of gate-failing checks (0 = clean / advisory-only).
Scopes: always, ui, kb, vault, memory, topology, security, release.

Adding a check: append one row to CHECKS. No edits to the check script itself.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Registry of static, read-only, fast scanners. `gate=True` means a non-zero
# exit fails preflight (the hard floor). Heavy/live checks that need the *user's*
# daemon or a browser (check-clickable, ui_walk_audit) stay out of the static
# runner — they belong in release-public.py / dedicated invocations.
#
# One exception: check_snapshot_boot.py (release scope) spawns its own throwaway
# daemon on an ephemeral port with an isolated data dir + vault. It never touches
# :9000/:9001, and only runs in the release scope, where a ~30s boot is worth it.
CHECKS: list[dict] = [
    # always — cheap repo-wide hygiene
    {"script": "check-personal.py",        "scope": ["always", "security", "release"], "gate": True},
    {"script": "check-branding.py",        "scope": ["always", "security", "release"], "gate": True},
    {"script": "check-asyncio-blocking.py","scope": ["always", "security"],            "gate": False},
    {"script": "check-swallowed-exceptions.py","scope": ["always", "security"],         "gate": False},
    {"script": "check-vault-rmw-race.py",  "scope": ["always", "vault"],               "gate": False},
    {"script": "check_call_app_declared.py","scope": ["always", "release"],             "gate": True},
    {"script": "check_dark_flags.py",      "scope": ["always", "release"],             "gate": False},
    # A test hardcoding apps/<track>/<group>/<id> breaks on every promote/regroup
    # and surfaces as a pytest COLLECTION error — reddening the whole CI gate,
    # not one file. Deterministic (flags only paths that no longer resolve), so
    # it gates. Four historical breakages: explore, daily-brief, publish, devices.
    {"script": "check-test-app-paths.py",  "scope": ["always", "release"],             "gate": True},
    # topology — full dataflow graph audit (call_app/emit/capability edges +
    # dead listeners). Hard floor = dangling call_app; debt categories advisory.
    {"script": "dataflow_audit.py",        "scope": ["topology"],                      "gate": False},
    # event wiring — drift (listener with no emitter) + phantom (declared but
    # never emitted) are exit-code signals; dead events stay informational.
    {"script": "check_event_wiring.py",    "scope": ["always", "topology"],            "gate": False},
    # ui / frontend
    {"script": "check-contrast.py",        "scope": ["ui"],            "gate": True},
    {"script": "check-text-tokens.py",     "scope": ["ui"],            "gate": True},
    {"script": "check-design-md.py",       "scope": ["ui"],            "gate": True},
    {"script": "check-absolute.py",        "scope": ["ui"],            "gate": False},
    {"script": "check-attr-escaper.py",    "scope": ["ui", "security"],"gate": False},
    {"script": "check-app-nav.py",         "scope": ["ui"],            "gate": False},
    {"script": "check-ios-safe-area.py",   "scope": ["ui"],            "gate": False},
    {"script": "check-button-tips.py",     "scope": ["ui"],            "gate": False, "args": ["--strict"]},
    {"script": "check_ui_structure.py",    "scope": ["ui"],            "gate": False},
    {"script": "check_ui_consistency.py",  "scope": ["ui"],            "gate": False},
    # A hub.panel naming a renderer hub.js lacks paints a red "Unknown renderer"
    # box on the home screen; a duplicate panel id collides in the DOM and makes
    # lazy hydration fetch the wrong panel. Both are provable from the manifest +
    # hub.js RENDERERS map, and both are silent on a healthy tree, so they gate.
    # The ambient band (priority >= 150, 63 of 102 panels) is deliberate and only
    # ever printed as an advisory — gating it would fail on a healthy tree.
    {"script": "check_hub_panels.py",      "scope": ["ui", "release"], "gate": True},
    # An app's ⚙ panel hand-mirrors its manifest [provides.settings] schema, so a
    # new setting silently reaches /settings and nowhere else. Advisory: an
    # omission can be deliberate (dark flag, secret) — mark it with
    # `settings-panel-drift: ignore <key>`, or pass `app:` to derive the fields.
    {"script": "check-settings-panel-drift.py", "scope": ["ui"],       "gate": False},
    # export-surface inline handlers must stay inside the eos-csp-bridge
    # grammar (MV3 extension packaging) — needs node; skips when absent
    {"script": "check-csp-inline.py",      "scope": ["ui", "export"],  "gate": False},
    # kb / engineering notes (EmptyOS KB + the personal vault KB corpus)
    {"script": "kb_claim_audit.py",        "scope": ["kb"],            "gate": False},
    {"script": "kb_link_audit.py",         "scope": ["kb"],            "gate": False},
    {"script": "kb_claim_audit.py",        "scope": ["kb"],            "gate": False,
     "args": ["--root", "30_Resources/KB"], "label": "kb_claim_audit (personal)"},
    {"script": "kb_link_audit.py",         "scope": ["kb"],            "gate": False,
     "args": ["--root", "30_Resources/KB"], "label": "kb_link_audit (personal)"},
    # vault hygiene
    {"script": "check_vault_test_leak.py", "scope": ["vault"],         "gate": False},
    {"script": "check_vault_structure.py", "scope": ["vault"],         "gate": False},
    # session memory hygiene (Claude-Code auto-memory)
    {"script": "check_memory_rot.py",      "scope": ["memory"],        "gate": False},
    # skill-authoring contract (frontmatter trigger + boundary + preflight)
    # release: repo store only (a release must not gate on the machine's personal skills)
    {"script": "check_skills.py",          "scope": ["release"],       "gate": True},
    # skills scope: also lint the user-global store (~/.claude/skills); skipped on a
    # clone with no user skills (the flag no-ops when the dir is absent)
    {"script": "check_skills.py",          "scope": ["skills"],        "gate": True,
     "args": ["--user-skills"], "label": "check_skills (+ user store)"},
    # skill-content security scan (vendored SkillSpector static pass — advisory)
    {"script": "check_skill_security.py",  "scope": ["skills", "security"], "gate": False},
    # docs — generated docs must match the code (APPS.md, TIERS.md, SKILLS.md)
    {"script": "generate_apps_doc.py",  "scope": ["docs", "release"], "gate": True, "args": ["--check"]},
    {"script": "generate_tiers_doc.py", "scope": ["docs", "release"], "gate": True, "args": ["--check"]},
    {"script": "generate_skills_doc.py", "scope": ["docs", "skills", "release"], "gate": True,
     "args": ["--check"]},
    # apps — whole-system 8-dimension quality scorecard (App Optimizer). Advisory;
    # --check prints the summary without writing a vault snapshot.
    {"script": "app_optimizer_scan.py", "scope": ["apps"], "gate": False, "args": ["--check"]},
    # apps — [storage] manifest-declaration hygiene (docs/CLOUD-ARCHITECTURE.md);
    # advisory: exit = invalid declarations only, undeclared apps are fine.
    {"script": "check_storage_decl.py", "scope": ["apps"], "gate": False},
    # apps — AI-native scorecard (backend think vs assistant reach vs page AI-UI);
    # advisory: exit = "dark AI" apps (think but no chip + no reach). Judgment
    # layer / triage = the eos-ai-native-audit skill; exceptions go in DARK_OK.
    {"script": "check_ai_native.py", "scope": ["apps"], "gate": False},
    # apps — market gap-analysis registry coverage/staleness (vault
    # 30_Resources/EmptyOS/gap-analysis/). Advisory: exit = stale + missing
    # (capped 99); judgment layer = the eos-app-gap-analysis skill.
    {"script": "check_gap_freshness.py", "scope": ["apps"], "gate": False},
    # release packaging
    {"script": "check-tier-folder.py",     "scope": ["release"],       "gate": True},
    {"script": "check-licenses.py",        "scope": ["release"],       "gate": False},
    # Compile every module, then actually boot the tree in a throwaway daemon.
    # The only check here that runs the code rather than reading it — the class
    # that cost releases v0.2.7-v0.2.10 (compiles clean, dies on a fresh boot).
    # Boot capped below the runner's 120s per-check timeout.
    {"script": "check_snapshot_boot.py",   "scope": ["release"],       "gate": True,
     "args": ["--timeout", "60"]},
]

ALL_SCOPES = ["always", "ui", "kb", "vault", "memory", "skills", "docs", "topology", "apps", "security", "release"]


def _select(scopes: set[str], gate_only: bool) -> list[dict]:
    out = []
    for c in CHECKS:
        if not (scopes & set(c["scope"])):
            continue
        if gate_only and not c.get("gate"):
            continue
        if not (REPO / "scripts" / c["script"]).exists():
            continue
        out.append(c)
    return out


def _run_one(c: dict, timeout: int) -> dict:
    script = c["script"]
    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, str(REPO / "scripts" / script), *c.get("args", [])],
            cwd=REPO, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",  # children emit UTF-8; don't let cp1252 mojibake it
        )
        rc = r.returncode
        tail = (r.stdout or r.stderr or "").strip().splitlines()
        summary = tail[-1] if tail else ""
    except subprocess.TimeoutExpired:
        return {**c, "rc": -1, "state": "error", "summary": f"timeout >{timeout}s", "dt": timeout}
    except Exception as e:  # never let one check crash the runner
        return {**c, "rc": -1, "state": "error", "summary": str(e)[:80], "dt": round(time.time() - t0, 1)}
    if rc == 0:
        state = "ok"
    elif c.get("gate"):
        state = "FAIL"
    else:
        state = "warn"
    return {**c, "rc": rc, "state": state, "summary": summary[:90], "dt": round(time.time() - t0, 1)}


_MARK = {"ok": "✓", "warn": "·", "FAIL": "✗", "error": "!"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run EmptyOS static self-audit checks by scope.")
    ap.add_argument("--scope", default="always", help="comma-separated: " + ", ".join(ALL_SCOPES))
    ap.add_argument("--all", action="store_true", help="run every registered check")
    ap.add_argument("--gate-only", action="store_true", help="only hard-gate checks")
    ap.add_argument("--list", action="store_true", help="list the registry and exit")
    ap.add_argument("--timeout", type=int, default=120, help="per-check timeout seconds")
    args = ap.parse_args()

    if args.list:
        print(f"{'check':28} {'scope':28} gate")
        for c in CHECKS:
            print(f"  {c.get('label', c['script']):26} {','.join(c['scope']):28} {'gate' if c.get('gate') else '-'}")
        return 0

    scopes = set(ALL_SCOPES) if args.all else {s.strip() for s in args.scope.split(",") if s.strip()}
    bad = scopes - set(ALL_SCOPES)
    if bad:
        print(f"unknown scope(s): {', '.join(sorted(bad))}; valid: {', '.join(ALL_SCOPES)}", file=sys.stderr)
        return 2
    selected = _select(scopes, args.gate_only)
    if not selected:
        print(f"preflight: no checks for scope {sorted(scopes)}")
        return 0

    print(f"preflight — scope {sorted(scopes)} · {len(selected)} checks\n")
    fails = warns = 0
    for c in selected:
        res = _run_one(c, args.timeout)
        if res["state"] == "FAIL":
            fails += 1
        elif res["state"] in ("warn", "error"):
            warns += 1
        print(f"  {_MARK[res['state']]} {res.get('label', res['script']):26} {res['state']:5} {res['dt']:>5}s  {res['summary']}")
    print(f"\n{len(selected)} checks · {fails} FAIL · {warns} warn/err")
    if fails:
        print("Hard-gate check(s) failed — see .claude/rules/self-audit-loops.md + audits.md.")
    return fails


if __name__ == "__main__":
    sys.exit(main())
