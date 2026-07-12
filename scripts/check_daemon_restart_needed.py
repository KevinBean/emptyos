"""PostToolUse hook — flag edits that require daemon restart.

Reads the Claude Code hook payload from stdin, inspects the edited file
path, and writes a brief reminder to stderr when the change touches a
file the daemon imports (apps/**/*.py, plugins/**/*.py, emptyos/**/*.py).
HTML / CSS / JS under pages/ or static/ don't trigger the reminder — the
daemon serves those from disk per request.

Always exits 0 so the hook never blocks a tool call. Reminder text goes
to stderr; Claude Code surfaces stderr from PostToolUse hooks.
"""

from __future__ import annotations

import json
import re
import sys

from hook_common import edited_paths, relpath, root_marker


# Files that, when changed, won't take effect until the daemon respawns.
# Patterns are matched against the path *relative to the project root*,
# never against the absolute path (where "D:/emptyos/" would create false
# positives on every script under scripts/).
RESTART_PATTERNS = [
    re.compile(r"^apps/[^/]+/.*\.py$"),
    re.compile(r"^apps/personal/[^/]+/.*\.py$"),
    re.compile(r"^plugins/[^/]+/.*\.py$"),
    re.compile(r"^emptyos/(kernel|web|sdk|capabilities|runtime|cli)/.*\.py$"),
    re.compile(r"^emptyos\.toml$"),
]

# Static-served files — daemon picks them up per request, no restart needed.
STATIC_PATTERNS = [
    re.compile(r"^apps/[^/]+/pages/"),
    re.compile(r"^apps/personal/[^/]+/pages/"),
    re.compile(r"^emptyos/web/static/"),
]


def needs_restart(path: str, root: str) -> bool:
    rel = relpath(path, root)
    if any(p.search(rel) for p in STATIC_PATTERNS):
        return False
    return any(p.search(rel) for p in RESTART_PATTERNS)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    root = root_marker(payload)
    paths = edited_paths(payload)

    flagged = [relpath(p, root) for p in paths if needs_restart(p, root)]
    if flagged:
        msg = (
            "[daemon-restart] Edited Python the daemon imports: "
            + ", ".join(flagged[:3])
            + (f" (+{len(flagged)-3} more)" if len(flagged) > 3 else "")
            + " — change won't be live until the user runs restart.bat."
        )
        sys.stderr.write(msg + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
