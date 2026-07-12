

- **POSIX only in the Bash tool.** The Bash tool is Git Bash (POSIX sh), not PowerShell — NEVER use PowerShell here-string syntax (`@'…'@`) when committing. Use `git commit -m "message"` with the `-m` flag, or a POSIX heredoc for a multi-line message. PowerShell here-strings belong only in the PowerShell tool.
- **Check for parallel-session work before staging.** Another Claude session or the user may have staged files concurrently. Run `git status --short`, stage/commit **only** files scoped to the current task, and decline to commit unfamiliar uncommitted changes that belong to another session — never `git add -A` / `git add .`. After committing, verify `HEAD` is yours. Full rule: `.claude/rules/environment.md` § Parallel-session staging.
