#!/usr/bin/env python3
"""PostToolBatch hook — block the agentic loop on a syntax error in an edit.

Closes a real asymmetry: `fix-agent` gates every merge behind `py_compile`
precisely because a SyntaxError stops the daemon from booting (`.claude/rules/
test-fix-verify-loop.md` — "a SyntaxError merged means the sandbox can't restart,
which means the loop locks up"). But Claude's OWN edits have no such gate — a
broken `.py` sits there until the user restarts the daemon and it fails to boot,
or until it ships in a commit.

WHY PostToolBatch AND NOT PostToolUse: `PostToolUse` cannot reliably block (the
docs contradict themselves; the exit-code table says No). `PostToolBatch` is the
only post-edit event that can BOTH inspect a whole parallel batch AND halt the
loop with `{"decision": "block"}` before the next model call — so the model sees
the error immediately, while it still has the edit in context.

Payload shape is GROUND TRUTH, captured from a live batch via
`probe_hook_payload.py` (the docs render it two different ways and get the
per-call field name wrong):

    {"tool_calls": [{"tool_name", "tool_input", "tool_use_id", "tool_response"}]}

Checks, by extension:
  .py            -> compile() (in-process; no subprocess, no __pycache__ litter)
  .json          -> json.loads
  .js            -> `node --check` (skipped silently when node isn't on PATH)

Fail-open everywhere: an unreadable file, a missing node, or a bug in this gate
means "no opinion", never a spurious block. A file that was DELETED between the
edit and this hook is not an error.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from hook_common import edited_paths, relpath, root_marker

# Tools whose payload carries a file path we should syntax-check.
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

_PY = {".py"}
_JSON = {".json"}
_JS = {".js"}


def collect_paths(payload: dict) -> list[str]:
    """Every file path touched by an edit-shaped call in this batch (deduped).

    Each entry of ``tool_calls`` is itself a mini-payload, so `edited_paths`
    (which already normalises Claude's ``file_path`` AND Codex apply-patch
    headers) does the extraction — don't re-roll it here.
    """
    seen: list[str] = []
    for call in payload.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        if (call.get("tool_name") or "") not in _EDIT_TOOLS:
            continue
        for path in edited_paths(call):
            if path and path not in seen:
                seen.append(path)
    return seen


def check_file(path: str) -> str | None:
    """Return a human error string if `path` has a syntax error, else None."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in (_PY | _JSON | _JS):
        return None
    if not p.is_file():
        return None  # deleted / moved between the edit and this hook — not an error

    try:
        src = p.read_text(encoding="utf-8")
    except Exception:
        return None  # unreadable / binary → no opinion

    if not src.strip():
        return None  # empty file is not a syntax error worth halting the loop for

    if ext in _PY:
        try:
            compile(src, str(p), "exec")
        except SyntaxError as exc:
            return f"{exc.__class__.__name__}: {exc.msg} (line {exc.lineno})"
        except ValueError as exc:  # e.g. source with null bytes
            return f"ValueError: {exc}"
        return None

    if ext in _JSON:
        try:
            json.loads(src)
        except json.JSONDecodeError as exc:
            return f"JSONDecodeError: {exc.msg} (line {exc.lineno}, col {exc.colno})"
        return None

    if ext in _JS:
        node = shutil.which("node")
        if not node:
            return None  # no node → silently skip, never a false block
        try:
            proc = subprocess.run(
                [node, "--check", str(p)], capture_output=True, text=True, timeout=15
            )
        except Exception:
            return None
        if proc.returncode != 0:
            first = (proc.stderr or "").strip().splitlines()
            return f"node --check: {first[0] if first else 'failed'}"
        return None

    return None


def audit(payload: dict) -> list[tuple[str, str]]:
    """[(path, error)] for every edited file in the batch with a syntax error."""
    out: list[tuple[str, str]] = []
    for path in collect_paths(payload):
        try:
            err = check_file(path)
        except Exception:
            err = None  # a bug in the checker must never block the loop
        if err:
            out.append((path, err))
    return out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    try:
        bad = audit(payload)
    except Exception:
        return 0

    if not bad:
        return 0  # silent → loop proceeds

    marker = root_marker(payload)
    lines = "\n".join(f"  - {relpath(p, marker)} — {e}" for p, e in bad)
    reason = (
        f"Syntax error in {len(bad)} file(s) you just edited:\n{lines}\n\n"
        "Fix them before continuing. A broken .py stops the daemon from booting "
        "(fix-agent gates its merges on exactly this check); a broken .js breaks "
        "the page at load."
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
