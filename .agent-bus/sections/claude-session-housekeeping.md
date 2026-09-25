

**Assume another session is running.** Before writing to any shared file — devlog, ledger, `docs/`, `_next/` briefs, `MEMORY.md` — re-read it immediately before the write and **append** rather than overwrite. Stage and commit in one chained command; never `git add -A`.

**Close ledger entries by exact id, never by fuzzy or substring match** — a substring close once shut three unrelated entries. Verify the count you closed matches the count you intended.
