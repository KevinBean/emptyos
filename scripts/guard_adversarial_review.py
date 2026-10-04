#!/usr/bin/env python3
"""PreToolUse hook — deny `git commit` until an adversarial-review receipt exists.

Sibling of `guard_git_safety.py` and `guard_daemon_safety.py`: same deny
protocol, same fail-open posture, always exits 0 so a bug here can never
hard-block every command.

WHY A GATE AND NOT A REMINDER. The review step is worthless exactly when it is
most needed — a long session, a big diff, the end of the day — because that is
when a reminder gets skimmed. Keying the gate to a hash of the staged diff means
the attestation is about *these bytes*, not about the fact that a review once
happened this session: amend the diff and the gate re-closes.

WHICH REPO. The key comes from the work tree the commit actually targets, so
`git -C apps/personal commit` is gated on the NESTED index, not the parent's.
Keying everything on the parent made the nested repo uncommittable whenever a
parallel session held an unreviewed index, and both documented escapes were
wrong there: `--waive-all` would have written a receipt for the parent's key,
opening somebody else's gate on work nobody reviewed, and EOS_SKIP_REVIEW_GATE
cannot be set per-command because this hook reads its own environment and runs
before the shell does. Found 2026-09-01, one repo boundary out from the
pathspec hole below. `cd <dir> && git commit` is the same defect on the more
common spelling and was still open until 2026-09-02; unlike the `-C` case it
failed OPEN, since the parent index is usually empty and an empty index is
read as "nothing to attest to". An unresolvable `cd` now denies — the one
place this hook is deliberately not fail-open, because keying on the wrong
repo is worse than refusing to guess.

WHAT THIS CANNOT DO. It cannot tell whether the review was any good, whether the
findings were real, or whether a waiver reason is honest. It only enforces that
a written disposition exists for the exact diff being committed. The judgment is
still the reviewer's.

ESCAPE HATCHES, in order of preference:
  1. `python scripts/review_receipt.py write --summary "..."`   (normal path)
  2. `... write --waive-all "<reason>"`   (typo fix, revert, generated artifacts)
  3. `EOS_SKIP_REVIEW_GATE=1`             (emergency; leaves no receipt)
Removing this hook's entry from `.claude/settings.json` turns it off for good.

Non-commit commands, `git commit --dry-run`, and every non-git command pass
through untouched.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hook_common import command_code_view as _flags_view  # noqa: E402
from hook_common import project_root, tool_input  # noqa: E402

# A git-commit invocation anywhere in the command line (incl. after && / ;).
# Mirrors guard_git_safety.py so the two guards agree on what a commit is.
#
# `(?![-\w])` rather than `\b`: `-` is a non-word character, so `\bcommit\b`
# fires inside `commit-tree` and `commit-graph` — plumbing and maintenance
# commands that update no ref and commit nothing. Both were denied, and the
# suggested way out (write a receipt) attests to a diff they never touch.
_GIT_COMMIT = re.compile(r"\bgit\b[^|;&\n]*?\bcommit(?![-\w])", re.I)

# Shapes that look like a commit but change nothing.
_DRY_RUN = re.compile(r"--dry-run\b", re.I)


def _is_commit(cmd: str) -> bool:
    view = _flags_view(cmd)
    if not _GIT_COMMIT.search(view):
        return False
    if _DRY_RUN.search(view):
        return False
    return True


def _git_leading_opts(cmd: str) -> list[str]:
    """Tokens between `git` and `commit`, i.e. git's own options. [] if unparseable."""
    m = re.search(r"\bgit\b([^|;&\n]*?)\bcommit\b", cmd, re.I)
    if not m or not m.group(1).strip():
        return []
    try:
        return shlex.split(m.group(1), posix=True)
    except Exception:
        return []


# A `cd` that changes which repo a later `git commit` lands in. Anchored to a
# statement boundary so it never matches `cd` inside a message or a path.
_CD = re.compile(r"(?:^|[;&|]|\bthen\b|\bdo\b)\s*cd\s+([^;&|\n]+)", re.I)

# Shell text we decline to resolve: expansion, substitution, globbing. Guessing
# at these would key the gate on a repo the command does not actually target.
_UNRESOLVABLE = ("$", "`", "*", "?", "~")

# `cd` present, target not statically knowable. Distinct from None (no `cd` at
# all) because the two want opposite defaults — see `_commit_repo`.
UNRESOLVED_CD = "\x00unresolved-cd"


