#!/usr/bin/env python3
"""Run a project hook script from Codex with a stable project-root context.

Codex hook commands run from the session cwd and receive JSON on stdin. The
existing EmptyOS hook scripts were written for Claude Code, so this wrapper
keeps the project-local hook definitions short while preserving the same stdin
payload for the target script.
"""

from __future__ import annotations

import io
import json
import os
import runpy
import sys
from pathlib import Path

# Importable whether run as a top-level script or imported as scripts.run_codex_hook.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_common import project_root  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: run_codex_hook.py <script-name.py>\n")
        return 0

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    root = project_root(payload).resolve()
    script = root / "scripts" / argv[1]
    if not script.exists():
        return 0

    os.environ.setdefault("CODEX_PROJECT_DIR", str(root))
    # Snapshot process globals so an in-process caller (the test suite runs
    # main() directly) isn't left with a mutated cwd/argv/stdin.
    old_cwd, old_argv, old_stdin = os.getcwd(), sys.argv[:], sys.stdin
    os.chdir(root)
    sys.stdin = io.StringIO(json.dumps(payload))
    sys.argv = [str(script)]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 0
    finally:
        os.chdir(old_cwd)
        sys.argv = old_argv
        sys.stdin = old_stdin
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
