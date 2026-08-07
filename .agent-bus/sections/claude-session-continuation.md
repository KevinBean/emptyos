

```bash
python -m emptyos          # System status
python -m emptyos health   # Full health check
python -m emptyos start    # Boot daemon on port 9000 (or restart.bat on Windows)
```

In a Claude Code session, `/eos-session-resume` reads the per-track brief index at `{vault}/10_Projects/emptyos/log/_next/_index.md` (written by the previous `/eos-session-wrapup`) and briefs you on where to pick up.

**Bounded work runs off a session plan.** A problem you can enumerate as ≥3 ordered tasks gets a plan file in `{vault}/10_Projects/emptyos/log/_plans/` — resume claims exactly one task (writing an `active_task` mutual-exclusion marker before starting), wrapup closes it with a disposition, and the plan moves to `done/` when its last task closes. Plans carry the *task*; `_next/` briefs carry the *story*; unbounded work stays track-driven. This is what keeps completed work from accumulating in the index — a track is a work area and never completes, so nothing about it ever closes. See `.claude/rules/session-plans.md`.

**Reading this file means you're in conversation mode** — the system's most powerful runtime. You have the full architecture in context. You can create apps, extract patterns, wire events, make architectural decisions coherent with the consciousness model. The daemon serves what exists; you evolve what's next.

For recent work, use `git log` and `10_Projects/emptyos/log/`. Don't maintain changelogs here.
