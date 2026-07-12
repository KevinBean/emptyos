"""PostToolUse hook — advisory skill suggestions based on the edited path.

The "with you, not for you" version of ECC's trigger-based proactive
delegation (`.claude/rules/` has 40+ rules; only daemon-handling is
hook-enforced today). This hook NEVER runs a skill or blocks a tool — it
inspects the edited file path and writes a one-line reminder to stderr
when an edit crosses a boundary that a known skill/rule already governs.
The reminder is context for the agent; the agent (and user) decide whether
to act.

Mirrors scripts/check_daemon_restart_needed.py: reads the Claude Code hook
payload from stdin, matches the path *relative to the project root*, writes
advisories to stderr (Claude Code surfaces PostToolUse stderr), always
exits 0 so it can never block a tool call.

Three advisories, each tied to a real EmptyOS rule/skill:
  1. apps/**/pages/*.{html,js}      -> /eos-page-design-review (Phase 2.7 anti-slop)
  2. .claude/rules/*.md             -> `eos bus ripple` (sync .agent-bus)
  3. apps/**/app.py > 1200 lines    -> multi-module decomposition (P4 Atomic)
"""

from __future__ import annotations

import json
import re
import sys

from hook_common import edited_paths, relpath, root_marker

# P4 Atomic threshold (.claude/rules/multi-module-apps.md, apps/personal/integrity/).
DECOMPOSE_LINE_THRESHOLD = 1200

# (compiled relpath pattern, advisory text). Path is relative to project root,
# forward-slashed. The app.py rule is handled separately (needs a line count).
# Depth-agnostic: the app track tree nests arbitrarily
# (apps/public/standard/<app>/, apps/extension/<group>/<app>/,
# apps/personal/<app>/ ...), so match with `.*`, mirroring
# check_daemon_restart_needed.py and the iter_app_dirs scan.
_ADVISORIES = [
    (
        re.compile(r"^apps/.*/pages/.*\.(?:html|js)$"),
        "[skill] Page edited -- consider /eos-page-design-review before shipping "
        "(Phase 2.7 anti-slop + theme/mobile coverage).",
    ),
    (
        re.compile(r"^\.claude/rules/[^/]+\.md$"),
        "[skill] Rule changed -- run `eos bus ripple` to sync the canonical "
        ".agent-bus store, and consider whether this rule is mechanically "
        "enforceable (hookify it like daemon-handling).",
    ),
]

_APP_PY = re.compile(r"^apps/.*/app\.py$")


def _line_count(abs_path: str) -> int:
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def advisories_for(rel: str, abs_path: str) -> list[str]:
    out: list[str] = []
    for pat, text in _ADVISORIES:
        if pat.search(rel):
            out.append(text)
    if _APP_PY.search(rel):
        n = _line_count(abs_path)
        if n > DECOMPOSE_LINE_THRESHOLD:
            out.append(
                f"[skill] {rel} is now {n} lines (> {DECOMPOSE_LINE_THRESHOLD}, "
                "P4 Atomic threshold) -- consider multi-module decomposition "
                "(.claude/rules/multi-module-apps.md, scripts/decompose_app.py)."
            )
    return out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    marker = root_marker(payload)
    paths = edited_paths(payload)

    seen: set[str] = set()
    for p in paths:
        for msg in advisories_for(relpath(p, marker), p):
            if msg not in seen:
                seen.add(msg)
                sys.stderr.write(msg + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
