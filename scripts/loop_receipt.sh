#!/usr/bin/env bash
# scripts/loop_receipt.sh — append-only receipt log for the autonomous fix loop.
#
# Append-only by construction: the ONLY write is `>>`. No read-modify-write, no
# truncation, no rewrite path. Safe against a parallel session appending too
# (CLAUDE.md § Session Housekeeping).
#
# Usage: scripts/loop_receipt.sh <iteration> <status> <one-line summary>
#   e.g. scripts/loop_receipt.sh 1 fixed "check-personal: employer name out of tracked files"

set -euo pipefail
cd "$(dirname "$0")/.." || exit 2

LOG="${EOS_LOOP_DEVLOG:-docs/loop-devlog.md}"

if [ "$#" -lt 3 ]; then
    echo "usage: $0 <iteration> <status> <summary...>" >&2
    exit 2
fi

iter="$1"; status="$2"; shift 2; summary="$*"
ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# Header written once, and only when the file does not yet exist.
if [ ! -e "$LOG" ]; then
    mkdir -p "$(dirname "$LOG")"
    {
        echo "# Autonomous fix-loop receipts"
        echo
        echo "One line per iteration. Append-only — see scripts/loop_receipt.sh."
        echo
        echo "| # | UTC | status | receipt |"
        echo "|---|---|---|---|"
    } >> "$LOG"
fi

printf '| %s | %s | %s | %s |\n' "$iter" "$ts" "$status" "$summary" >> "$LOG"
echo "receipt logged -> $LOG"
