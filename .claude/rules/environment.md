# Environment Quirks — Dev Shells

Stable facts about the user's dev environments. Promoted from per-session re-discovery so we stop paying the same friction twice.

Two machines, two shells, and the difference bites: the **Windows** box (homepc, `D:/emptyos`) runs the Bash tool as Git Bash / POSIX sh, and everything down to § Parallel-session staging assumes it. The **Mac** runs it as **zsh**, which is not a drop-in for the same one-liners — see § macOS / zsh.

## Console encoding

- **Default:** Windows console uses `cp1252`. Any `print()` of non-ASCII (Greek letters, emoji, CJK) crashes with `UnicodeEncodeError`.
- **Fix in place:** `.claude/settings.json` sets `env.PYTHONIOENCODING=utf-8` so Claude-spawned `python` subprocesses inherit UTF-8 stdout. Verify with `python -c "import sys; print(sys.stdout.encoding)"` → should read `utf-8`.
- **If the hook isn't active:** write non-ASCII output via `open(path, "w", encoding="utf-8")` rather than `print()`. Don't reach for `chcp 65001` — fragile across PowerShell vs cmd vs Bash tools.

## Line endings

`.gitattributes` pins `*.py` and `*.sh` to `eol=lf`, which overrides the system-wide `core.autocrlf=true` for those types. Other text files (`.md`) follow autocrlf, which is why their warning reads "LF will be replaced by CRLF". Even so, **about a third of tracked `.py` working copies are CRLF**: `git ls-files --eol -- '*.py'` measured 1029 `w/crlf` against 2105 `w/lf` on 2026-09-26, left over from checkouts that predate the attribute or from tools that write CRLF. Every commit normalises them to LF, so a CRLF `.py` shows no content diff, and "CRLF will be replaced by LF" is only a warning. Before blaming the last tool that touched a file, run `git ls-files --eol -- <path>` and check an untouched sibling. On 2026-09-26 this was nearly written up as a mutation-runner bug: the runner restores bytes exactly, and the file had been CRLF before the run.

## Python 3.13 dep gaps

- `g2p_en` has no Python 3.13 wheel as of recent sessions. Used by the pronounce stack.
- `espeak-ng` Python binding needs separate Windows install.
- `cadquery 2.7.0` (+ underlying `OCP` / `vtk`) needs Python 3.12. EmptyOS daemon stays on 3.13 — use the user-home env pattern below.
- Apps that depend on these (`apps/pronounce`, `apps/public/standard/voice-assistant` listen path) should gate imports behind try/except so the daemon doesn't refuse to boot when the user's interpreter lacks them.

## User-home Python envs

Never embed a venv in the repo. Heavy or version-pinned deps (cadquery,
markitdown, the desktop shell) live in `%LOCALAPPDATA%/eos/envs/<tool>-<py>/`
and are reached by subprocess → `.claude/rules/userhome-venvs.md`.

## Daemon ports

- `:9000` — main daemon (user-owned). NEVER restart from a Claude tool. See `.claude/rules/daemon-handling.md`.
- `:9001` — dogfood sidecar, plugin-spawned by `:9000`. Same hands-off rule applies.
- `:9002+` — sandbox-pool members. These ARE Claude's to lease/restart via `/sandbox/api/*`. See `.claude/rules/sandbox-usage.md`.

### Probing the daemon from Python — use `127.0.0.1`, not `localhost`

`curl http://localhost:9000/...` works fine. **Python `urllib`/`http.client` do NOT** —
they resolve `localhost`→IPv6 `::1` first, while the daemon binds IPv4, so you get
`URLError: [WinError 10061] ... actively refused it`. Always hit `http://127.0.0.1:9000`
from Python. Two companion gotchas for daemon HTTP from a script:

- **Auth (private mode):** `network.mode = private` requires `Authorization: Bearer <token>`
  on every request; token at `emptyos.toml [network] auth_token`. (`reference_daemon_auth_token_probe`)
