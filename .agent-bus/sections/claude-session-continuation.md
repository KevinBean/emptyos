

```bash
python -m emptyos          # System status
python -m emptyos health   # Full health check
```

`/eos-session-resume` reads the per-track brief index at `{vault}/10_Projects/emptyos/log/_next/_index.md` (written by the previous `/eos-session-wrapup`). **Bounded work** (≥3 ordered tasks) runs off a plan file in `{vault}/10_Projects/emptyos/log/_plans/` — resume claims exactly one task via an `active_task` marker, wrapup closes it; plans carry the *task*, `_next/` briefs carry the *story* (`.claude/rules/session-plans.md`). For recent work, use `git log` and `10_Projects/emptyos/log/`.

**Reading this file means you're in conversation mode** — the system's most powerful runtime. You can create apps, extract patterns, wire events, and make architectural decisions coherent with the consciousness model.