def _cd_repo(cmd: str, base: str) -> str | None:
    """Repo a `cd` moves the commit into, or UNRESOLVED_CD, or None.

    `_commit_repo` models `git -C <dir>` only, so `cd apps/personal && git
    commit` was keyed on the PARENT index. That is the same defect the `-C`
    handling was written to fix, still open on the more common spelling — and
    here it fails OPEN, not closed: `_receipt_state` returns ok when the parent
    index is empty, which is its normal state, so the nested repo committed
    ungated. It only ever denied when a parallel session happened to be holding
    staged files. Found 2026-09-02.

    Only a `cd` BEFORE the commit counts, and the last one wins, since that is
    the directory in force when git runs.
    """
    # `_GIT_COMMIT` itself, not a copy of its pattern: a second spelling here
    # would keep the old behaviour silently the next time that regex is edited.
    m = _GIT_COMMIT.search(cmd)
    limit = m.start() if m else len(cmd)
    target = None
    for cd in _CD.finditer(cmd):
        if cd.start() >= limit:
            break
        raw = cd.group(1).strip()
        if not raw or any(ch in raw for ch in _UNRESOLVABLE):
            target = UNRESOLVED_CD
            continue
        try:
            toks = shlex.split(raw, posix=True)
        except Exception:
            target = UNRESOLVED_CD
            continue
        # `cd x y` is not a directory change we understand; `cd` with no operand
        # goes home, which is never a repo we should key on.
        target = toks[0] if len(toks) == 1 else UNRESOLVED_CD
    if target is None or target is UNRESOLVED_CD:
        return target
    return target if os.path.isabs(target) else os.path.join(base, target)


def _commit_repo(cmd: str, base: str) -> str | None:
    """Resolve `git -C <dir>` so the gate keys on the repo the commit targets.

    Without this the key always came from the PARENT index, so a
    `git -C apps/personal commit` was gated on unrelated bytes — see the
    `staged_key` docstring for what that cost. `-C` is cumulative in git (each
    is applied relative to the last), so they are joined in order rather than
    taking the final one.

    With no `-C`, falls through to `_cd_repo` — so the return is None (parent
    repo), a path, or UNRESOLVED_CD. Returns None outright when
    `--git-dir`/`--work-tree` are present: those redirect the repo in ways this
    parse does not model, and guessing would be worse than the existing
    behaviour. Fail-open by design there — a guard that cannot read the command
    line must not become a wall.
    """
    toks = _git_leading_opts(cmd)
    if any(t == "--git-dir" or t == "--work-tree"
           or t.startswith(("--git-dir=", "--work-tree=")) for t in toks):
        return None
    where, i = None, 0
    while i < len(toks):
        tok = toks[i]
        if tok == "-C" and i + 1 < len(toks):
            nxt = toks[i + 1]
            where = nxt if where is None else os.path.join(where, nxt)
            i += 2
            continue
        if tok.startswith("-C") and len(tok) > 2:      # `-C<dir>`, no space
            nxt = tok[2:]
            where = nxt if where is None else os.path.join(where, nxt)
        elif tok == "-c":
            # `-c` consumes the NEXT token as its value, so a value beginning
            # with `-C` would otherwise be misread as a repo. Only `-c` is
            # listed: git's other pre-command options that carry a value
            # (--namespace, --exec-path, --git-dir…) all use the `=` form, so
            # they never swallow a following token.
            i += 2
            continue
        i += 1
    if where is None:
        # No `-C`. A `cd` earlier in the line moves the commit just as surely.
        return _cd_repo(cmd, base)
    # An explicit `-C` wins over a `cd`. Resolving it against `base` rather than
    # the cd'd directory is wrong for `cd a && git -C b commit`, but that shape
    # has not been seen and inventing a resolution order for it would be guessing.
    return where if os.path.isabs(where) else os.path.join(base, where)


def _commit_pathspec(cmd: str, repo: str | None = None) -> list[str]:
    """Explicit paths on a `git commit <paths>` line, best-effort.

    CLAUDE.md's parallel-session rule mandates `git commit <paths> -F-` with NO
    `git add`, which commits WORKTREE state and ignores the index. An
    index-keyed gate is therefore satisfiable by a receipt for somebody else's
    staged work while committing something nobody reviewed — which is exactly
    what happened the first time this gate was used for real (2026-08-28: 25
    files staged by a parallel session, with their receipt, while this session
    committed a disjoint set).

    Best-effort on purpose: an unparseable line yields [] and falls back to the
    index key, which is the previous behaviour. Never guesses a path.
    """
    m = re.search(r"\bgit\b[^|;&\n]*?\bcommit\b([^|;&\n]*)", cmd, re.I)
    if not m:
        return []
    tail, paths, skip = m.group(1), [], False
    for tok in shlex.split(tail, posix=True) if tail.strip() else []:
        if skip:
            skip = False
            continue
        if tok in ("-m", "--message", "-F", "--file", "--author", "--date"):
            skip = True
            continue
        if tok == "--":
            continue
        if tok.startswith("-"):
            continue
        # A path we can see on disk. Anything else (a commit message that lost
        # its flag, a heredoc marker) is not treated as a pathspec. Resolved
        # against the repo the commit targets, since `git -C <dir> commit <p>`
        # means `<dir>/<p>` — checking the parent would silently drop every
        # pathspec in a nested repo and fall back to the index key.
        root = repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.exists(os.path.join(root, tok)):
            paths.append(tok)
    return paths