- **Non-ASCII bodies:** POST UTF-8 bodies via Python `urllib`, not `curl -d` — Windows mangles
  `§`/`—`/CJK to cp1252. (`feedback_curl_windows_utf8_bodies.md`)

```python
import urllib.request, json, tomllib
tok = tomllib.load(open(r'D:/emptyos/emptyos.toml','rb'))['network']['auth_token']
req = urllib.request.Request("http://127.0.0.1:9000/<app>/api/<endpoint>",
    data=json.dumps({...}).encode("utf-8"), method="POST",
    headers={"Content-Type":"application/json","Authorization":f"Bearer {tok}"})
with urllib.request.urlopen(req, timeout=8) as r: print(json.load(r))
```

### Probing from PowerShell — `Invoke-WebRequest` needs `-UseBasicParsing`, or it hangs forever

Windows PowerShell 5.1's `Invoke-WebRequest` parses the response through the
**Internet Explorer DOM engine** unless you pass `-UseBasicParsing`. On a box
where IE was never first-run configured — and in any non-interactive session —
that parse **blocks indefinitely**. Not an error, not a timeout: `-TimeoutSec`
does not apply, because the request already completed and the *parser* is what
is stuck.

Measured 2026-08-31 against a sandbox member, same URL, same body, one variable:

| call | result |
|---|---|
| `curl.exe --data-binary "@file"` | **200 in 0.013s** |
| `Invoke-WebRequest -UseBasicParsing` | **200 in 0.52s** |
| `Invoke-WebRequest` (no flag) | **never returns** |

The failure mode is worse than a hang, because the **server handled the request
normally**. A POST that writes will have written; a py-spy dump of the daemon
shows the event loop **idle** in `select`. So it reads as "the write landed but
the response never came" — which looks exactly like a server-side wedge and is
not one. Three probes were lost to this before an A/B, and it briefly got
written up as an EmptyOS bug.

`Invoke-RestMethod` is **unaffected** — it does not use the IE parser — which is
why every other PowerShell call in the same session worked and hid the pattern.

So: prefer `curl.exe` with `--data-binary "@file"` (which also dodges the
quote-eating in § POST bodies above), or `Invoke-RestMethod`. If you must use
`Invoke-WebRequest`, always `-UseBasicParsing`. And when a request appears to
hang, **dump the server's stack before believing it** — an idle loop means the
hang is in your client.

## Parallel-session staging

Another Claude session or the user can stage files into `git` while a session is running. Before any `git add` or `git commit`:

1. Run `git status --short`
2. Compare staged files against what you actually edited this session
3. If anything looks unfamiliar, ASK before staging more — never `git add -A` or `git add .`

This rule exists because several sessions burned hours on commit splits or history rewrites after parallel auto-adds bundled 23 files into what should have been a focused commit.

### Stage and commit in ONE command — the index can move underneath you

Checking `git status` and then committing in a **separate** call is not enough. A
background clean/drain routine can stash + reset + re-stage the whole tree between
your two commands, and a bare `git commit` commits **the index as it is at that
moment**, not what you staged. On 2026-07-20 that turned an intended 3-file commit
into 103 files of four other tracks' in-flight work.

Chain the add, a count assertion, and the commit so nothing can intervene:

```bash
git add <paths> && N=$(git diff --cached --name-only | wc -l) \
  && [ "$N" -eq <expected> ] && git commit -F- <<'MSG'
…
MSG
```

If the count is wrong, the commit never runs. Recovering afterwards is
`git reset --soft HEAD~1` (undo the commit, keep every change) → `git reset`
(unstage all) → re-add your paths — non-destructive, but far more expensive than
the guard. Signals that a tree-manager is active: a `reset: moving to HEAD` entry
in `git reflog`, or a stash named like `parallel-wip-during-clean-<date>`. Recover
just your own files from such a stash with
`git checkout stash@{0} -- <your paths>` rather than popping the whole thing.

### When a parallel session has touched the same file, or parked the tree on its branch

