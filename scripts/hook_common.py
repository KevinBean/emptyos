"""Shared stdlib-only helpers for the agent hook scripts in this directory.

Hook scripts (check_daemon_restart_needed.py, session_start_refresh.py, …) are
deliberately self-contained and import nothing from the emptyos package, so they
keep working even when the kernel or daemon is broken. This module preserves that
property — pure stdlib, co-located in scripts/ — while removing the duplicated
project-root / payload handling. It is importable because Python puts a script's
own directory on sys.path[0] when the script runs as
`python <root>/scripts/<hook>.py`.

Named without a leading underscore on purpose: `.gitignore` ignores `scripts/_*.py`
(scratch scripts), so a `_`-prefixed shared module would never be committed and the
importing hooks would crash in a fresh clone.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def vault_path(root: Path) -> Path | None:
    """Resolve the mounted vault path, stdlib-only, never raises.

    Precedence: `.claude/vault-connection.json` (cached connection) →
    `emptyos.toml` `[notes] path`. Returns None when neither resolves, so
    hooks degrade silently on a fresh clone with no vault mounted.
    """
    vc = root / ".claude" / "vault-connection.json"
    if vc.exists():
        try:
            d = json.loads(vc.read_text(encoding="utf-8"))
            if d.get("connected") and d.get("vault_path"):
                return Path(d["vault_path"])
        except Exception:
            pass
    toml = root / "emptyos.toml"
    if toml.exists():
        try:
            import tomllib

            d = tomllib.loads(toml.read_text(encoding="utf-8"))
            p = d.get("notes", {}).get("path")
            if p:
                return Path(p)
        except Exception:
            pass
    return None


def project_root(payload: dict | None = None) -> Path:
    """Resolve the project root portably across clones.

    Precedence: CLAUDE_PROJECT_DIR (Claude Code sets it for every hook) →
    CODEX_PROJECT_DIR (if a Codex runner provides it) → the hook payload's
    cwd → os.getcwd(). Never hardcodes an absolute path, so the same hook works
    in the dev tree and in a fresh clone at any location.
    """
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CODEX_PROJECT_DIR")
    if not root and payload:
        root = payload.get("cwd")
    return Path(root or os.getcwd())


def session_tag(payload: dict | None) -> str:
    """`#<sid8>` for the session in the hook payload, or `""`.

    The 8-character prefix of Claude Code's session id — the form the status
    bar shows, and the one a plan-row claim carries (`.claude/rules/
    session-plans.md`). A session cannot learn its own id any other way, so the
    SessionStart hooks print it into context for the resume skill to copy.
    Anything that is not 8 hex characters is refused rather than truncated:
    a wrong tag in a claim is worse than none.
    """
    raw = str((payload or {}).get("session_id") or "").strip().lower()
    m = re.match(r"([0-9a-f]{8})(?:-|$)", raw)
    return f"#{m.group(1)}" if m else ""


def root_marker(payload: dict | None = None) -> str:
    """Project root as a forward-slashed string with a trailing slash.

    The relpath logic strips this marker off an absolute edited-file path.
    """
    return str(project_root(payload)).replace("\\", "/").rstrip("/") + "/"


def relpath(path: str, marker: str) -> str:
    """Return `path` relative to the project-root `marker`, forward-slashed.

    Matches an edited-file path (which may be absolute) against patterns that
    are written relative to the project root, never the absolute path.
    """
    norm = path.replace("\\", "/")
    idx = norm.lower().find(marker.lower())
    if idx >= 0:
        return norm[idx + len(marker):]
    return norm.lstrip("/")


_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

# Any heredoc body, quoted delimiter or not.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?^\2$", re.S | re.M)


def command_code_view(cmd: str) -> str:
    """A shell command with all string DATA blanked — verbs and flags only.

    Blanks quoted spans AND heredoc bodies, so a trigger token that appears only
    as *data* cannot masquerade as a command being run. Both omissions have
    caused real false positives in the PreToolUse guards:

      - `git commit -m "fix -a bug"`      → `-a` read as a flag (quoted span)
      - a commit/devlog heredoc containing the words "kill switch" and "emptyos"
        → read as a `kill` targeting the daemon (heredoc body)

    Use this for matching VERBS and FLAGS. Do NOT use it for matching file paths
    (a quoted `rm "data/x.db"` must still be caught), and do NOT use it to look
    for shell-expanded backticks — an unquoted heredoc body *does* expand, so
    that scan needs its own, narrower view.
    """
    return _QUOTED.sub(" ", _HEREDOC.sub(" ", cmd))


_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$")


def tool_input(payload: dict | None) -> dict:
    """Return the tool input across Claude and Codex hook payload shapes."""
    if not isinstance(payload, dict):
        return {}
    for key in ("tool_input", "input", "parameters", "arguments"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def edited_paths(payload: dict | None) -> list[str]:
    """Extract edited file paths from common hook payload shapes.

    Claude edit tools provide ``tool_input.file_path``. Codex apply-patch style
    events may provide a patch string instead, so parse the file headers as a
    fallback. The function is intentionally permissive and returns an empty list
    when the payload does not describe a file edit.
    """
    inp = tool_input(payload)
    paths: list[str] = []

    for key in ("file_path", "path", "filename"):
        value = inp.get(key)
        if isinstance(value, str) and value:
            paths.append(value)

    for key in ("file_paths", "paths", "files", "filenames"):
        value = inp.get(key)
        if isinstance(value, list):
            paths.extend(str(p) for p in value if p)

    for key in ("patch", "input", "content"):
        value = inp.get(key)
        if not isinstance(value, str):
            continue
        for line in value.splitlines():
            m = _PATCH_FILE.match(line.strip())
            if m:
                paths.append(m.group(1).strip())

    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        norm = p.replace("\\", "/")
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out
