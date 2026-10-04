#!/usr/bin/env bash
# check_done.sh — objective gate for the autonomous fix loop.
#
# Exits 0 ONLY when the objective is fully met:
#   (1) every HARD-GATE static scanner in the chosen preflight scope passes, and
#   (2) every regression test this loop added still passes.
#
# Both halves are required. (1) alone would let a fix regress silently; (2)
# alone would let the tree drift. Advisory (non-gate) scanners are deliberately
# NOT part of the objective — per .claude/rules/audits.md an ambiguous signal
# must never gate, and those carry known false positives.
#
# Usage:  ./check_done.sh          # human-readable
#         ./check_done.sh --quiet  # exit code only
#
# Override the scope with EOS_DONE_SCOPE=... (comma-separated preflight scopes).

set -uo pipefail
cd "$(dirname "$0")" || exit 2

# `release` is in the default set because the objective named it and its gates
# are otherwise invisible: they only run at release time, so four hard-gate
# failures sat green-by-omission. One of them -- apps/public/labs/operate never
# appearing in [tiers.labs].apps -- had been red since the app was created on
# 2026-07-16, 1115 commits back. A scope that never runs is not a passing gate.
SCOPE="${EOS_DONE_SCOPE:-always,apps,ui,security,tests,topology,skills,release}"
TESTS_MANIFEST="${EOS_DONE_TESTS:-.loop-regression-tests.txt}"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

log() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }

fail=0

# ---------------------------------------------------------------- (1) scanners
# preflight's exit code IS the number of gate-failing checks. Capture it
# directly -- never through a pipe, because in a pipeline $? reports the LAST
# command (tail/grep), which silently masks the failure. That exact bug
# reported a clean tree while two hard gates were red.
log "== (1) hard-gate scanners  [scope: $SCOPE] =="
scan_out="$(mktemp)"
python scripts/preflight.py --scope "$SCOPE" --gate-only >"$scan_out" 2>&1
scan_rc=$?
if [ "$scan_rc" -ne 0 ]; then
    fail=1
    log "   FAIL — $scan_rc gate check(s) failing:"
    # Never report a count without naming the checks. If the marker scan comes
    # up empty -- a check that errored rather than failed, or a change to
    # preflight's output format -- fall back to the raw tail, so the failure is
    # always actionable. Observed once: "FAIL - 1 gate check(s) failing:"
    # followed by nothing, which is the least useful thing a gate can say.
    detail=$(grep -E '^\s*✗' "$scan_out" || true)
    [ -n "$detail" ] || detail=$(tail -15 "$scan_out")
    log "$(printf '%s\n' "$detail" | sed 's/^/     /')"
else
    log "   PASS — 0 gate checks failing"
fi
rm -f "$scan_out"

# --------------------------------------------------- (2) loop regression tests
# One pytest node id per line; blank lines and #comments ignored. Absent or
# empty manifest = nothing added yet, which is a pass, not a failure.
log "== (2) regression tests added by this loop =="
if [ ! -s "$TESTS_MANIFEST" ]; then
    log "   PASS — no regression tests registered yet ($TESTS_MANIFEST empty/absent)"
else
    node_ids=$(grep -vE '^\s*(#|$)' "$TESTS_MANIFEST" || true)
    if [ -z "$node_ids" ]; then
        log "   PASS — manifest has no active entries"
    else
        n=$(printf '%s\n' "$node_ids" | wc -l | tr -d ' ')
        test_out="$(mktemp)"
        # shellcheck disable=SC2086
        python -m pytest $node_ids -q --no-header >"$test_out" 2>&1
        test_rc=$?
        if [ "$test_rc" -ne 0 ]; then
            fail=1
            log "   FAIL — regression suite red ($n registered):"
            tail -12 "$test_out" | sed 's/^/     /'
        else
            log "   PASS — $n regression test(s) green"
        fi
        rm -f "$test_out"
    fi
fi

log ""
if [ "$fail" -eq 0 ]; then
    log "OBJECTIVE MET — gates clean, regressions green."
else
    log "NOT DONE — see failures above."
fi
exit "$fail"
