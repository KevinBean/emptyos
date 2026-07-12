"""Terminal diff rendering — shared by the rooms CLI (`eos rooms --code`)
and the agent REPL (`eos chat` / `eos code`) approval surfaces.

`render_diff_lines` was originally `_render_diff_lines` in `emptyos/cli/chat.py`;
lifted here when the agent REPL's terminal approval became the second consumer
(P1.1 of the CLI-hardening plan) — CLAUDE.md Dev Rule 9 (extract on the 2nd caller).

Pure: returns Rich-markup strings, imports only stdlib. The `diff_lines` shape
({"kind": "ctx"|"add"|"del"|"hunk", "text": ...}) matches what `SandboxedWrite`
produces server-side, so the rooms gate and the agent approval render identically.
"""

from __future__ import annotations

import difflib


def unified_diff_lines(
    old: str, new: str, *, path: str = "", context: int = 3
) -> list[dict]:
    """A unified diff of ``old`` → ``new`` as ``diff_lines`` dicts.

    Output matches the ``SandboxedWrite`` diff_lines shape consumed by
    ``render_diff_lines``. File-header lines (``---``/``+++``) are dropped —
    the caller shows the path separately.
    """
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    name = path or "file"
    out: list[dict] = []
    for line in difflib.unified_diff(
        old_lines, new_lines, fromfile=name, tofile=name, lineterm="", n=context
    ):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            out.append({"kind": "hunk", "text": line})
        elif line.startswith("+"):
            out.append({"kind": "add", "text": line})
        elif line.startswith("-"):
            out.append({"kind": "del", "text": line})
        else:
            out.append({"kind": "ctx", "text": line})
    return out


def render_diff_lines(diff_lines: list[dict], max_lines: int = 60) -> str:
    """Color-render a diff_lines list for terminal (Rich-markup) output.

    Each entry is {"kind": "ctx"|"add"|"del"|"hunk", "text": "..."}.
    Long diffs truncate with a tail marker so the card stays readable in a
    small terminal pane.
    """
    out: list[str] = []
    shown = 0
    for entry in diff_lines:
        if shown >= max_lines:
            remaining = len(diff_lines) - shown
            out.append(f"[dim]… {remaining} more diff line(s) — apply to see full result[/dim]")
            break
        kind = entry.get("kind", "ctx")
        text = entry.get("text", "")
        # Rich markup-safe: escape any literal `[` so syntax inside the diff
        # doesn't get parsed as styling.
        text = text.replace("[", r"\[")
        if kind == "add":
            out.append(f"[green]{text}[/green]")
        elif kind == "del":
            out.append(f"[red]{text}[/red]")
        elif kind == "hunk":
            out.append(f"[cyan]{text}[/cyan]")
        else:
            out.append(f"[dim]{text}[/dim]")
        shown += 1
    return "\n".join(out)