Two sharper cases seen repeatedly in one long session (2026-07-15):

- **The parallel session edited the *same file* you did** (e.g. both add to one manifest). `git add <file>` would stage their uncommitted hunks too. Stage only your hunks with a filtered patch to the index — the working tree stays untouched, their changes stay unstaged:

  ```bash
  git diff HEAD -- <file> > /tmp/full.patch
  # keep only YOUR hunks (drop hunks containing the other session's markers), then:
  git apply --cached /tmp/mine.patch
  git commit -m "…"   # commits only your staged hunks
  ```

  `git add -p` / `git reset -p` are interactive and blocked in this harness — the filtered-patch route is the non-interactive equivalent. Split cleanly because your hunks and theirs are in different `@@` regions.

- **A parallel session switched the working tree onto its feature branch**, so your commit lands there instead of `main`. Don't `git checkout main` (it would carry or fight their dirty tree). Get your commit to `main` through a throwaway worktree that touches neither their branch nor their working tree:

  ```bash
  git worktree add "$SCRATCH/mwt" main
  ( cd "$SCRATCH/mwt" && git cherry-pick <your-commit> )
  git worktree prune                       # if `worktree remove` hits a file lock, prune clears the registry
  ```

  The commit exists on both branches (identical content) until the feature branch merges — a clean no-op merge. Verify with `git merge-base --is-ancestor <commit> main`. On Windows the temp worktree dir may stay locked; `worktree prune` is enough — the leftover dir is harmless scratch.

## macOS / zsh

### zsh does not word-split an unquoted variable — a loop over files silently runs nothing

In POSIX sh, `python -m pytest $FILES` expands to one argument per file. **In zsh it
expands to a single argument** containing the whole string, so pytest receives one
path that does not exist. It reports `no tests ran in 0.01s` and exits 0-ish; a
mutation batch built on it certifies every mutation as "survived" while nothing was
ever executed. Measured 2026-09-01 on an eight-mutation verification pass — every
line read `no tests ran`, which is at least loud, but the same shape in a `for f in
$FILES` loop just iterates once over a bogus name.

Use an array (`FILES=(a.py b.py); pytest $FILES` — zsh splits arrays), `${=VAR}` to
force splitting, or list the paths literally. The habit that catches it regardless:
**re-run the restored state after every mutation batch** and confirm the expected
count comes back, rather than trusting the loop's own output.

Same family as the harness traps in `.claude/rules/dev-gotchas.md` — a harness that
cannot run the thing under test does not return a null result, it returns a false one.

### Non-ASCII over `ssh homepc-ts` still hits the cp1252 trap

`.claude/settings.json` sets `PYTHONIOENCODING=utf-8` for **locally** spawned
processes. A python script run on the Windows box *through* ssh inherits that box's
console encoding instead, so printing `§`, `Ω`, `·` or `→` raises `UnicodeEncodeError`
mid-output — which looks like the script hanging or the daemon truncating a response,
not like an encoding fault. Prefix the remote command with `set PYTHONIOENCODING=utf-8 &`,
or wrap stdout: `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")`.

### Probe a remote file's content with Python, not a chained `findstr`

`ssh host "cd /d D:\repo && findstr /C:\"needle\" file | find /c \"\""` returned `0`
for strings that provably exist in the file — the quoting survives neither the ssh
layer nor cmd's, and the failure is a plausible-looking count rather than an error.
Write the probe to a file, `scp` it, run it. And **use positive markers**: a probe that
passes when a string is *absent* cannot tell "the fix is applied" from "the file is
missing", which is how one audit read a successful deploy as a wiped one.

## When to invoke

- `/preflight` — start-of-session safety check (git + daemons + env)
- `/env-check` — when something env-shaped just broke (encoding crash, missing dep, daemon refused)
- This file — read it directly when in doubt; it's the canonical reference

## When the rule list grows

If a new environment quirk surfaces in 2+ sessions, codify it here before it becomes a third. Don't bloat per-session memory with re-discoveries.
