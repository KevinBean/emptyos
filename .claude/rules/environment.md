# Environment Quirks — Windows Dev Shell

Stable facts about the user's Windows dev environment. Promoted from per-session re-discovery so we stop paying the same friction twice.

## Console encoding

- **Default:** Windows console uses `cp1252`. Any `print()` of non-ASCII (Greek letters, emoji, CJK) crashes with `UnicodeEncodeError`.
- **Fix in place:** `.claude/settings.json` sets `env.PYTHONIOENCODING=utf-8` so Claude-spawned `python` subprocesses inherit UTF-8 stdout. Verify with `python -c "import sys; print(sys.stdout.encoding)"` → should read `utf-8`.
- **If the hook isn't active:** write non-ASCII output via `open(path, "w", encoding="utf-8")` rather than `print()`. Don't reach for `chcp 65001` — fragile across PowerShell vs cmd vs Bash tools.

## Python 3.13 dep gaps

- `g2p_en` has no Python 3.13 wheel as of recent sessions. Used by the pronounce stack.
- `espeak-ng` Python binding needs separate Windows install.
- `cadquery 2.7.0` (+ underlying `OCP` / `vtk`) needs Python 3.12. EmptyOS daemon stays on 3.13 — use the user-home env pattern below.
- Apps that depend on these (`apps/pronounce`, `apps/voice-assistant` listen path) should gate imports behind try/except so the daemon doesn't refuse to boot when the user's interpreter lacks them.

## User-home Python envs for heavy / version-pinned deps

Never embed a venv in `D:/emptyos/`. When a tool needs a Python version or
deps that conflict with the daemon's 3.13, install into a user-home venv and
shell out from the plugin.

```bash
# One-time setup per tool
uv venv --python 3.12 "$LOCALAPPDATA/eos/envs/<tool>-3.12"
uv pip install --python "$LOCALAPPDATA/eos/envs/<tool>-3.12/Scripts/python.exe" <packages>
```

Plugin reads the python path from `emptyos.toml` `[plugins.<tool>] python_exe = "..."`.
Shared plumbing lives in `emptyos/sdk/userhome_venv.py` — `default_venv_python(tool, pyver)`,
`probe_launch(exe, args)`, `run_venv(exe, args) -> RunResult`. Stateless functions
(not a base class), so each plugin keeps its own `_launch_ok`/`_launch_err` + domain
error strings. A third venv-backed plugin should reuse these, not re-roll the subprocess plumbing.
First consumer: `plugins/cadquery/` (cadquery + trimesh + vtk in
`%LOCALAPPDATA%/eos/envs/cadquery-3.12/`). Verified 2026-05-23.
Second consumer: `plugins/markitdown/` (`markitdown[pptx,xlsx,docx]` in
`%LOCALAPPDATA%/eos/envs/markitdown-3.13/`) — markitdown's core `magika` dep
pins `onnxruntime<=1.20.1` on win32, which conflicts with the daemon env's
`onnxruntime-gpu 1.23.2`. Installing it in-process downgrades/breaks the GPU
runtime, so it runs in an isolated venv reached via subprocess. Verified
2026-06-07. (Note: the venv can be 3.13 here — markitdown only needs ≥3.10;
the venv is for the onnxruntime *version* conflict, not a Python-version gap.)

Why not embedded: keeps repo clean, survives `git clean -fdx`, shared across
checkouts, never gets into `release-public.py` snapshots.

Why not switching EmptyOS to 3.12: daemon's own deps (FastAPI, plugins,
agent-runtime) have settled on 3.13.

`py -0p` lists installed Pythons. `uv` is on PATH at
`%APPDATA%\Python\Python313\Scripts\uv.exe`.

See `reference_userhome_python_envs_for_heavy_deps` memory for cross-references.

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

## Parallel-session staging

Another Claude session or the user can stage files into `git` while a session is running. Before any `git add` or `git commit`:

1. Run `git status --short`
2. Compare staged files against what you actually edited this session
3. If anything looks unfamiliar, ASK before staging more — never `git add -A` or `git add .`

This rule exists because several sessions burned hours on commit splits or history rewrites after parallel auto-adds bundled 23 files into what should have been a focused commit.

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

## When to invoke

- `/preflight` — start-of-session safety check (git + daemons + env)
- `/env-check` — when something env-shaped just broke (encoding crash, missing dep, daemon refused)
- This file — read it directly when in doubt; it's the canonical reference

## When the rule list grows

If a new environment quirk surfaces in 2+ sessions, codify it here before it becomes a third. Don't bloat per-session memory with re-discoveries.
