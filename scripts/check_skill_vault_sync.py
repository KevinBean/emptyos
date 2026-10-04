#!/usr/bin/env python
"""Report drift between a repo skill and its vault copy.

A skill opts in with ``vault_sync: true`` in its SKILL.md frontmatter. Only
opted-in skills are checked, and they are compared byte-for-byte (after
line-ending normalisation).

Why opt-in rather than "check every pair": measured 2026-07-28, 12 of the 33
skills present in both places differ, and 7 of those differences are
*intentional*. The tracked copy uses ``{vault}`` / ``{home}`` placeholders
because CLAUDE.md rule 13 forbids personal paths in git; the vault copy has
them expanded. A byte-equality check across all pairs would demand the repo
contain the very paths that rule bans — a 21% false-positive rate on a healthy
tree, and the wrong instruction. Two more differ because the same placeholder
expands differently per machine (a MacBook row and a Home-PC row in one table).
So the signal only separates when a human declares "these two files are meant
to be identical", which is exactly what the marker is.

Why report and never fix: drift runs in both directions. On the day this was
written the vault held a 119-line execution contract the repo lacked, while the
repo held a skill rewrite the vault lacked — and a blind repo->vault copy
silently deleted a live contract section. Direction is a human call.

Exit code is the number of skills that need attention, so preflight and CI can
gate on it.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOTS = ("skills", ".claude/skills", ".agents/skills")
VAULT_SKILLS_SUBDIR = "_claude/skills"
MARKER = re.compile(r"^vault_sync:\s*true\s*$", re.MULTILINE | re.IGNORECASE)


def _frontmatter(text: str) -> str:
    """The YAML block between the leading `---` fences, or ""."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end > 0 else ""


def _normalise(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").strip()


def vault_skills_dir() -> Path | None:
    config = REPO_ROOT / "emptyos.toml"
    if not config.exists():
        return None
    try:
        notes = tomllib.loads(config.read_text(encoding="utf-8")).get("notes", {})
    except (OSError, tomllib.TOMLDecodeError):
        return None
    path = str(notes.get("path") or "").strip()
    if not path:
        return None
    return Path(path) / VAULT_SKILLS_SUBDIR


def opted_in_skills() -> list[Path]:
    """Every repo SKILL.md carrying the ``vault_sync: true`` marker."""
    found: list[Path] = []
    for root in SKILL_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for skill_md in sorted(base.glob("*/SKILL.md")):
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if MARKER.search(_frontmatter(text)):
                found.append(skill_md)
    return found


def check() -> tuple[list[dict], list[str]]:
    """Return (findings, checked skill names)."""
    vault_dir = vault_skills_dir()
    findings: list[dict] = []
    checked: list[str] = []
    for repo_md in opted_in_skills():
        name = repo_md.parent.name
        checked.append(name)
        if vault_dir is None:
            findings.append({
                "skill": name,
                "problem": "no vault configured",
                "detail": "emptyos.toml has no [notes] path; cannot compare",
            })
            continue
        vault_md = vault_dir / name / "SKILL.md"
        if not vault_md.exists():
            findings.append({
                "skill": name,
                "problem": "missing in vault",
                "detail": str(vault_md),
            })
            continue
        repo_bytes = _normalise(repo_md.read_bytes())
        vault_bytes = _normalise(vault_md.read_bytes())
        if repo_bytes == vault_bytes:
            continue
        repo_lines = repo_bytes.decode("utf-8", "replace").splitlines()
        vault_lines = vault_bytes.decode("utf-8", "replace").splitlines()
        findings.append({
            "skill": name,
            "problem": "drifted",
            "detail": (
                f"{len(set(repo_lines) - set(vault_lines))} lines only in repo, "
                f"{len(set(vault_lines) - set(repo_lines))} only in vault"
            ),
        })
    return findings, checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable envelope")
    args = parser.parse_args()

    findings, checked = check()
    if args.json:
        import json
        print(json.dumps({
            "ok": not findings,
            "code": "ok" if not findings else "drift",
            "message": (
                f"{len(checked)} synced skill(s) checked, {len(findings)} need attention"
            ),
            "data": {"checked": checked, "findings": findings},
        }, ensure_ascii=False))
        return len(findings)

    if not checked:
        print("OK: no skill declares `vault_sync: true` (nothing to check)")
        return 0
    if not findings:
        print(f"OK: {len(checked)} synced skill(s) match their vault copy")
        return 0
    print(f"DRIFT: {len(findings)} of {len(checked)} synced skill(s) need attention\n")
    for item in findings:
        print(f"  {item['skill']}")
        print(f"    {item['problem']}: {item['detail']}")
    print(
        "\nDirection is a human call — drift has run both ways. Diff before "
        "copying; a blind repo->vault copy has already deleted a live section.",
    )
    return len(findings)


if __name__ == "__main__":
    sys.exit(main())
