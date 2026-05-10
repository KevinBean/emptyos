"""Grep search provider — ripgrep-backed, with content mode and filters."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from emptyos.capabilities import Provider

# path:lineno:text for match, path-lineno-text for context. On Windows the
# path itself contains a drive colon (`D:\...`), so we anchor on the
# `:<digits>:` or `-<digits>-` separator between path and line number.
_RG_LINE_RE = re.compile(r"^(?P<path>.+?)(?P<sep>[:\-])(?P<lineno>\d+)(?P=sep)(?P<text>.*)$")


class GrepSearchProvider(Provider):
    """Search files using ripgrep (rg) or grep.

    Two modes:
        mode="files_with_matches" (default) — returns [{"path": str}, ...]
        mode="content"                      — returns [{"path": str, "line_number": int, "text": str}, ...]

    Filters (mode-agnostic):
        case_insensitive: bool = True   — rg -i
        glob: str = ""                  — rg --glob <pat>
        type: str = ""                  — rg --type <name>   (e.g. 'py', 'md')
        context: int = 0                — rg -C <n>  (content mode only)
        limit: int = 200                — cap on result count

    Falls back to `grep -rl` when ripgrep is unavailable (files mode only —
    `grep` doesn't give the same structured content output).
    """

    name = "grep"

    def __init__(self, base_path: str = ""):
        self.base_path = Path(base_path) if base_path else None
        self._cmd: str | None = None

    async def available(self) -> bool:
        if shutil.which("rg"):
            self._cmd = "rg"
            return True
        if shutil.which("grep"):
            self._cmd = "grep"
            return True
        return False

    async def execute(
        self,
        *,
        query: str,
        path: str = "",
        mode: str = "files_with_matches",
        case_insensitive: bool = True,
        glob: str = "",
        type: str = "",
        context: int = 0,
        limit: int = 200,
        **kwargs,
    ) -> list[dict]:
        search_path = path or (str(self.base_path) if self.base_path else ".")
        limit = max(1, int(limit))

        if self._cmd == "rg":
            cmd = ["rg"]
            if case_insensitive:
                cmd.append("-i")
            if glob:
                cmd += ["--glob", glob]
            if type:
                cmd += ["--type", type]

            if mode == "content":
                # --with-filename forces the path prefix even on single-file
                # searches, so our parser has a consistent shape.
                cmd += ["-n", "--no-heading", "--with-filename"]
                if context > 0:
                    cmd += ["-C", str(int(context))]
                cmd += [query, search_path]
            else:
                cmd += ["-l", query, search_path]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode(errors="replace")

            if mode == "content":
                return _parse_content_lines(text, limit)
            return _parse_file_list(text, limit)

        # grep fallback — files-mode only
        cmd = ["grep", "-rl"]
        if case_insensitive:
            cmd.append("-i")
        cmd += [query, search_path]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return _parse_file_list(stdout.decode(errors="replace"), limit)


def _parse_file_list(text: str, limit: int) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("{"):
            out.append({"path": line})
            if len(out) >= limit:
                break
    return out


def _parse_content_lines(text: str, limit: int) -> list[dict]:
    """Parse `rg -n --no-heading` output: `path:line:text` or `path-line-text` (context).

    Uses a regex that tolerates Windows drive letters in the path (which
    contain their own `:`). `--` lines between context groups are skipped.
    """
    out = []
    for raw in text.splitlines():
        if not raw.strip() or raw.strip() == "--":
            continue
        m = _RG_LINE_RE.match(raw)
        if not m:
            continue
        out.append(
            {
                "path": m.group("path"),
                "line_number": int(m.group("lineno")),
                "text": m.group("text"),
            }
        )
        if len(out) >= limit:
            break
    return out