def _receipt_state(
    paths: list[str] | None = None,
    repo: str | None = None,
) -> tuple[bool, str, int]:
    """Return (ok, key, n_staged). ok=True means a matching receipt exists.

    Fail-open on any internal error: a guard that cannot read git must not
    become a wall in front of every commit.
    """
    try:
        import review_receipt as rr

        key, paths = rr.staged_key(paths or None, repo=repo)
        if not paths:
            # Nothing staged. Either the command stages inline (`git add x &&
            # git commit`) or this commit will fail on its own. Either way we
            # have nothing to attest to — let git speak.
            return True, key, 0
        return (rr.load_receipt(key) is not None), key, len(paths)
    except Exception:
        return True, "", 0


def _deny(reason: str) -> None:
    """Emit the PreToolUse deny envelope. The hook still exits 0 — see main()."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") not in ("Bash", "PowerShell", "exec_command", "shell"):
        return 0

    if os.environ.get("EOS_SKIP_REVIEW_GATE"):
        return 0

    inp = tool_input(payload)
    cmd = str(inp.get("command") or inp.get("cmd") or "")
    if not _is_commit(cmd):
        return 0

    repo = _commit_repo(cmd, str(project_root(payload)))

    # A repo we named but cannot read. `_receipt_state` catches everything and
    # returns ok, so this used to ALLOW: `git -C <dir>` whose <dir> did not
    # resolve sailed straight through the gate. Reachable on Windows, where
    # `shlex.split(posix=True)` eats the backslashes out of `C:\path\to\repo`
    # and leaves `C:pathtorepo`. Same reasoning as UNRESOLVED_CD — an explicitly
    # named target we cannot read is a refusal, not a pass.
    if repo is not UNRESOLVED_CD and repo is not None and not os.path.isdir(repo):
        repo = UNRESOLVED_CD

    if repo is UNRESOLVED_CD:
        # The two cases just above: a `cd` we cannot resolve, or a named repo we
        # cannot read. The fail-open posture elsewhere is deliberate — a guard
        # that cannot read git must not become a wall — but it does not extend
        # here. Everywhere else, failing open means keying on the repo the commit
        # targets and merely missing a receipt; here it means attesting the WRONG
        # repo's bytes, so an unreviewed commit passes on a receipt written for
        # something else. Both cases name a target we cannot verify, so both
        # refuse rather than guess.
        _deny(
            "Blocked by the adversarial-review gate: this hook cannot tell "
            "which repo the commit targets — the command either `cd`s to a "
            "directory that is not statically resolvable, or names a repo "
            "path that does not exist.\n\n"
            "Name the repo with a literal, existing path:\n"
            '  git -C <dir> commit -m "..."\n\n'
            "An inline EOS_SKIP_REVIEW_GATE=1 will NOT help here — this hook "
            "reads its own environment and runs before the shell, so the var "
            "only takes effect from .claude/settings.json -> env.\n\n"
            "See .claude/skills/eos-adversarial-review/SKILL.md."
        )
        return 0

    ok, key, n = _receipt_state(_commit_pathspec(cmd, repo), repo)
    if ok:
        return 0

    # Name the way out for the repo actually being committed to. Without the
    # flag the suggested command keys on the parent index, so following the
    # message would write a receipt that authorises somebody else's staged work
    # and still leave this commit blocked.
    repo_flag = f' --repo "{repo}"' if repo else ""
    repo_line = f"\n\nTargets the nested repo at {repo}.\n" if repo else "\n\n"

    _deny(
        f"Blocked by the adversarial-review gate: no review receipt for the "
        f"staged diff (key {key}, {n} staged path(s))."
        f"{repo_line}"
        "Run the hostile review first (skill: eos-adversarial-review), resolve "
        "or waive each finding, then record the disposition:\n"
        f'  python scripts/review_receipt.py write{repo_flag} --summary "N findings: X fixed, Y waived"\n\n'
        "For a commit that does not warrant a review (typo fix, revert, "
        "generated artifacts):\n"
        f'  python scripts/review_receipt.py write{repo_flag} --waive-all "<reason>"\n\n'
        "See .claude/skills/eos-adversarial-review/SKILL.md."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
