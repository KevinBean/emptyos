# Claude Code — agent-view cheatsheet

Personal scratch note. Untracked. The dashboard that replaces "N terminal windows."

## Launch
```
claude agents              # the dashboard (your home base)
claude agents --cwd .      # only sessions started under this dir
claude agents --json       # non-interactive list (scripting; no TTY needed)
```
Use `claude agents` as your primary entry point instead of `claude`.

## Inside the dashboard
| Key | Action |
|---|---|
| `↑` / `↓` | move between rows |
| `Space` | peek panel — latest output / the question it's blocked on; type a reply + `Enter` |
| `Enter` / `→` | attach (full interactive session) |
| `Shift+Enter` | dispatch a new task **and** attach immediately |
| `←` (empty prompt) | detach back to the table (session keeps running) |
| `Alt+1`..`Alt+9` | jump to session 1–9 |
| `Ctrl+S` | toggle grouping: state ↔ directory |
| `Ctrl+T` | pin a session to top (keeps it alive while idle) |
| `Ctrl+R` | rename session |
| `Ctrl+X` | stop; press again within 2s to **delete** (removes its worktree too) |
| `Shift+↑/↓` | reorder |
| `Esc` | close peek / clear input / exit |
| `?` | show all shortcuts in-context |

Rows group: **Needs input → Ready for review → Working → Completed**.

## Dispatch a task
- Type a prompt in the bottom input + `Enter` → new background session (each `Enter` = a new session, not a follow-up).
- `@agent-name ...` → run a custom subagent as the session's main agent.
- `! pytest -x` → run a shell command as a background job (not a Claude session).
- Filter instead of dispatch: `s:working`, `s:blocked`, `a:<agent>`, `#<PR>`.

## From the shell (no dashboard)
```
claude --bg "task..."          # dispatch straight to background
claude --bg --name "tag" "..." # with a display name
claude attach <id>             # open in this terminal
claude logs <id>               # recent output
claude stop <id>               # stop it
```
Background your *current* session anytime: `/bg`.

## Interactive vs background (why a fresh board shows 0)
The dashboard table shows **background** sessions only. Your existing terminal
windows are **interactive** sessions and DON'T appear as rows until you
background them. So `claude agents` starts empty. Populate it by:
- dispatching a task from the bottom input (`Enter`), or
- pressing `←` on an empty prompt (or `/bg`) inside an existing window to move
  it onto the board.
(`claude agents --json` lists interactive sessions too — that's why the JSON
probe shows more than the TUI table.)

## Persistence
A supervisor process runs sessions — close the dashboard / shell / sleep the
machine and they keep going. Full shutdown stops them. Transcripts stay local
(`claude --resume`).

## EmptyOS-specific (this machine)
- `bgIsolation: "none"` is set in `.claude/settings.json` → background sessions
  edit the **working copy directly** (no per-session worktree). So: never run
  two background sessions that touch the **same file** at once.
- The EmptyOS daemon (`:9000`) is singular + user-owned — no session restarts
  it. For runtime verification each session leases a sandbox pool member
  (`:9002+`) via `/sandbox/api/*`. See `.claude/rules/sandbox-usage.md`.
- `.worktreeinclude` carries `emptyos.toml` + local config into explicit
  `claude --worktree` sessions (not bg sessions, since isolation is off).

Docs: https://code.claude.com/docs/en/agent-view.md
