#!/usr/bin/env python
"""Report drift between a skill's `.agents/` and `.claude/` copies.

Both trees are git-tracked mirrors of the same skill: `.agents/skills/` is the
Codex-facing copy, `.claude/skills/` the Claude-Code-facing one. A runner
invoked from either path must behave identically, so every `*.py` and `*.md` present in
both is compared byte-for-byte after line-ending normalisation.

Why this exists, concretely: on 2026-08-05 the `.claude` copy of
`eos-ai-conversation-ingest/scripts/apply_native_export_batch.py` was 162 lines
behind its `.agents` twin and was missing three functions, including
`repair_source_digest_link`. A reviewed spec setting
`repair_source_digest_link: true` was therefore **accepted and silently
discarded** — the runner emitted `ok: true`, the source note kept pointing at a
stale digest, and nothing anywhere said so. The plan for that task had recorded
the same divergence as "fixed" once already; it had recurred.

Why byte equality is gated here, while the sibling `check_skill_vault_sync.py`
needs an opt-in marker: that check compares a repo skill against its **vault**
copy, where 21% of pairs differ *legitimately* because the tracked copy carries
`{vault}` / `{home}` placeholders that CLAUDE.md rule 13 forbids expanding in
git. Both trees here are tracked, so that rationale cannot apply and no
legitimate reason for divergence has been found. Measured 2026-08-05 across all
27 pairs: after one sync, 27 identical / 0 different — the signal separates
completely, so it gates.

Opt out per skill with `script_sync: false` in its SKILL.md frontmatter. The
marker lives out-of-band deliberately: an inline comment marker would itself be
a byte difference, and so could never be equal in both copies.

Skill-level asymmetry (a skill directory in one tree and not the other) is
reported as **advisory**, not gated — a Codex-only or Claude-only skill is a
legitimate choice, whereas a *script* that exists in only one copy of a shared
skill is invisible to one of the two agents that is supposed to run it.

Exit code is the number of gated findings, so preflight and CI can gate on it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_ROOT = REPO_ROOT / ".agents" / "skills"
CLAUDE_ROOT = REPO_ROOT / ".claude" / "skills"
OPT_OUT = re.compile(r"^script_sync:\s*false\s*$", re.MULTILINE | re.IGNORECASE)
SKIP_DIR_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_MIRROR_SUFFIXES = ("-memory.md",)


def _frontmatter(text: str) -> str:
    """The YAML block between the leading `---` fences, or ""."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end > 0 else ""


def _opted_out(skill_dir: Path) -> bool:
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return False
    try:
        return bool(OPT_OUT.search(_frontmatter(md.read_text(encoding="utf-8"))))
    except OSError:
        return False


def _normalise(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").strip()


def _synced_files(base: Path) -> list[Path]:
    """Files that must be byte-identical across both mirrors.

    ``*.py`` — a runner invoked from either path must behave identically.

    ``*.md`` — the skill text itself. Covered from 2026-08-14: markdown was
    excluded, so 43 ``SKILL.md`` files silently drifted and `.agents/` shipped
    corrupted ``.Codex/rules/...`` paths (a naive Claude->Codex word swap had
    rewritten real `.claude/` references into directories that do not exist).
    The Python check would never have seen it.

    ``*-memory.md`` sidecars are per-machine living state and are never
    mirrored — same exclusion `sync_user_skills.py` applies to the bundled tree.
    """
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.rglob("*")
        if p.is_file()
        and p.suffix in (".py", ".md")
        and not p.name.endswith(SKIP_MIRROR_SUFFIXES)
        and not SKIP_DIR_PARTS.intersection(p.parts)
    )


def scan(agents_root: Path, claude_root: Path) -> dict:
    """Compare every shared skill's scripts and markdown across the two trees."""
    findings: list[dict] = []
    advisories: list[dict] = []
    pairs = 0
    skills_checked: list[str] = []
    skipped: list[str] = []

    agent_names = {p.name for p in agents_root.iterdir() if p.is_dir()} \
        if agents_root.is_dir() else set()
    claude_names = {p.name for p in claude_root.iterdir() if p.is_dir()} \
        if claude_root.is_dir() else set()

    for name in sorted(agent_names - claude_names):
        advisories.append({"skill": name, "problem": "skill-only-in-agents",
                           "detail": "invisible to Claude Code and the agent bus"})
    for name in sorted(claude_names - agent_names):
        advisories.append({"skill": name, "problem": "skill-only-in-claude",
                           "detail": "invisible to Codex"})

    for name in sorted(agent_names & claude_names):
        a_dir, c_dir = agents_root / name, claude_root / name
        if _opted_out(a_dir) or _opted_out(c_dir):
            skipped.append(name)
            continue
        rels = {p.relative_to(a_dir) for p in _synced_files(a_dir)}
        rels |= {p.relative_to(c_dir) for p in _synced_files(c_dir)}
        if not rels:
            continue
        skills_checked.append(name)
        for rel in sorted(rels, key=lambda r: r.as_posix()):
            a_file, c_file = a_dir / rel, c_dir / rel
            posix = rel.as_posix()
            if not c_file.exists():
                findings.append({"skill": name, "file": posix,
                                 "problem": "missing-in-claude",
                                 "detail": "present in .agents only"})
                continue
            if not a_file.exists():
                findings.append({"skill": name, "file": posix,
                                 "problem": "missing-in-agents",
                                 "detail": "present in .claude only"})
                continue
            pairs += 1
            try:
                if _normalise(a_file.read_bytes()) != _normalise(c_file.read_bytes()):
                    findings.append({"skill": name, "file": posix,
                                     "problem": "differs",
                                     "detail": "byte-differs after line-ending normalisation"})
            except OSError as exc:
                findings.append({"skill": name, "file": posix,
                                 "problem": "unreadable", "detail": str(exc)})

    return {"pairs": pairs, "skills_checked": skills_checked, "skipped": skipped,
            "findings": findings, "advisories": advisories}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="agent-cli envelope on stdout")
    args = ap.parse_args(argv)

    result = scan(AGENTS_ROOT, CLAUDE_ROOT)
    findings, advisories = result["findings"], result["advisories"]

    if args.json:
        print(json.dumps({
            "ok": not findings,
            "code": "ok" if not findings else "drift",
            "message": (
                f"{result['pairs']} shared file pair(s) across "
                f"{len(result['skills_checked'])} skill(s), {len(findings)} drifted"
            ),
            "data": result,
        }, ensure_ascii=False))
        return len(findings)

    if not result["skills_checked"]:
        print("OK: no skill present in both .agents/ and .claude/ ships Python")
        return 0

    # Detail first, verdict last — preflight surfaces the final stdout line as
    # the row summary, so an advisory tail would bury the result.
    if findings:
        for item in findings:
            print(f"  {item['skill']}/{item['file']}")
            print(f"    {item['problem']}: {item['detail']}")
        print("\nA runner invoked from either tree must behave identically. Diff "
              "before copying — but note the observed direction has been .agents "
              "ahead every time, and a spec key the .claude copy did not know was "
              "accepted and silently discarded.\n")
    if result["skipped"]:
        print(f"  skipped (script_sync: false): {', '.join(result['skipped'])}")
    if advisories:
        print(f"  advisory: {len(advisories)} skill(s) exist in one tree only "
              f"(legitimate — Codex-only or Claude-only)")

    if findings:
        print(f"DRIFT: {len(findings)} shared skill script(s) differ across trees")
    else:
        print(f"OK: {result['pairs']} shared file pair(s) across "
              f"{len(result['skills_checked'])} skill(s) are identical")
    return len(findings)


if __name__ == "__main__":
    sys.exit(main())
