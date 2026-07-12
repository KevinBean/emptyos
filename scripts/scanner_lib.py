"""Shared plumbing for the `check-*.py` / `*_audit.py` scanner family.

`.claude/rules/agent-cli.md` pins one envelope for every agent-facing command:

    {"ok": bool, "code": str, "message": str, "data": any}

…and says to keep the JSON assembly in one place "so the envelope never
drifts". With nineteen scanners each hand-rolling `json.dumps({...})`, it has
drifted: `check_call_app_declared.py` emits `{"ok": ..., **results}` with no
`code` and no `message`, spreading its payload across the top level where a
parser expects `data`.

`emit_json` is that one place. It is deliberately tiny — a scanner's value is
its heuristic, not its plumbing.

Adopt on touch; do not migrate the other scanners wholesale. Several are
release gates whose exit codes and output shape have downstream consumers, and
a mass rewrite would risk them for no behavioural gain.

Usage:

    from scanner_lib import emit_json     # scripts/ is sys.path[0] under
                                         # `python scripts/check-foo.py`
    if args.json:
        return emit_json(not findings, "stale_path", f"{n} stale", {"findings": findings})
"""

from __future__ import annotations

import json
from typing import Any

# Reserved by the envelope. A scanner returning one of these as a payload field
# would silently shadow it, which is how the drift started.
ENVELOPE_KEYS = frozenset({"ok", "code", "message", "data"})


def envelope(ok: bool, code: str, message: str, data: Any = None) -> dict:
    """Build the agent-cli envelope. `code` is 'ok' whenever `ok` is True."""
    return {"ok": bool(ok), "code": "ok" if ok else code, "message": message, "data": data}


def emit_json(ok: bool, code: str, message: str, data: Any = None) -> int:
    """Print the envelope on stdout and return the process exit code.

    Exit code mirrors `ok` (0 / 1), per the rule's "exit code is the primary
    signal". Nothing else may go to stdout in `--json` mode — a parser does
    `json.loads(stdout)`, so diagnostics belong on stderr.
    """
    print(json.dumps(envelope(ok, code, message, data)))
    return 0 if ok else 1
