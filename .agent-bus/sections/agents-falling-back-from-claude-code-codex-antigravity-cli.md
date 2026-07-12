
When Claude Code usage is constrained, a strong native agentic CLI is the
sanctioned fallback for **deep + agentic** work (coding *and* deep non-coding —
strategy, research, synthesis). These tools have their own Read/Edit/Bash loop;
they are **not** degraded the way an in-app `think` provider would be.

- **Codex CLI** (`codex`) reads **this file (`AGENTS.md`)** at the repo root. So
  follow the instruction above — **read `CLAUDE.md` first**, then the relevant
  `.claude/rules/*.md` for the task. Google **Antigravity CLI** is the Gemini-CLI
  successor; confirm its context-file convention from its docs on first use.
- **Routing:** send *narrow, well-scoped* work to the native CLI; reserve Claude
  Code for *wide* architectural sessions where its large context is the edge.
- **Proactive Reading & No New Rules:** Unlike Claude Code, your context does not magically auto-load the full contents of `.claude/rules/*.md` and `.claude/skills/*/SKILL.md`. To load them into your context, you **must** proactively use your tools: run `list_dir` (or `ls`) on `.claude/rules/` and `.agents/skills/` to find relevant filenames, use `grep_search` (or `grep`) for task-related keywords, and then use `view_file` (or `cat`) to read the actual file contents before you begin planning. Do **NOT** invent or write new rules and skills to compensate for your empty context — Claude Code reads the existing ones natively, so read what is already there first.
- **Sync `AGENTS.md` with `CLAUDE.md`:** Check the git commit history of `CLAUDE.md` against the last update of `AGENTS.md`. Review the recent diffs of `CLAUDE.md` and merge in new conceptual or architectural content only if it is relevant to non-Claude agents.
- **Same quality gate, model-agnostic:** run `build → conform → walk → simplify →
  commit → live-verify` (CLAUDE.md § EmptyOS Workflow). The tests catch a weaker
  model's mistakes regardless of which model wrote the diff.
- **Daemon hands-off still applies:** never restart `:9000`/`:9001`; lease a
  sandbox member (`.claude/rules/daemon-handling.md`).
- **Bounded vault/knowledge work needs no coding CLI at all** — use EmptyOS's own
  `assistant` / `staff` / `rooms` surfaces on a cheap `think` provider
  (openai-mini / local ollama), which offloads Claude entirely for those flows.
