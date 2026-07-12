---
name: preflight
description: Pre-work safety check at the start of a session. Inspects git state for unexpected staged files or parallel-session commits, confirms the main daemon (:9000) and dogfood daemon (:9001) are reachable, and surfaces anomalies before any code work begins. Use when the user says "preflight", "/preflight", "check state", or whenever you suspect another Codex/user session may have been editing in parallel.
---

# Preflight

Pre-work safety check. Catches the recurring friction modes documented in `MEMORY.md` — parallel-session auto-staging, daemon drift, and environment quirks — *before* they compound into a wasted hour.

## When to use

- First turn after `/clear` or a fresh `/eos-session-resume`
- When `git status` shows files you didn't touch
- Before any release work (`/eos-release-public`, version bumps)
- When the user says "preflight" or invokes `/preflight`

## Process

### 1. Git state

```bash
git status --short
git log --oneline -5
```

Flag anything surprising:
- **Staged files Codex didn't touch this session** → ask before staging more. Another session may be active.
- **Commits with timestamps inside the last 30 min that you didn't make** → parallel session evidence; warn explicitly.
- **Detached HEAD or unexpected branch** → halt, ask.

### 2. Daemon health

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9000/api/health
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9001/api/health   # dogfood, may be disabled
```

- `:9000` not responding → the user's main daemon is down. Surface; do NOT try to restart it (`.Codex/rules/daemon-handling.md`).
- `:9001` connection refused with `dogfood-demo` plugin enabled → note it, but don't act.

### 3. Context Bus integrity (only if `.agent-bus/` exists)

```bash
python scripts/agent_bus.py ripple --dry-run
```

- **Workspace not initialized** (`[-] Context Bus not initialized…`) → skip; the bus is opt-in, fresh clones don't have one.
- **No changes detected** → bus in sync.
- **Changes detected** → surface to the user. Don't auto-ripple — they may have intentionally edited a native file and need to pull it back into `.agent-bus/` first. Ripple is one-way (canonical → native); blind sync would clobber.

### 4. Environment quick-probe

Two stable Windows quirks documented in `.Codex/rules/environment.md`:

- Console default cp1252 — handled by the `PYTHONIOENCODING=utf-8` hook in `.Codex/settings.json`. If a recent session crashed on non-ASCII output, mention it.
- Python 3.13 wheels missing for `g2p_en` / `espeak-ng` — surfaces only if the user touches pronounce/voice work.

### 5. Report

Write one short paragraph back. Format:

```
Preflight:
  git: <clean | N staged files Codex didn't touch | parallel commits detected>
  bus: <in sync | OUT OF SYNC — ripple needed>
  :9000: <ok | DOWN — user needs to run restart.bat>
  :9001: <ok | refused — dogfood plugin off>
  anomalies: <none | bullet list>
```

If everything is clean, one line is enough: `Preflight: clean. Ready.`

## What this skill is NOT

- Not a daemon restart. The user owns :9000 — see `.Codex/rules/daemon-handling.md`.
- Not a test runner. Tests are downstream of preflight, run via `pytest` after work starts.
- Not a AGENTS.md audit. Run `/Codex-md-management:Codex-md-improver` for that.
