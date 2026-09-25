"""Pins the three .claude/.agents skill pairs repaired on 2026-08-28.

Both trees are git-tracked mirrors of the same skill: `.agents/skills/` is the
Codex-facing copy, `.claude/skills/` the Claude-Code-facing one, and a runner
invoked from either path must behave identically. `scripts/check_skill_script_
sync.py` is the comprehensive guard and remains the gate; this test exists only
to stop the specific pairs that were repaired from silently drifting back.

Deliberately narrow, and the reason matters. At the time of writing 15 pairs
were still drifted, and NONE of them could be repaired mechanically:

  * seven had DIVERGED - each tree held unique content, so copying either way
    destroys work (memory: never overwrite one tree with the other), and
    merging them is content judgment, not a scripted sync;
  * the rest sit inside eos-ai-conversation-ingest, which another session was
    editing across both trees at the time - including one file where `.agents`
    was 134 lines AHEAD.

So a whole-tree assertion here would be red for work that is not this test's to
do, and per `.claude/rules/audits.md` a check that is red for reasons the
reader cannot act on gets ignored. The three pinned below were unambiguous:
`.agents` held no unique line, so the sync was purely additive.

Pure stdlib + pytest. No vault, no daemon, no network.
"""
from __future__ import annotations

from pathlib import Path

from helpers import read_text_normalised

REPO = Path(__file__).resolve().parent.parent

# Repaired 2026-08-28: each was one-directional (.claude ahead, .agents holding
# nothing unique) and outside any skill under concurrent cross-tree edit.
PINNED = (
    # One-directional: .claude ahead, .agents held nothing unique.
    "eos-agent-diff-review/SKILL.md",
    "eos-design-system-audit/SKILL.md",
    "eos-devlog-publish/SKILL.md",
    # Reviewed line-by-line: every .agents-only line was an OLD version of a
    # line .claude had since edited, not unique content. Several were actively
    # dangerous -- the Codex-facing copies still told an agent to run
    # `python -m emptyos start &` and `cd dist && python -m emptyos health`,
    # both forbidden by .claude/rules/daemon-handling.md.
    "eos-architecture-review/SKILL.md",
    "eos-article-diagrams/SKILL.md",
    "eos-podcast/SKILL.md",
    "eos-release/SKILL.md",
    "eos-session-wrapup/docs-sync-commands.md",
    "eos-system-install/SKILL.md",
    # Synced the OTHER way (.agents -> .claude), and the only executable code
    # in this list. `.claude`'s claude_export_queue.py was 134 lines behind and
    # missing fenced_payload / render_artifact / render_tool_trace, while
    # holding nothing unique. The lag was silent because the stale `.claude`
    # test passed 12/12 without exercising any of them -- the same shape as the
    # 2026-08-05 repair_source_digest_link incident in
    # scripts/check_skill_script_sync.py's docstring, where a reviewed spec
    # flag was accepted and then ignored by a runner that lacked the function.
    "eos-ai-conversation-ingest/scripts/claude_export_queue.py",
    "eos-ai-conversation-ingest/tests/test_claude_export_queue.py",
    "eos-ai-conversation-ingest/tests/test_audit_chatgpt_identity_alias.py",
    "eos-ai-conversation-ingest/tests/test_provider_scoping_followup_rows.py",
)


def test_repaired_skill_pairs_stay_in_sync():
    drift = []
    for rel in PINNED:
        claude = REPO / ".claude" / "skills" / rel
        agents = REPO / ".agents" / "skills" / rel
        assert claude.is_file(), f"missing .claude copy: {rel}"
        assert agents.is_file(), f"missing .agents copy: {rel}"
        if read_text_normalised(claude) != read_text_normalised(agents):
            drift.append(rel)

    assert not drift, (
        "repaired skill pairs have drifted apart again: "
        + ", ".join(drift)
        + "\nThese three were synced .claude -> .agents because .agents held no "
        "unique content. Re-check direction before copying: run "
        "scripts/check_skill_script_sync.py and diff both ways first."
    )
