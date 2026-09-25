

- **POSIX only in the Bash tool.** It is Git Bash, not PowerShell — never use PowerShell here-strings (`@'…'@`) when committing there.
- **Never put a backtick in a double-quoted commit message** — Git Bash runs it as command substitution and silently drops text. Use a **single-quoted heredoc** (`git commit -F- <<'MSG'` … `MSG`); an unquoted `<<MSG` still expands. Hook-enforced by `scripts/guard_git_safety.py`, which also blocks `git add -A` / `git add .` / `git commit -am`.
- **Check for parallel-session work before staging.** Run `git status --short`, stage/commit **only** your task's files, never commit another session's uncommitted changes, and verify `HEAD` is yours afterwards. Full rule: `.claude/rules/environment.md` § Parallel-session staging.
