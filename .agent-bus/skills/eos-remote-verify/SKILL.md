---
name: eos-remote-verify
description: Run an app's daemon-backed tests when your edits are on one machine and the daemon is on another — via an isolated worktree plus a sandbox-pool member, touching neither the remote's working tree nor its :9000. Use when you have committed locally, the only live daemon is on another host (typically homepc), and tests are still unverified. NOT for deploying a change to stay (that is eos-deploy-homepc) and NOT when a daemon is on the same machine (just lease a sandbox directly).
---

# Remote Verify

Your edits are here. The daemon is over there. `test_sys_*` needs a daemon, so
the tests sit unrun — and "unverified" is where the interesting defects live.

This is the safe path to running them: a **git worktree built from the remote's
own committed HEAD**, your changed files copied over it, and a **sandbox-pool
member** pointed at that worktree. It never touches the remote's working tree,
its branch, its vault, or `:9000`.

Distinct from `eos-deploy-homepc`, which puts a change on the remote **to
stay**. This one is read-only in intent: verify, then remove every trace.

## Do this first — three facts that change the plan

1. **Where is the daemon, actually?** Probe both. A "restarted" daemon is not
   necessarily on the machine you are editing.
   ```bash
   curl -s -m 5 -o /dev/null -w "%{http_code}\n" http://127.0.0.1:9000/api/health
   ssh <host> "curl -s -m 6 http://127.0.0.1:9000/api/health"
   ```
2. **Is the remote's HEAD even in your history?** Frequently not.
   ```bash
   git cat-file -e <their-head> 2>/dev/null && echo present || echo absent
   ```
   If absent, the histories have diverged — see § Why not a bundle.
3. **Is a session live on the remote?** A dirty count is not a liveness signal.
   Sample the dirty set's mtimes twice ~45s apart; zero movement plus a
   most-recent-touch older than a few minutes reads as quiet. Anything moving,
   stop and ask. (Memory: `detecting-live-agent-session`.)

## Why not a bundle

`git bundle` is the documented way to move commits to homepc, and it is the
wrong tool here.

- `git bundle create f.bundle <base>..<tip>` refuses with **"Refusing to create
  empty bundle"** unless the tip is a *ref*, not a bare SHA range.
- Worse, a thin bundle carries a **prerequisite** commit the remote must
  already have. When the histories have diverged — which is the normal state
  for a machine that commits its own work — it does not.

A worktree checked out from **the remote's own HEAD** sidesteps history
reconciliation entirely. You are not merging anything; you are building a
scratch tree out of code the remote already has, then dropping your files on
top. What you test is *your change against their current main*, which is the
more useful question anyway.

## The safety step people skip

Before copying a file over, prove the remote's committed copy is **byte-identical
to the base you edited from**. Otherwise you silently discard whatever they
changed there.

```python
# on the remote, per file
got  = git("-C", REPO, "rev-parse", "HEAD:" + path)   # their blob
want = "<git rev-parse <your-base>:<path>>"           # yours, computed locally
assert got == want
```

Also check the file is not dirty in their tree (`git status --short -- <path>`).
Only proceed on a full match. A mismatch is a merge, not a copy — stop.

## The loop

```bash
# 1. worktree from THEIR committed HEAD, detached — touches no branch
ssh <host> "git -C <repo> worktree add --detach <scratch>/wt HEAD"

# 2. your changed files over it
scp <each changed file> <host>:<scratch>/wt/<same path>
ssh <host> "git -C <scratch>/wt status --short"   # must list EXACTLY your files

# 3. lease a sandbox member running that tree (see sandbox-usage.md)
POST /sandbox/api/lease {"session_id": "...", "source_root": "<scratch>/wt", "ttl_s": 1800}

# 4. run the suite against the member, from inside the worktree
EOS_TEST_BASE_URL=http://127.0.0.1:9002 python -m pytest tests/test_sys_<app>.py -v
```

`EOS_TEST_BASE_URL` is the override (`tests/helpers.py`); without it every
fixture targets `:9000` — the user's daemon, running someone else's code.

Run the commands through **scp'd Python scripts, not chained ssh one-liners**.
`%{http_code}`, quotes and `&&` do not survive the ssh→cmd layers, and the
failure is a plausible-looking wrong answer rather than an error
(`.claude/rules/environment.md` § macOS / zsh).

## Then mutate — this is the point

A suite that has only ever been green proves nothing
(`.claude/rules/audits.md` § Failure mode 3). Having paid for a live
environment, spend the extra two minutes:

**Page JS hot-reloads** — the daemon serves `pages/` from disk per request — so
a mutation to HTML/JS lands with no restart. That is the specific carve-out to
`eos-mutation-verify`'s "don't use on `test_sys_*`", which is about *Python*
modules already imported into a running process. Mutating Python needs a
sandbox `/restart` between rounds.

Mutate in the worktree, run with `-k`, **check the selected count**, restore in
the same script. This is where the real finds happen: a first run of this loop
caught an `aria-selected` assertion that was satisfied by the static markup and
passed with the sync deleted.

## Teardown — and prove you left nothing

```bash
DELETE /sandbox/api/lease/_        {"session_id": "..."}
ssh <host> "git -C <repo> worktree remove --force <scratch>/wt || true"
ssh <host> "git -C <repo> worktree prune"
# delete the probe/runner scripts you scp'd
```

On Windows `worktree remove` often fails **Permission denied** because the
just-stopped daemon still holds a handle. `prune` clears the registry and the
leftover directory is inert scratch — say so and move on. **Never kill
processes to force it** (`.claude/rules/daemon-handling.md`).

Then prove innocence, out loud:

```bash
ssh <host> "git -C <repo> status --short -- <your paths>"   # must be empty
ssh <host> "git -C <repo> worktree list"                     # main only
ssh <host> "git -C <repo> log --oneline -1"                  # HEAD unchanged
```

Report a dirty-count change you did not cause rather than letting it look like
yours.

## When NOT to use it

- **A daemon on this machine** — just lease a sandbox (`sandbox-usage.md`); no
  worktree, no ssh.
- **The change is meant to land on the remote** — that is `eos-deploy-homepc` (user-global skill — lives in `~/.claude/skills`, not in the repo).
- **Daemon-free logic** — `test_unit_*` / `test_sdk_*` / engine tests need none
  of this. Exhaust them first; only the daemon-backed remainder is worth this
  ceremony.
- **A live session on the remote** — stop and ask. Its `.git` is shared with
  your worktree even though its working tree is not.
- **The remote's copy of a file differs from your base** — that is a merge.

## Cross-references

- `.claude/rules/sandbox-usage.md` — the lease API and `source_root` contract
  this rides; read its "which mode do I want" table.
- `.claude/rules/daemon-handling.md` — `:9000`/`:9001` stay untouched; `:9002+`
  are leasable.
- `.claude/skills/eos-mutation-verify/SKILL.md` — the mutation loop, and the
  `test_sys_*` caveat this skill carves an exception to.
- `.claude/rules/environment.md` — ssh quoting, the zsh word-splitting trap,
  parallel-session staging.
- `eos-deploy-homepc` — the deploy-to-stay sibling (user-global skill — lives in `~/.claude/skills`, not in the repo).
