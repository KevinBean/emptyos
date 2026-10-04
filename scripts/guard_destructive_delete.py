#!/usr/bin/env python3
"""PreToolUse hook — make every delete of user data ask the user first.

Sibling of `guard_git_safety.py` and `guard_daemon_safety.py` (same payload
handling, same fail-open posture), with one difference: it does not deny. It
returns ``permissionDecision: "ask"``, which forces a permission prompt for the
command even in auto mode, so the user approves or refuses each delete.

Why it exists: on 2026-09-23 an agent deleted a 6.8 GB, 22,482-file folder, a set
of installers and a zip with ``Remove-Item -Recurse -Force`` after telling the
user it would leave that to them. ``Remove-Item`` bypasses the Recycle Bin, so
nothing could be undone locally. A standing instruction ("finish the work") is
not permission to delete; only the user, per delete, can give that.

What counts as a delete:
  * shell deletes — rm, rmdir, unlink, del, erase, rd, shred, Remove-Item (and
    its ri alias), Clear-Content, Clear-RecycleBin, ``find -delete`` / ``-exec
    rm``, ``git clean -f``, ``rsync --delete``, ``robocopy /MIR`` or ``/PURGE`` —
    in command position, which includes after ``!`` or a shell keyword (``if``,
    ``then``, ``else``, ``do``, ``while`` …), behind a wrapper and its options
    (``sudo -u root``, ``nice -n 10``, ``env -u X``, ``xargs -0``, ``timeout
    60``, ``command``, ``nohup`` …), a ``\\`` alias bypass, or a ``/bin/``,
    ``/usr/bin/`` or ``/usr/local/bin/`` path;
  * the same through a language runtime — Python ``os.remove`` / ``os.unlink`` /
    ``os.rmdir`` / ``rmtree`` / ``Path.unlink`` / ``Path.rmdir``, and the indirect
    ways to reach them (``x = shutil.rmtree``, ``import os as o``, ``from os
    import …`` incl. ``*`` and a parenthesised list, ``__import__('os')``,
    ``getattr(os, 'remove')``); node ``fs.rm*`` / ``fs.unlink*`` incl.
    ``require('fs').rm`` and a destructured require; perl ``unlink`` /
    ``remove_tree``; ruby ``FileUtils.rm*`` / ``File.delete``; .NET
    ``[IO.File]::Delete`` / ``[IO.Directory]::Delete``, and in PowerShell any
    ``.Delete(`` or ``ForEach-Object Delete`` — matched anywhere in the raw
    command;
  * code the command hands to another interpreter, classified in turn (six
    levels deep): ``bash/sh -c`` incl. combined flags (``-lc``, ``-ec``),
    ``pwsh/powershell -c``, ``cmd /c`` (payload quoted or not), ``eval`` and
    ``Invoke-Expression`` (quoted or not), ``echo … | bash``, ``$( … )`` and
    backtick substitutions — in code, inside double quotes and inside an
    unquoted heredoc, not inside single quotes or a quoted ``<<'EOF'`` heredoc —
    a heredoc whose header runs a shell, a file written by a heredoc or ``echo
    >`` that a later segment runs with a shell, ``source``, ``.`` or by path, and
    the literal strings of ``os.system`` / ``subprocess`` calls inside Python or
    node code the command runs. An ``-EncodedCommand`` cannot be read, so it
    asks;
  * cloud and account deletes — ``gcloud … delete``, ``firebase … delete``,
    ``gh repo|release|gist … delete``, and any MCP tool whose name says
    delete / trash / remove.

Not asked about: ``git rm`` (git history keeps the file); Bash ``rmdir`` (it
refuses a non-empty directory; PowerShell's ``rmdir`` is ``Remove-Item`` and
still asks); a ``find`` read against an allow-list — roots, then only cache
``-name`` tests (at least one) and narrowing tests (``-type``, ``-path``,
``-prune`` …), then a final ``-delete`` or ``-exec rm`` taking only flags and
``{}`` — any other token (``!``, ``,``, ``-o``, ``(``, a second action) asks;
and a script the command runs but does not write (``bash x.sh``), whose
contents the guard cannot see.

The exemptions are deliberately few, because each one is a way for a real delete
to pass unasked. A shell delete is let through only when EVERY target is one of:

  * a path inside a session directory of Claude's own temp area
    (``<user>/AppData/Local/Temp/claude/<project>/<session>/…`` or
    ``/tmp/claude…/<a>/<b>/…``), checked after ``..`` is collapsed and anchored
    at the start of the path — never ``claude/`` itself or a project-wide glob;
  * a regenerable cache — a path with no ``..`` segment naming ``__pycache__``,
    ``.pytest_cache``, ``.ruff_cache``, ``.mypy_cache``, ``.hypothesis`` or a
    ``*.pyc`` file.

A ``$VAR`` / ``${VAR}`` / ``$env:VAR`` / ``%VAR%`` in a target is resolved only
when the command assigns it exactly once — in code (not inside quoted text or a
heredoc body), before the delete, as a statement of its own (not a ``VAR=x cmd``
prefix) — AND every other mention of the name, anywhere in the command, is a
plain read (``$VAR`` / ``${VAR}`` / ``$env:VAR`` not followed by ``=``, ``,``,
``+=``, ``in`` …). Shells have too many ways to rebind a name to list them
(``declare``, ``local``, ``read``, ``for … in``, ``printf -v``, ``${VAR:=…}``,
``Set-Variable``, ``$global:VAR``, ``foreach ($VAR in …)``, ``$a, $b = …``,
``Set-Item Variable:/VAR``), so any bare mention counts as one — except three
shapes that cannot rebind a name: a flag (``-t`` after whitespace), a drive
letter (``D:/`` for a one-letter name), and a path segment (``…/D--repo/…``)
that is not inside a provider path (``Variable:``, ``Env:``, ``Function:``,
``Alias:``). Names compare case-insensitively. A name the
command never mentions bare may fall back to TEMP / TMP / LOCALAPPDATA / APPDATA
/ USERPROFILE / HOME from the hook's environment. Anything still unresolved — a
loop variable, a relative path, a glob outside scratch, an ``xargs`` feed, a
``$(…)`` or backtick substitution — asks.

A runtime delete asks unless every absolute path the command names is scratch.
Its call is looked for in the raw command with two kinds of text removed first:
the body of a heredoc that is only written to a file (``cat > f <<EOF``, ``tee
f <<EOF``) — when ``f`` is a literal name, is not named again later, and nothing
later in the command runs an interpreter, a shell, ``source``, ``.`` or ``./`` —
and Python string literals inside a heredoc body that has no ``exec``/``eval``/
``compile``/``subprocess``/``system`` call and whose literals are not named again
later. Both are text the command writes, not code it runs.

A move (``mv``, ``move``, ``Move-Item``, ``ren``, ``robocopy /MOV``, ``rsync
--remove-source-files``) anywhere in the command switches every exemption off:
moving data into scratch and then deleting it is a delete of that data.

Always exits 0 — the decision is expressed via JSON, so a bug in this guard can
never hard-block every command.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import sys

from hook_common import tool_input

# Shell delete verbs, matched only in command position (start of the line or
# after ; & | ( or a newline), in the view with quoted data blanked, so the words
# inside a commit message or an echo cannot trigger it.
# A `VAR=value cmd` prefix is skipped, so the verb after it is still in position.
# So are shell keywords that precede a command (`if`, `then`, `else`, `do` …),
# command wrappers and their options (`sudo -u root rm`, `nice -n 10 rm`,
# `xargs -0 rm`, `timeout 60 rm`), a leading `\` that bypasses an alias, and an
# absolute `/bin/`, `/usr/bin/` or `/usr/local/bin/` path.
_WRAPPER = (
    r"(?:(?:if|then|else|elif|do|while|until|sudo|doas|command|builtin|exec|nohup|time|env|nice|busybox|xargs)"
    r"(?:\s+-{1,2}[^\s=]+(?:=\S+|\s+[^-\s]\S*)?)*\s+"
    r"|timeout\s+(?:-\S+\s+)*\S+\s+)"
)
_CMD_START = (
    r"(?:^|[;&|(\n{!])\s*"
    rf"{_WRAPPER}*"
    r"(?:[A-Za-z_]\w*=[^\s;&|]*\s+)*\\?(?:(?:/usr(?:/local)?)?/s?bin/)?"
)
_SHELL_VERBS = re.compile(
    _CMD_START
    + r"(rm|rmdir|unlink|del|erase|rd|ri|shred|Remove-Item|Clear-Content|Clear-RecycleBin)\b",
    re.I | re.M,
)
_PS_VERBS = {"ri", "remove-item", "clear-content", "clear-recyclebin", "del", "erase", "rd"}
_MOVE = re.compile(
    _CMD_START + r"(?:mv|move|mi|ren|rename|Move-Item|Rename-Item)\b"
    r"|\brobocopy\b[^;&|\n]*\s/MOVE?\b"
    r"|\brsync\b[^;&|\n]*\s--remove-source-files\b",
    re.I | re.M,
)
# `\;` ends an -exec but not the find, so it may sit inside the span.
_FIND_DELETE = re.compile(
    r"\bfind\b(?:[^;&|\n]|\\;)*?\s-(?:delete\b|(?:exec|execdir|ok|okdir)\s+(?:\S*/)?(?:rm|rmdir|unlink|shred|del)\b)",
    re.I,
)
_GIT_CLEAN = re.compile(r"\bgit\b[^;&|\n]*?\sclean\b[^;&|\n]*\s(?:-[a-zA-Z]*f|--force)", re.I)
_ROBOCOPY_MIRROR = re.compile(r"\brobocopy\b[^;&|\n]*\s/(?:MIR|PURGE)\b", re.I)
_RSYNC_DELETE = re.compile(r"\brsync\b[^;&|\n]*\s--delete(?:-\w+)?\b", re.I)
_CLOUD_DELETE = re.compile(
    r"\b(?:gcloud|firebase)\b[^;&|\n]*\bdelete\b"
    r"|\bgh\s+(?:repo|release|gist|secret|variable)\s+delete\b",
    re.I,
)

# Deletes through a language runtime — direct calls, and the indirect ways to
# reach the same functions (an alias, a from-import, getattr).
_RUNTIME_DELETE = re.compile(
    r"\bos\.(?:remove|unlink|rmdir|removedirs)\s*\("
    r"|\b(?:shutil\.)?rmtree\s*\("
    r"|\.(?:unlink|rmdir)\s*\("
    r"|(?:^|[;\s])[A-Za-z_]\w*\s*=\s*(?:shutil\.rmtree|os\.(?:remove|unlink|rmdir|removedirs))\b"
    r"|(?:^|[;:\"'])\s*from\s+(?:os|shutil|posix|nt)\s+import\s*(?:\*|\([^)]*\b(?:remove|unlink|rmdir|removedirs|rmtree)\b"
    r"|[^\n;]*\b(?:remove|unlink|rmdir|removedirs|rmtree)\b)"
    r"|\b__import__\s*\(\s*[\"'](?:os|shutil)[\"']\s*\)\s*\.\s*(?:remove|unlink|rmdir|removedirs|rmtree)\b"
    r"|\[(?:System\.)?IO\.(?:File|Directory)\]::Delete\s*\("
    r"|\b(?:fs|fsp|fsPromises|promises)\.(?:rm|rmSync|rmdir|rmdirSync|unlink|unlinkSync)\s*\("
    r"|\brequire\s*\([^)]*\)\s*\.\s*(?:rm|rmdir|unlink)\w*\s*\("
    r"|\{[^}]*\b(?:rm|rmSync|rmdir|rmdirSync|unlink|unlinkSync)\b[^}]*\}\s*=\s*require\s*\("
    r"|\b(?:rmSync|rmdirSync|unlinkSync)\s*\("
    r"|\bFileUtils\.(?:rm\w*|remove\w*)\b|\b(?:File|Dir)\.(?:delete|unlink|rmdir)\b"
    r"|\bremove_tree\s*\(|\bunlink\s*\(\s*[\"'$@]|\bunlink\s+(?:[\"'$@]|glob\b)",
    re.I | re.M,
)
# Patterns that need a string literal, so they run on text whose literals are kept.
_LITERAL_DELETE = re.compile(
    r"\bgetattr\s*\(\s*(?:os|shutil|pathlib|Path)\s*,\s*[\"'](?:remove|unlink|rmdir|removedirs|rmtree)[\"']"
)
# `import os as o` makes `o.remove(` a delete; checked per alias the code names.
_PY_MODULE_ALIAS = re.compile(r"\bimport\s+(os|shutil)\s+as\s+(\w+)")
# A delete on a file or directory object — PowerShell code only, since the same
# text in a Bash command is almost always prose.
_PS_OBJECT_DELETE = re.compile(
    r"\.Delete\s*\(|\b(?:ForEach-Object|%)\s+(?:-MemberName\s+)?Delete\b", re.I
)

# Code a command hands to another interpreter, which is classified in turn.
# A heredoc header names its shell as the first word, or after a pipe.
_HEADER_SHELL = re.compile(
    r"(?:^\s*|\|\s*)(?:sudo\s+|env\s+)?(bash|sh|zsh|dash|ksh|pwsh|powershell|cmd)(?:\.exe)?(?![\w\-])",
    re.I,
)
_PAYLOAD = r"(\"(?:[^\"\\]|\\.)*\"|'[^']*'|\S+)"
_SH_DASH_C = re.compile(
    r"(?<![\w.\-])(bash|sh|zsh|dash|ksh)(?:\.exe)?\b[^;&|\n]*?\s-[A-Za-z]*c[A-Za-z]*\s+(?:--\s+)?" + _PAYLOAD
)
_PS_DASH_C = re.compile(
    r"(?<![\w.\-])(pwsh|powershell)(?:\.exe)?\b[^;&|\n]*?\s-(c|Command|EncodedCommand|ec|enc|e)\s+" + _PAYLOAD,
    re.I,
)
_CMD_SLASH_C = re.compile(r"(?<![\w.\-])cmd(?:\.exe)?\s+/{1,2}[cCkK]\s+([^\n]+)")
_EVAL = re.compile(
    r"(?<![\w.\-])(eval|iex|Invoke-Expression)\s+(?:-Command\s+)?"
    r"(\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^\n;&|]+)",
    re.I,
)
# Text piped or written into a shell: `echo '…' | bash`, `echo '…' > s.sh`.
_PIPE_TO_SHELL = re.compile(
    r"\b(?:echo|printf|cat)\b([^|\n]*)\|\s*(bash|sh|zsh|dash|pwsh|powershell|iex|Invoke-Expression)\b", re.I
)
_ECHO_TO_FILE = re.compile(r"\b(?:echo|printf)\b([^>\n]*)>>?\s*(\"[^\"]*\"|'[^']*'|[^\s;&|]+)")
_SPAWN = re.compile(
    r"\b(?:os\.(?:system|popen|exec\w*|spawn\w*)|subprocess\.\w+|Popen|execSync|spawnSync|execFileSync)\s*\("
)
# Interpreter code given inline: `python -c "…"`, `node -e "…"`.
_INLINE_CODE = re.compile(
    r"(?<![\w.\-])(?:python[0-9.]*|py|node|perl|ruby)(?:\.exe)?\b[^;&|\n]*?\s-[ce]\s+"
    r"(\"(?:[^\"\\]|\\.)*\"|'[^']*')",
    re.I,
)
# What precedes a file name when a command runs it with a shell or by path.
_RUN_PREFIX = re.compile(
    r"^\s*(?:(?:bash|sh|zsh|dash|ksh|source|\.|pwsh|powershell|cmd(?:\.exe)?\s+/[cCkK])"
    r"(?:\s+-\S+)*\s+(?:<\s*)?)?[\"']?(?:\.[/\\])?[^\s\"']*$",
    re.I,
)

# MCP tools that delete or trash something.
_MCP_DESTRUCTIVE = re.compile(r"(?:^|_)(?:delete|trash|remove|purge|wipe|destroy)", re.I)

# A session directory inside Claude's temp area, on a canonical lower-cased path.
# The project and session segments must be literal names, never a glob, so a
# delete can never reach claude/ itself or every session of a project.
_SEG = r"[^/*?\[\]]+"
_SCRATCH_ROOT = re.compile(
    rf"^(?:[a-z]:)?/users/{_SEG}/appdata/local/temp/claude/{_SEG}/{_SEG}(?:/|$)"
    rf"|^/tmp/claude{_SEG}?/{_SEG}/{_SEG}(?:/|$)"
)
# An absolute Windows or POSIX-drive path token.
_ABS_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|(?<![\w.])/[a-z]/|(?<![\w.])/tmp/)[^\s\"'`;|&<>),]*")

# PowerShell parameters whose NEXT token is a value, never a path to delete.
_VALUE_PARAMS = {
    "-erroraction", "-warningaction", "-informationaction", "-errorvariable",
    "-warningvariable", "-outvariable", "-filter", "-include", "-exclude",
}

# Regenerable caches — deleting one loses nothing a rerun does not rebuild.
_CACHE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".hypothesis"}

# Same spans `hook_common.command_code_view` blanks, but length-preserving, so a
# verb found in the blanked view can be read back from the raw command. The
# delimiter may be tab-indented, as `<<-` allows.
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?^\t*\2$", re.S | re.M)
_QUOTED_HEREDOC = re.compile(r"<<-?\s*(['\"])(\w+)\1.*?^\t*\2$", re.S | re.M)
_INTERPRETER_WORD = re.compile(
    r"(?:^\s*|\|\s*)(?:python[0-9.]*|py|node|perl|ruby)(?:\.exe)?(?![\w\-])", re.I
)
_SEGMENT_END = re.compile(r"[;&|\n)}]")
# A line continuation: `\` in a POSIX shell, a backtick in PowerShell. A Bash
# backtick at the end of a line closes a substitution and must stay.
_CONTINUATION = {"PowerShell": re.compile(r"`\r?\n")}
_CONTINUATION_DEFAULT = re.compile(r"\\\r?\n")

# Variable assignments as statements of their own: bash `NAME=value;`,
# PowerShell `$name = value` / `$env:NAME = value`.
_STMT_END = r"(?=\s*(?:;|&&|\|\||\n|$))"
_ASSIGN_SH = re.compile(
    r"(?:^|[;&|\n(]|\bexport\s+)\s*([A-Za-z_]\w*)=(\"[^\"]*\"|'[^']*'|[^\s;&|]*)" + _STMT_END, re.M
)
_ASSIGN_PS = re.compile(r"\$(?:env:)?([A-Za-z_]\w*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s;]+)" + _STMT_END, re.M | re.I)
# What follows a `$name` read when it is really a write.
_REF_WRITE = re.compile(r"\s*(?:,|[+\-*/.%]?=(?!=)|\+\+|--|in\b)", re.I)
_VAR_REF = re.compile(r"\$env:([A-Za-z_]\w*)|\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)|%([A-Za-z_]\w*)%", re.I)

# Heredocs: a header that only writes the body to a file, and Python literals.
_WRITE_HEADER = re.compile(r"(?:^|[;&(]\s*)\s*(?:cat\s*>>?\s*|tee\s+(?:-a\s+)?)(\"[^\"]*\"|'[^']*'|[^\s<;&|]+)")
_PY_STRING = re.compile(r"'''.*?'''|\"\"\".*?\"\"\"|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"", re.S)
# Anything after a written heredoc that could run the file it wrote.
_RUNNER = re.compile(
    r"(?<![\w.\-])(?:python[0-9.]*|py|pwsh|powershell|bash|sh|zsh|node|perl|ruby|source|exec|eval|xargs)"
    r"(?:\.exe)?(?![\w\-])|(?:^|[;&|(]\s*)\.\s|\./",
    re.I | re.M,
)
_RUNS_STRINGS = re.compile(r"\b(?:exec|eval|compile|subprocess|system|Popen|spawn)\b")

# Overridable for tests; the hook runs on the box whose temp dirs these are.
ENV: dict[str, str] = dict(os.environ)
_ENV_KEYS = ("TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME")


def _blank(text: str, rx: re.Pattern[str]) -> str:
    return rx.sub(lambda m: " " * len(m.group(0)), text)


def _code_view(cmd: str) -> str:
    """Quoted spans and heredoc bodies blanked, same length as `cmd`."""
    return _blank(_blank(cmd, _HEREDOC), _QUOTED)


def _unquote(tok: str) -> str:
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    return tok


def _write_sites(raw: str, name: str) -> set[int]:
    """Positions where `name` is mentioned other than as a plain read.

    A plain read is `$name`, `${name}` or `$env:name` not followed by a write
    (`=`, `+=`, `,`, `++`, `in`). Every other mention — a bare word, `$global:`,
    `${name:=…}`, `foreach ($name in …)`, `Variable:/name` — is counted, since
    each is a way to rebind the name that the assignment regexes do not model.
    Not counted: a flag (`-t`), a one-letter drive (`D:/`), and a path segment
    (`…/D--repo/…`) outside a provider path, none of which can rebind it.
    """
    n = re.escape(name)
    sites: set[int] = set()
    for m in re.finditer(rf"(?<![\w$]){n}(?!\w)", raw, re.I):
        before = raw[max(0, m.start() - 5):m.start()].lower()
        if before.endswith("${") and raw[m.end():m.end() + 1] == "}":
            end = m.end() + 1                     # `${name}`
        elif before.endswith("$env:"):
            end = m.end()                         # `$env:name`
        else:
            before2 = raw[max(0, m.start() - 2):m.start()]
            nxt = raw[m.end():m.end() + 2]
            flag = before2[-1:] == "-" and (len(before2) < 2 or before2[0].isspace())
            drive = len(name) == 1 and nxt in (":/", ":\\") and not before2[-1:].isalnum()
            segment = before2[-1:] in ("/", "\\", ".") or nxt[:1] in ("/", "\\", ".")
            token = re.split(r"[\s\"'(=,;]", raw[:m.start()])[-1]
            provider = re.search(r"(?:variable|env|function|alias)\s*:", token, re.I)
            if (flag or drive or segment) and not provider:
                continue                          # a flag, a drive, a path segment
            sites.add(m.start())                  # a bare mention, or `Variable:/name`
            continue
        if _REF_WRITE.match(raw, end):
            sites.add(m.start())
    for m in re.finditer(rf"\${n}(?!\w)", raw, re.I):
        if _REF_WRITE.match(raw, m.end()):
            sites.add(m.start() + 1)
    return sites


def _assignments(cmd: str, view: str, before: int) -> dict[str, str]:
    """Names (lower-cased) whose value is provable at `before`: one statement
    assignment before it, and no other write or bare mention anywhere."""
    raw = _blank(cmd, _HEREDOC)
    stmts: dict[str, list[tuple[int, str]]] = {}
    for rx in (_ASSIGN_SH, _ASSIGN_PS):
        for m in rx.finditer(cmd):
            if view[m.start(1)] == " ":          # inside quoted text or a heredoc
                continue
            stmts.setdefault(m.group(1).lower(), []).append(
                (m.start(1), "" if m.start() >= before else _unquote(m.group(2)))
            )
    found: dict[str, str] = {}
    for name, vals in stmts.items():
        if len(vals) == 1 and vals[0][1] and _write_sites(raw, name) == {vals[0][0]}:
            found[name] = vals[0][1]
    for key in _ENV_KEYS:
        name = key.lower()
        if name not in stmts and key in ENV and not _write_sites(raw, name):
            found[name] = ENV[key]
    return found


def _expand(tok: str, assigns: dict[str, str]) -> str:
    """Resolve `$VAR`-style references; unknown ones stay as written."""
    def sub(m: re.Match[str]) -> str:
        name = next(g for g in m.groups() if g).lower()
        val = assigns.get(name)
        return _unquote(val) if val is not None else m.group(0)
    for _ in range(3):
        new = _VAR_REF.sub(sub, tok)
        if new == tok:
            break
        tok = new
    return tok


def _canonical(path: str) -> str | None:
    """Absolute path with `..` collapsed, forward slashes, lower case, Git Bash
    `/c/…` folded to `c:/…`; None for a relative path."""
    p = path.replace("\\", "/")
    p = p.removeprefix("//?/")                   # Windows extended-length prefix
    m = re.match(r"^/([A-Za-z])(/.*|$)", p)
    if m:
        p = f"{m.group(1)}:{m.group(2) or '/'}"
    m = re.match(r"^([A-Za-z]:)(/.*)?$", p)
    drive, rest = (m.group(1), m.group(2) or "/") if m else ("", p)
    if not rest.startswith("/"):
        return None
    return (drive + posixpath.normpath(rest)).lower()


def _is_scratch(path: str) -> bool:
    canon = _canonical(path)
    return bool(canon and _SCRATCH_ROOT.match(canon))


def _is_cache(path: str) -> bool:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts or ".." in parts:
        return False
    return bool(set(parts) & _CACHE_DIRS) or parts[-1].endswith(".pyc")


def _tokens(segment: str) -> list[str]:
    lex = shlex.shlex(segment, posix=True)
    lex.whitespace_split = True
    lex.escape = ""          # keep Windows backslashes literal
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:
        return segment.split()


def _shell_targets(cmd: str, view: str, start: int, split_commas: bool) -> list[str]:
    """The non-flag arguments of the delete verb whose match ends at `start`."""
    m = _SEGMENT_END.search(view, start)
    end = m.start() if m else len(view)
    targets: list[str] = []
    skip = False
    for t in _tokens(cmd[start:end]):
        if skip:
            skip = False                  # the value of a parameter, not a path
        elif t.startswith("-"):
            skip = t.lower() in _VALUE_PARAMS
        elif split_commas:
            targets.extend(p for p in t.split(",") if p)   # PowerShell array
        else:
            targets.append(t)
    return targets


def _unsafe_shell_delete(cmd: str, view: str, tool_name: str, exempt: bool) -> str | None:
    """The verb of the first shell delete whose targets are not all provably
    harmless, or None."""
    for m in _SHELL_VERBS.finditer(view):
        verb = m.group(1)
        if verb.lower() == "rmdir" and tool_name == "Bash":
            continue                      # refuses a non-empty dir — nothing lost
        if not exempt or "xargs" in m.group(0).lower():
            return verb                   # a move happened, or stdin feeds targets
        split = tool_name == "PowerShell" or verb.lower() in _PS_VERBS
        targets = _shell_targets(cmd, view, m.end(), split)
        if not targets:
            return verb
        assigns = _assignments(cmd, view, m.start())
        for raw in targets:
            if "$(" in raw or "`" in raw:
                return verb               # a substitution runs a command of its own
            path = _expand(raw, assigns)
            if _VAR_REF.search(path):
                return verb               # unresolved variable
            if not (_is_scratch(path) or _is_cache(path)):
                return verb
    return None


def _runtime_text(cmd: str, strip_literals: bool = True) -> str:
    """The raw command minus text it only writes: bodies of heredocs written to a
    file not named again, and (unless `strip_literals` is False) Python literals
    in heredocs that cannot run them."""
    out = cmd
    for m in _HEREDOC.finditer(cmd):
        line_start = cmd.rfind("\n", 0, m.start()) + 1
        header = cmd[line_start:cmd.find("\n", m.start()) if "\n" in cmd[m.start():] else len(cmd)]
        body_start = cmd.find("\n", m.start()) + 1
        body = cmd[body_start:m.end()]
        later = cmd[m.end():]
        w = _WRITE_HEADER.search(header)
        target = _unquote(w.group(1)) if w else ""
        if (
            w
            and "|" not in header
            and not re.search(r"[$*?`\[]", target)
            and not _named_later(target, later)
            and not _RUNNER.search(later)
        ):
            out = out[:body_start] + " " * len(body) + out[m.end():]
            continue
        if not strip_literals or _RUNS_STRINGS.search(body):
            continue
        literals = [_unquote(s.group(0).strip("'\"")) for s in _PY_STRING.finditer(body)]
        if any(_named_later(lit, later) for lit in literals):
            continue
        stripped = _PY_STRING.sub(lambda s: re.sub(r"[^\n]", " ", s.group(0)), body)
        out = out[:body_start] + stripped + out[m.end():]
    return out


def _named_later(name: str, later: str) -> bool:
    """True when the file `name` (by basename) appears in `later`."""
    base = name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return len(base) >= 3 and base in later


def _scratch_only(cmd: str, view: str) -> bool:
    """True when every absolute path the command names is inside scratch."""
    assigns = _assignments(cmd, view, len(cmd))
    paths = _ABS_PATH.findall(_expand(cmd, assigns))
    return bool(paths) and all(_is_scratch(p) for p in paths)


_FIND_VALUE_TESTS = {"-type", "-maxdepth", "-mindepth", "-path", "-ipath", "-newer", "-mtime", "-mmin", "-size"}
_FIND_FLAG_TESTS = {"-prune", "-print", "-depth", "-xdev", "-mount"}


def _find_deletes_only_caches(cmd: str, m: re.Match[str]) -> bool:
    """True when a `find` provably deletes only caches. Read against an allow-list,
    never a deny-list: `find <roots> <tests…> <action>` where every test is a
    `-name` of a cache (`__pycache__`, `*.pyc` …) or a narrowing test (`-type`,
    `-path`, `-prune` …), there is at least one such `-name`, and the action —
    `-delete`, or `-exec rm` taking only flags and `{}` — is the last thing in
    the command segment. Any other token (`!`, `\\!`, `,`, `-o`, `-not`, `(`, a
    second action) means it is not proven, and the delete asks."""
    rest = cmd[m.start():]
    end = re.search(r"(?<!\\)[;&|\n]", rest)
    tokens = _tokens(rest[: end.start()] if end else rest)
    tokens = [t for t in tokens if not re.match(r"^\d?>>?", t)]   # redirections
    if not tokens or tokens[0] != "find":
        return False
    i = 1
    while i < len(tokens) and not re.match(r"[-!\\(),]", tokens[i]):
        i += 1                                        # the search roots
    names = 0
    while i < len(tokens):
        t = tokens[i].lower()
        if t in ("-name", "-iname") and i + 1 < len(tokens):
            if tokens[i + 1] not in _CACHE_DIRS and tokens[i + 1] != "*.pyc":
                return False
            names += 1
            i += 2
        elif t in _FIND_VALUE_TESTS and i + 1 < len(tokens):
            i += 2
        elif t in _FIND_FLAG_TESTS:
            i += 1
        elif t == "-delete":
            return names > 0 and i == len(tokens) - 1
        elif t in ("-exec", "-execdir"):
            args = tokens[i + 1:]
            if not args or re.sub(r"^(?:/usr)?/bin/", "", args[0]) != "rm":
                return False
            for j, a in enumerate(args[1:], start=1):
                if a in ("+", ";", "\\;"):
                    return names > 0 and i + 1 + j == len(tokens) - 1
                if not (a.startswith("-") or a == "{}"):
                    return False
            return False
        else:
            return False
    return False


def _balanced(text: str, open_idx: int) -> str:
    """The text inside the parenthesis opening at `open_idx`, skipping parens
    inside quotes or after a backslash (to the end if it never closes)."""
    depth = 0
    quote = ""
    i = open_idx
    while i < len(text):
        ch = text[i]
        if not quote and text.startswith("<<", i):
            hd = _HEREDOC.match(text, i)
            if hd:                                    # a heredoc body is prose
                i = hd.end()
                continue
        if ch == "\\":
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
        i += 1
    return text[open_idx + 1:]


def _runs_file_later(name: str, later: str) -> bool:
    """True when a line of `later` runs the file `name` with a shell or by path."""
    base = _unquote(name).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if len(base) < 3:
        return False
    for seg in re.split(r"[;&|(\n]", later):
        idx = seg.find(base)
        while idx >= 0:
            if _RUN_PREFIX.match(seg[:idx]):          # `bash f`, `source f`, `./f`, `f`
                return True
            idx = seg.find(base, idx + 1)
    return False


def _shell_tool(word: str) -> str:
    word = word.lower()
    if word in ("pwsh", "powershell"):
        return "PowerShell"
    return "cmd" if word == "cmd" else "Bash"


_MAX_DEPTH = 6


def _aliased_module_delete(text: str) -> re.Match[str] | None:
    """`import os as o` … `o.remove(` — a delete through a module alias."""
    for a in _PY_MODULE_ALIAS.finditer(text):
        m = re.search(rf"\b{re.escape(a.group(2))}\.(?:remove|unlink|rmdir|removedirs|rmtree)\s*\(", text)
        if m:
            return m
    return None


def _nested(cmd: str, tool_name: str) -> list[tuple[str, str]]:
    """Code the command hands to another interpreter, as (tool, code) pairs.
    A tool of "" means code the guard cannot read (an encoded command)."""
    found: list[tuple[str, str]] = []
    view = _code_view(cmd)
    # Substitutions run in code, inside double quotes and inside an unquoted
    # heredoc; not inside single quotes or a quoted heredoc (`<<'EOF'`), where
    # a backtick is usually markdown.
    subst_view = _blank(_blank(cmd, _QUOTED_HEREDOC), re.compile(r"'[^']*'"))
    interp_code: list[str] = []                    # Python / node code the command runs

    def in_code(pos: int) -> bool:
        return view[pos] != " "

    def payload(raw: str) -> str:
        return _unquote(raw.strip()).replace('\\"', '"')

    for m in _SH_DASH_C.finditer(cmd):
        if in_code(m.start()):
            found.append(("Bash", payload(m.group(2))))
    for m in _PS_DASH_C.finditer(cmd):
        if not in_code(m.start()):
            continue
        if m.group(2).lower() in ("encodedcommand", "ec", "enc", "e"):
            found.append(("", ""))
        else:
            found.append(("PowerShell", payload(m.group(3))))
    for m in _CMD_SLASH_C.finditer(cmd):
        if in_code(m.start()):
            found.append(("cmd", payload(m.group(1))))
    for m in _EVAL.finditer(cmd):
        if in_code(m.start()):
            found.append(("Bash" if m.group(1).lower() == "eval" else "PowerShell", payload(m.group(2))))
    for m in _PIPE_TO_SHELL.finditer(cmd):         # echo '…' | bash
        if in_code(m.start()):
            found.append((_shell_tool(m.group(2)), " ".join(_tokens(m.group(1)))))
    for m in _ECHO_TO_FILE.finditer(cmd):          # echo '…' > s.sh; bash s.sh
        if in_code(m.start()) and _runs_file_later(m.group(2), cmd[m.end():]):
            found.append(("Bash", " ".join(_tokens(m.group(1)))))
    for m in re.finditer(r"\$\(", subst_view):    # command substitution
        found.append((tool_name, _balanced(cmd, m.end() - 1)))
    if tool_name != "PowerShell":                  # a PowerShell backtick is an escape
        for m in re.finditer(r"`([^`\n]+)`", subst_view):
            found.append((tool_name, cmd[m.start(1):m.end(1)]))
    for m in _INLINE_CODE.finditer(cmd):
        if in_code(m.start()):
            interp_code.append(payload(m.group(1)))
    for m in _HEREDOC.finditer(cmd):               # a heredoc a shell or interpreter runs
        line_start = cmd.rfind("\n", 0, m.start()) + 1
        line_end = cmd.find("\n", m.start())
        header = cmd[line_start:line_end if line_end >= 0 else len(cmd)]
        body = cmd[line_end + 1:m.end()].rsplit("\n", 1)[0] if line_end >= 0 else ""
        hview = _code_view(header)
        shell = _HEADER_SHELL.search(hview)
        w = _WRITE_HEADER.search(header)
        if shell:
            found.append((_shell_tool(shell.group(1)), body))
        elif w and _runs_file_later(w.group(1), cmd[m.end():]):
            found.append(("Bash", body))
        elif _INTERPRETER_WORD.search(hview):
            interp_code.append(body)
    for code in interp_code:                       # os.system / subprocess strings
        for m in _SPAWN.finditer(code):
            args = _balanced(code, m.end() - 1)
            words = [_unquote(s.group(0)) for s in _PY_STRING.finditer(args)]
            if words:                              # no literal: nothing to read
                found.append(("Bash", " ".join(words)))
    return found


def _delete_kind(cmd: str, tool_name: str, depth: int = 0) -> str | None:
    """Name the kind of delete a shell command performs, or None."""
    cmd = _CONTINUATION.get(tool_name, _CONTINUATION_DEFAULT).sub("  ", cmd)   # join continued lines
    view = _code_view(cmd)
    exempt = not _MOVE.search(view)
    verb = _unsafe_shell_delete(cmd, view, tool_name, exempt)
    if verb:
        return verb
    for inner_tool, code in _nested(cmd, tool_name):
        if not inner_tool or depth >= _MAX_DEPTH:
            return "a command the guard cannot read"
        kind = _delete_kind(code, inner_tool, depth + 1)
        if kind:
            return kind
    for m in _FIND_DELETE.finditer(view):
        if not _find_deletes_only_caches(cmd, m):
            return "find -delete or -exec rm"
    for rx, label in (
        (_GIT_CLEAN, "git clean -f"),
        (_ROBOCOPY_MIRROR, "robocopy /MIR or /PURGE"),
        (_RSYNC_DELETE, "rsync --delete"),
        (_CLOUD_DELETE, "a cloud or repository delete"),
    ):
        if rx.search(view):
            return label
    text = _runtime_text(cmd)
    m = (
        _RUNTIME_DELETE.search(text)
        or _aliased_module_delete(text)
        or _LITERAL_DELETE.search(_runtime_text(cmd, strip_literals=False))
    )
    if not m and tool_name == "PowerShell":
        m = _PS_OBJECT_DELETE.search(_blank(text, _QUOTED))
    if m and not (exempt and _scratch_only(cmd, view)):
        return m.group(0).rstrip("( ")
    return None


def classify(tool_name: str, cmd: str) -> str | None:
    """Return a reason to ask the user, or None to let the call through."""
    if tool_name.startswith("mcp__"):
        verb = tool_name.split("__")[-1]
        if _MCP_DESTRUCTIVE.search(verb):
            return f"calls {tool_name}, which deletes or trashes something"
        return None
    if not cmd:
        return None
    kind = _delete_kind(cmd, tool_name)
    if not kind:
        return None
    return f"deletes data ({kind})"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = str(payload.get("tool_name") or "")
    shell = tool_name in ("Bash", "PowerShell", "exec_command", "shell")
    if not (shell or tool_name.startswith("mcp__")):
        return 0

    inp = tool_input(payload)
    cmd = str(inp.get("command") or inp.get("cmd") or "") if shell else ""
    reason = classify(tool_name, cmd)
    if not reason:
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": (
                f"This command {reason}. Deleting data needs the user's own "
                "approval for this specific delete; a general instruction to finish "
                "a task is not that approval. Say what will be deleted and whether "
                "it can be recovered, then let the user decide."
            ),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
