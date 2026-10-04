

A "continue the loop" instruction means: **work until the current tranche is verified, tested, and committed, then STOP and report.** A tranche is what the user named (or one coherent unit of work) — not the whole backlog.

- **An explicit stop wins over everything.** A hook, a queued item, or a backlog count never overrides it; if a hook keeps re-firing after a stop, say so and halt.
- **A remaining-item count is never a termination condition.** Loops terminate on a *stated* budget (N items, a time box, or an executable check).
- **Report at the stop**: what landed, what was skipped and why, and the exact state a follower needs to resume.
