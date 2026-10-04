"""Ask git which of a set of paths it ignores, and which it tracks.

Two questions, one batched subprocess each, for everything that *publishes* a
list derived from the working tree:

- ``gitignored_paths`` — a **generator** (the app catalog, the skills matrix)
  must not advertise a directory that exists only on the generating machine,
  but must keep an uncommitted one: "not ignored" is its filter.
- ``tracked_paths`` — a **gate** (a subscription plan naming apps, a test that
  pins "every id names a real app") means what reaches a fresh clone, and an
  untracked scaffold does not: "tracked" is its filter.

Four hand-rolled copies of the first question had accumulated before it was
extracted (``doc_data``, ``generate_skills_doc``, ``test_manifest_deps``, the
control-plane plan test), and one of them answered wrong in a way git never
reports: ``text=True`` rewrites ``\\n`` to ``\\r\\n`` on stdin on Windows, so
every line but the last reached ``check-ignore`` with a stray CR and matched
nothing (measured 2026-09-02: 1 of 2 ignored dirs slipped through). Bytes in,
``-z`` on both sides. Paths go to git **relative to where it runs**: a
backslash path makes git C-quote its echo (``core.quotePath``) and the string
compare fails as "not ignored".

Top-level and stdlib-only (``.claude/rules/top-level-modules.md``): a preflight
gate like ``generate_skills_doc`` must not pay the ``emptyos.sdk`` package
import — and its third-party deps — to ask git a question.

Both functions **degrade open** by default: no git, not a repository, a stall
past the 20 s budget → an empty set, i.e. the pre-filter behaviour (nothing
ignored; nothing known tracked). A generator must never silently empty its
output on a tooling hiccup. A gate passes ``strict=True`` so the same failure
raises ``GitUnavailable`` instead of becoming a vacuous pass or fail.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

_TIMEOUT_S = 20  # a budget, not a measurement: well past any healthy check-ignore/ls-files run


class GitUnavailable(RuntimeError):
    """git could not answer: no binary, not a repository, a stall, or a failed run."""


def _relative_to(paths: Iterable[Path], cwd: Path) -> dict[str, Path]:
    """``{posix-relative: original}`` for the paths under ``cwd``; the rest are
    skipped — git cannot be asked about a path outside the directory it runs in."""
    rel_of: dict[str, Path] = {}
    for p in paths:
        try:
            rel = Path(p).resolve().relative_to(cwd)
        except (ValueError, OSError):
            continue
        rel_of[rel.as_posix()] = p
    return rel_of


def _git_stdout(args: list[str], *, cwd: Path, payload: bytes | None, strict: bool) -> bytes | None:
    """stdout of ``git -C cwd <args>``, or ``None`` when git could not answer.
    Exit 1 is a successful "nothing matched" for both commands used here."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            input=payload,
            capture_output=True,
            timeout=_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if strict:
            raise GitUnavailable(str(exc)) from exc
        return None
    if proc.returncode not in (0, 1):  # 128 = not a git repository
        if strict:
            detail = proc.stderr.decode("utf-8", "replace").strip()
            raise GitUnavailable(detail or f"git {args[0]} exited {proc.returncode}")
        return None
    return proc.stdout


def gitignored_paths(paths: Iterable[Path], *, cwd: Path, strict: bool = False) -> set[Path]:
    """Subset of ``paths`` that git ignores, keyed on the objects passed in.

    Tracked content is never reported — git applies exclude rules to untracked
    paths only, so a force-added item under an ignored pattern stays, whether
    its file or its directory is queried. Callers still query a file
    (``manifest.toml``, ``SKILL.md``): in the real tree a directory path has
    produced a spurious match for an *untracked* dir against a blank
    ``.gitignore`` line (``generate_skills_doc``, 2026-08-07), and that shape
    does not reproduce in a fixture. An empty input makes no call.
    """
    cwd = Path(cwd).resolve()
    rel_of = _relative_to(paths, cwd)
    if not rel_of:
        return set()
    payload = b"\0".join(k.encode("utf-8") for k in rel_of) + b"\0"
    out = _git_stdout(["check-ignore", "-z", "--stdin"], cwd=cwd, payload=payload, strict=strict)
    if out is None:
        return set()
    hits = (entry.decode("utf-8", "replace") for entry in out.split(b"\0"))
    return {rel_of[k] for k in hits if k in rel_of}


def tracked_paths(paths: Iterable[Path], *, cwd: Path, strict: bool = False) -> set[Path]:
    """Subset of ``paths`` git tracks: a file git lists, or a directory with at
    least one tracked file under it. An untracked, un-ignored scaffold is not
    tracked. Without ``strict`` a git failure answers "nothing tracked".
    """
    cwd = Path(cwd).resolve()
    rel_of = _relative_to(paths, cwd)
    if not rel_of:
        return set()
    out = _git_stdout(["ls-files", "-z", "--", *rel_of], cwd=cwd, payload=None, strict=strict)
    if out is None:
        return set()
    tracked: set[Path] = set()
    for entry in out.split(b"\0"):
        listed = entry.decode("utf-8", "replace")
        if not listed:
            continue
        parts = listed.split("/")
        for depth in range(1, len(parts) + 1):  # the file itself and each queried ancestor
            key = "/".join(parts[:depth])
            if key in rel_of:
                tracked.add(rel_of[key])
    return tracked
