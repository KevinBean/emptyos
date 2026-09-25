#!/usr/bin/env python3
"""check_skills.py — enforce the skill-authoring contract across .claude/skills.

Graduates the 2026-06-08 skill audit (borrowed from mattpocock/skills: small,
single-purpose, composable skills with a clear trigger, an explicit "when NOT
to use", and a local preflight/setup check) into a rerunnable gate, per
`.claude/rules/self-audit-loops.md` + `.claude/rules/audits.md`.

Two tiers (so the report is never the noisy "N skills broken" trap audits.md
warns about):

  HARD (exit nonzero) — the structural frontmatter contract the harness itself
  depends on, all deterministic:
    * SKILL.md exists
    * has a YAML frontmatter block that parses STRICTLY (PyYAML)
    * carries `name` + `description`
    * `name` == directory slug
    * `description` contains no `: ` (colon-space) — breaks strict YAML plain
      scalars; the working convention rephrases to ` — `
    * the `.agent-bus/skills` mirror is byte-identical (bus drift)

  SOFT (warn; only fails under --strict) — trigger-clarity / boundary / setup
  cues that are heuristic and may have honest exceptions:
    * description shorter than ~60 chars (weak trigger)
    * no "use when" cue in the description
    * no "when NOT to use" boundary anywhere (description or body)
    * a daemon/Playwright-dependent skill with no preflight/prerequisites section

Agent-CLI convention (`.claude/rules/agent-cli.md`): `--json` prints exactly one
envelope to stdout; exit 0 == ok. Human mode is the default.

Pure file I/O — no kernel import, safe while the daemons are up.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / ".claude" / "skills"
MIRROR = ROOT / ".agent-bus" / "skills"
# The Codex-facing mirror. Tracked, same SKILL.md shape, same frontmatter
# contract — and it went unchecked until 2026-07-31, by which point 13 of its 49
# skills breached the HARD contract (9 with no frontmatter at all, so Codex
# silently refused to load them). A gate that skips a tracked sibling store is a
# promise it isn't keeping. No bus mirror here, so the drift check is skipped.
AGENTS_NATIVE = ROOT / ".agents" / "skills"

# Skills whose body legitimately needs no preflight even though they mention a
# daemon. Keep this tight — audits.md. Each entry is a known false positive:
#   eos-session-resume / -wrapup : planning/aggregator, daemon is incidental
#   eos-repo-extract             : the hint is literally "never :9000" (uses a sandbox)
#   eos-external-vault-connector : already does an inline /api/health check
#   eos-mutation-verify          : names :9000 / test_sys_* only to rule them OUT
#                                  (a daemon-backed suite tests the running
#                                  process, not the mutated worktree)
PREFLIGHT_EXEMPT = {
    "eos-session-resume",
    "eos-session-wrapup",
    "eos-repo-extract",
    "eos-external-vault-connector",
    "eos-mutation-verify",
}

FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)
USE_WHEN_RE = re.compile(r"use when|use this|run (this|when)|when the user says", re.I)
BOUNDARY_RE = re.compile(
    r"when not to use|do ?n.?t use|not for\b|distinct from|skip if|skip:|\bNOT\b", re.I
)
PREFLIGHT_RE = re.compile(
    r"pre-?flight|prerequisite|pre-?requisite|precondition|^##+\s*setup|^##+\s*inputs|"
    r"daemon must|must be running|installed:",
    re.I | re.M,
)
DAEMON_HINT_RE = re.compile(r":900\d|localhost:9000|127\.0\.0\.1:9000|playwright|curl ", re.I)


def lint_one(d: Path, mirror_root: Path | None = MIRROR) -> tuple[list[str], list[str]]:
    """Return (hard_errors, soft_warnings) for one skill dir.

    ``mirror_root=None`` skips the ``.agent-bus`` drift check — for stores that
    have no bus mirror (the user-global ``~/.claude/skills``)."""
    slug = d.name
    hard: list[str] = []
    soft: list[str] = []
    f = d / "SKILL.md"
    if not f.exists():
        return [f"{slug}: no SKILL.md"], []
    text = f.read_text(encoding="utf-8", errors="replace")

    m = FM_RE.match(text)
    if not m:
        hard.append(f"{slug}: no YAML frontmatter block")
        return hard, soft
    raw = m.group(1)

    # Strict-parse gate runs only when PyYAML is importable; absence degrades to
    # the regex checks below rather than collapsing every skill to a hard fail.
    if yaml is not None:
        try:
            meta = yaml.safe_load(raw)
        except Exception as e:
            hard.append(f"{slug}: frontmatter does not parse strictly ({str(e).splitlines()[0]})")
            return hard, soft
        if not isinstance(meta, dict):
            hard.append(f"{slug}: frontmatter is not a mapping")
            return hard, soft

    # name/description read by regex (independent of yaml) — the contract is
    # single-line scalars, which is also what makes them robust YAML.
    nm = re.search(r"^name:\s*(.+?)\s*$", raw, re.M)
    dm = re.search(r"^description:\s*(.+?)\s*$", raw, re.M)
    name = nm.group(1).strip().strip("'\"") if nm else None
    desc = (dm.group(1).strip().strip("'\"") if dm else "")
    if not name:
        hard.append(f"{slug}: missing `name`")
    elif name != slug:
        hard.append(f"{slug}: `name` ({name!r}) != directory slug")
    if not desc:
        hard.append(f"{slug}: missing `description`")
    if ": " in desc:
        hard.append(f"{slug}: description has `: ` (colon-space) — breaks strict YAML; use ` — `")

    # bus mirror drift (skipped when this store has no bus mirror)
    if mirror_root is not None:
        mirror = mirror_root / slug / "SKILL.md"
        if mirror.exists():
            if mirror.read_text(encoding="utf-8") != text:
                hard.append(f"{slug}: .agent-bus mirror out of sync (run `eos bus import`)")
        # (a missing mirror is not an error — `eos bus import` creates it)

    if desc:
        if len(desc) < 60:
            soft.append(f"{slug}: description is short ({len(desc)} chars) — weak trigger")
        if not USE_WHEN_RE.search(desc):
            soft.append(f"{slug}: description has no 'use when …' trigger cue")
        if not BOUNDARY_RE.search(desc) and not BOUNDARY_RE.search(text):
            soft.append(f"{slug}: no 'when NOT to use' boundary in description or body")
        body = text[m.end():]
        if (
            slug not in PREFLIGHT_EXEMPT
            and DAEMON_HINT_RE.search(body)
            and not PREFLIGHT_RE.search(body)
        ):
            soft.append(f"{slug}: hits the daemon/Playwright but has no preflight/prerequisites section")
    return hard, soft


def _emit_json(ok: bool, code: str, message: str, data=None) -> None:
    """Single assembly point for the agent-cli envelope (so it never drifts)."""
    print(json.dumps({"ok": ok, "code": code, "message": message, "data": data}))


def main() -> int:
    ap = argparse.ArgumentParser(description="Lint EmptyOS skills for the authoring contract.")
    ap.add_argument("--json", action="store_true", help="emit one JSON envelope to stdout")
    ap.add_argument("--strict", action="store_true", help="treat soft warnings as failures too")
    ap.add_argument(
        "--user-skills",
        action="store_true",
        help="also lint the machine's user-global store (~/.claude/skills); it has no "
        "bus mirror, so the drift check is skipped there",
    )
    args = ap.parse_args()

    if not NATIVE.is_dir():
        msg = f"no skills dir at {NATIVE}"
        if args.json:
            _emit_json(False, "not_found", msg)
        else:
            print(msg, file=sys.stderr)
        return 2

    # (label, store_root, mirror_root) — mirror_root None ⇒ no bus-drift check.
    stores: list[tuple[str, Path, Path | None]] = [("", NATIVE, MIRROR)]
    if AGENTS_NATIVE.is_dir() and AGENTS_NATIVE.resolve() != NATIVE.resolve():
        stores.append(("[agents] ", AGENTS_NATIVE, None))
    if args.user_skills:
        user_root = Path.home() / ".claude" / "skills"
        if user_root.is_dir() and user_root.resolve() != NATIVE.resolve():
            stores.append(("[user] ", user_root, None))

    hard: list[str] = []
    soft: list[str] = []
    total = 0
    for label, root, mroot in stores:
        sdirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_"))
        total += len(sdirs)
        for d in sdirs:
            h, s = lint_one(d, mirror_root=mroot)
            hard += [label + x for x in h]
            soft += [label + x for x in s]

    if yaml is None:
        soft.append("(PyYAML not importable — strict-parse check was skipped)")

    ok = not hard and (not args.strict or not soft)
    code = "ok" if ok else "contract_violation"
    message = f"{total} skills — {len(hard)} hard, {len(soft)} soft"

    if args.json:
        _emit_json(ok, code, message, {"checked": total, "hard": hard, "soft": soft})
        return 0 if ok else 1

    scope = f"{NATIVE}" + (" + ~/.claude/skills" if len(stores) > 1 else "")
    print(f"Skill contract check — {total} skills under {scope}")
    if hard:
        print(f"\n  HARD ({len(hard)}):")
        for e in hard:
            print(f"    ✗ {e}")
    if soft:
        print(f"\n  SOFT ({len(soft)}):")
        for w in soft:
            print(f"    ⚠ {w}")
    if not hard and not soft:
        print("  ✓ all skills satisfy the contract")
    elif not hard:
        print("\n  ✓ no hard failures" + ("" if not args.strict else " (but --strict fails on soft)"))
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
