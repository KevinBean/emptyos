"""Pins the .agent-bus skill mirror against its .claude/skills source.

`.agent-bus/` is not a build artifact — per `.claude/rules/agent-bus.md` it is
the canonical store that *internal* `think()` agents read (`bus_context`,
`bus_menu`). External CLIs read `.claude/skills/` directly, so drift between
the two is invisible from a Claude Code session and silently degrades every
in-daemon agent: it loads a stale copy of a skill.

Three skills had drifted this way at HEAD (2026-08-28) — each had gained a
`## Cross-references` section in `.claude/skills/` that was never rippled, so
an internal agent loading them could not see the sibling skills they point at.

Comparison is on-disk bytes, because that is what an agent actually loads --
not HEAD, which is only a proxy. Skills whose native SKILL.md is *uncommitted*
are skipped: mid-edit is normal, its mirror is expected to lag until the author
ripples, and failing there would train people to ignore this test. A committed
skill has no such excuse.

Pure stdlib + pytest. No vault, no daemon, no network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from helpers import read_text_normalised

REPO = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True
    ).stdout


def _dirty_paths() -> set[str]:
    """Repo-relative paths with uncommitted changes (staged or not)."""
    out = _git("status", "--porcelain", "--", ".claude/skills", ".agent-bus/skills")
    dirty = set()
    for line in out.splitlines():
        if len(line) > 3:
            dirty.add(line[3:].strip().strip('"'))
    return dirty


def test_agent_bus_skill_mirror_matches_claude_skills_at_head():
    try:
        listed = _git("ls-files", ".agent-bus/skills/*/SKILL.md")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git unavailable — mirror check needs the repo history")

    bus_paths = [ln.strip() for ln in listed.splitlines() if ln.strip()]
    assert bus_paths, "no .agent-bus skills tracked — the guard would be vacuous"

    dirty = _dirty_paths()
    drift: list[str] = []
    skipped: list[str] = []
    for bus_path in bus_paths:
        slug = bus_path[len(".agent-bus/skills/") : -len("/SKILL.md")]
        native_path = f".claude/skills/{slug}/SKILL.md"
        native = REPO / native_path
        if not native.is_file():
            # Bus-only entry (a retired skill still mirrored) is not this
            # test's business -- check_skills.py owns orphan detection.
            continue
        # Skip ONLY when the native source is mid-edit. Do NOT also skip on a
        # dirty bus copy: drift makes the mirror dirty by definition, so that
        # condition skipped precisely the case this test exists to catch, and
        # the test passed with deliberate drift injected (`.claude/rules/
        # audits.md` § "green because it checks nothing").
        if native_path in dirty:
            skipped.append(slug)
            continue
        if read_text_normalised(native) != read_text_normalised(REPO / bus_path):
            drift.append(slug)

    assert len(skipped) < len(bus_paths), "every skill was mid-edit -- guard was vacuous"

    assert not drift, (
        "on-disk .agent-bus mirror is stale for: "
        + ", ".join(sorted(drift))
        + "\nRipple them: copy .claude/skills/<slug>/SKILL.md over "
        ".agent-bus/skills/<slug>/SKILL.md (or run `eos bus import`, which "
        "rewrites the WHOLE store — check its blast radius first if another "
        "session is mid-edit on CLAUDE.md or .claude/rules/)."
    )
