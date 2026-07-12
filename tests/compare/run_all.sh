#!/usr/bin/env bash
# Don't set -e: one failed question shouldn't abort the rest of the suite.
cd "$(dirname "$0")"
PROV="${1:-openai-mini}"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

declare -A QS
QS[q1]="List every app in EmptyOS (both apps/ and apps/personal/) and give a one-sentence purpose for each. Keep the total under 400 words."
QS[q2]="Which plugins in EmptyOS register service names that apps can call via self.require()? List the plugin name and its service name(s). Keep it under 200 words."
QS[q5]="Explain the cloud consent gate in EmptyOS in 3 sentences: what triggers it, what the user sees, and which file owns the decision."
QS[q6]="There is a known vault read-modify-write race in EmptyOS. What pattern fixes it, which app is the reference implementation, and why are event emits placed outside the lock?"
QS[q4]="Find all vault notes tagged 'job-application' (modified in the last 365 days if possible) and show company names plus their status. Do not invent companies — list what you actually find, or say 'none found'."
QS[q7]="The PRICING dict in emptyos/capabilities/providers/openai_compat.py stores (input, output) per model. It doesn't capture OpenAI's priority-tier pricing (e.g. gpt-5.4 standard is 2.50/15.00 but priority is 5.00/22.50). Propose a minimal schema change to support priority-tier billing without breaking existing callers. Do NOT implement — just describe the diff shape, name the function(s) that need to change, and give a one-paragraph rationale. Under 250 words."
QS[q9]="Plan (do not execute) how you would add a streak counter to the hub dashboard showing consecutive days with a journal entry. Cover: manifest changes, method signature, renderer choice, priority band, and one thing you would explicitly NOT do. Cite the hub-panels.md rule. Under 300 words."
QS[q10]="Looking at EmptyOS as it stands today, name ONE abstraction you would delete if you had a blank slate. Cite a specific file and explain in under 150 words. Pick something concrete, not a wholesale redesign."

QIDS=(q1 q2 q5 q6 q4 q7 q9 q10)

for qid in "${QIDS[@]}"; do
    out="${qid}-${PROV}.json"
    if [ -s "$out" ] && python -c "import json; json.load(open('$out'))" 2>/dev/null; then
        echo "[$PROV] $qid: skip (already valid)" >&2
        continue
    fi
    prompt="${QS[$qid]}"
    echo "[$PROV] $qid: running..." >&2
    python harness.py "$PROV" "$qid" "$prompt" > "$out" 2>&1
    rc=$?
    echo "[$PROV] $qid: done (exit=$rc)" >&2
done
