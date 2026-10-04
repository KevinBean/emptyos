#!/usr/bin/env python3
"""Event-wiring audit — drift, undeclared emits, dead events.

EmptyOS's philosophy is "events over imports; the topology graph IS the
architecture", but nothing verified that emitters and listeners actually
agree. The 2026-07-10 architecture review found ~60% of emitted event types
have zero listeners and a dozen ``@on_event`` handlers listening for names
nobody emits (silent no-ops). This scanner is the graduated form of that
one-off sweep, per .claude/rules/audits.md.

Three finding classes:

  DRIFT (A, exit-code signal)  a listener (``@on_event`` / ``events.on``)
        names an event no code emits and no manifest declares. The handler
        is a silent no-op — either the emitter was renamed/removed or the
        listener has a typo. This is a bug class, not style.
  PHANTOM (A2, exit-code signal)  a listener names an event that some
        manifest DECLARES as emitted but no code actually emits — an
        aspirational declaration whose implementation was never wired (or
        was removed). Same silent no-op at runtime as DRIFT; the manifest
        just makes it look healthy. Fix by wiring the emit at the natural
        code site, or deleting both listener and declaration.
  UNDECLARED (B, advisory)     an app's ``self.emit("x")`` is not declared
        in its manifest ``[provides.events] emits``. Harmless at runtime,
        but the live topology graph builds event edges from manifests, so
        undeclared emits are invisible to /api/topology and its
        improvements audit undercounts.
  DEAD (C, informational)      an event is emitted but nothing listens —
        neither Python (``@on_event`` / ``events.on``) nor any frontend
        (pages JS/HTML can subscribe over the realtime WebSocket, so the
        name appearing in JS/HTML counts as consumed). Deliberately NOT a
        failure: dead emits are a cheap forward-compatible audit stream
        (2026-07-10 decision — keep emits, fix visibility).

Exit code = number of DRIFT findings (exit-code-as-signal; registered
gate=False in preflight so drift warns rather than blocks).

Pure file I/O — does NOT import emptyos.kernel (no syslog handle; safe while
the daemon is up, per .claude/rules/daemon-handling.md).

Usage::

    python scripts/check_event_wiring.py            # human report
    python scripts/check_event_wiring.py --json     # agent-cli envelope
    python scripts/check_event_wiring.py --dead     # also list every dead event
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import io
import json
import re
import sys
import token as _token
import tokenize
import tomllib
from collections import defaultdict
from pathlib import Path

from check_base import REPO_ROOT, SKIP_DIR_PARTS

# Roots scanned for emits + listeners. `emptyos` and `plugins` matter: the
# kernel/runtime emit framework events (vault:changed, think:executed,
# kernel:started, job:*) and plugins emit device events (hotkey:pressed,
# telegram:inbound) that app listeners legitimately depend on.
PY_ROOTS = ["apps", "plugins", "emptyos", "engines"]

# Listener names matching these fnmatch patterns are never drift even without
# a literal emitter. Keep this SHORT — every entry here is a hole in the
# check. Current entries are events produced dynamically (constructed names)
# or by external processes:
#   agent:*        the agent app re-broadcasts internal loop events whose
#                  names are built dynamically per tool/phase
#
# `wake-word:*` was dropped 2026-07-29: its only "listener" was a commented-out
# `@on_event` sketch in braindump documenting a hook that v1 deliberately does
# not wire. Token-based scanning stopped reading comments as listeners, so the
# entry excused nothing and only widened the hole.
ALLOW_ORPHAN_LISTENERS = [
    "agent:*",
]

# Event names are `<source>:<verb>` — requiring the colon keeps literals like
# "..." or bare words out of the match set.
_NAME = r"[a-z0-9_.\-*]+:[a-z0-9_:.\-*]+"
_NAME_RE = re.compile(rf"^{_NAME}$")
DYNAMIC_EMIT_RE = re.compile(r"\.emit\(\s*[a-zA-Z_][a-zA-Z0-9_.]*\s*[,)]")
_ANY_NAME_LITERAL_RE = re.compile(rf"""["']({_NAME})["']""")

# Call sites are found by TOKEN ADJACENCY, not by regex over raw source.
# A regex cannot tell `self.emit("x:y")` apart from the same text quoted
# inside a docstring or trailing a `#` — and both exist on purpose here: the
# agent app ships an emit example inside a prompt template, and app-builder
# documents its own matcher in a comment. Both were reported as undeclared
# emits forever. Tokenising removes the whole class: a docstring is ONE
# STRING token, so the `.` `emit` `(` sequence never appears inside it, and
# COMMENT tokens are dropped before matching.
#
# Each entry is (trigger token sequence, bucket). Every event-name-shaped
# string literal in the call's FIRST argument is recorded, which covers the
# plain call, the `"a:x" if cond else "a:y"` conditional, and any other
# literal-bearing expression without a rule per shape.
_CALL_TRIGGERS: tuple[tuple[tuple[str, ...], str], ...] = (
    ((".", "emit", "("), "emit"),
    (("@", "on_event", "("), "listen"),
    ((".", "events", ".", "on", "("), "listen"),
)
# SDK helpers (vault_project_create/update/delete, …) emit on the caller's
# behalf via an `event_name="x:y"` kwarg — that call site IS the emit.
_KWARG_TRIGGER: tuple[str, ...] = ("event_name", "=")


def _significant_tokens(text: str) -> list[tokenize.TokenInfo]:
    """Tokens with comments and layout removed; [] if the file won't tokenize."""
    drop = {
        _token.COMMENT, _token.NL, _token.NEWLINE, _token.INDENT,
        _token.DEDENT, _token.ENCODING, _token.ENDMARKER,
    }
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return []
    return [t for t in toks if t.type not in drop]


def _literal(tok: tokenize.TokenInfo) -> str | None:
    """The value of a plain string token, or None (f-strings, bytes, junk)."""
    if tok.type != _token.STRING:
        return None
    try:
        v = ast.literal_eval(tok.string)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    return v if isinstance(v, str) else None


def _first_arg_literals(toks: list[tokenize.TokenInfo], i: int) -> list[str]:
    """Event-name literals in the argument starting just after `toks[i]` = '('.

    Stops at the top-level comma or the closing paren, so a second argument
    (an event payload, which routinely contains colon-shaped strings) can
    never be mistaken for the event name.
    """
    out: list[str] = []
    depth = 1
    for t in toks[i + 1:]:
        if t.type == _token.OP:
            if t.string in "([{":
                depth += 1
            elif t.string in ")]}":
                depth -= 1
                if depth == 0:
                    break
            elif t.string == "," and depth == 1:
                break
        val = _literal(t)
        if val and _NAME_RE.match(val):
            out.append(val)
    return out


def _iter_files(roots: list[str], suffixes: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        p = REPO_ROOT / r
        if not p.exists():
            continue
        for suffix in suffixes:
            for f in p.rglob(f"*{suffix}"):
                if SKIP_DIR_PARTS & set(f.parts):
                    continue
                out.append(f)
    return out


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO_ROOT)).replace("\\", "/")


def scan_python() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return (emits, listens): event name -> [file:line, ...].

    Dynamic-emit heuristic: a file that calls ``self.emit(<identifier>...)``
    builds its event name in a variable (e.g. ``event = "x:perfect" if ok
    else "x:attempt"``; ``self.emit(event, ...)``). For such files, every
    quoted event-name-shaped literal in the file counts as an emit —
    otherwise those events would be flagged as phantom/drift false
    positives. Listener-only files (like the reactor mixins, which repeat
    the name inside ``_log_action``) never take this path because they
    contain no variable-emit call.
    """
    emits: dict[str, list[str]] = defaultdict(list)
    listens: dict[str, list[str]] = defaultdict(list)
    for f in _iter_files(PY_ROOTS, (".py",)):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        toks = _significant_tokens(text)
        for i, tok in enumerate(toks):
            for trigger, kind in _CALL_TRIGGERS:
                end = i + len(trigger)
                if end > len(toks):
                    continue
                if all(toks[i + n].string == s for n, s in enumerate(trigger)):
                    bucket = emits if kind == "emit" else listens
                    for name in _first_arg_literals(toks, end - 1):
                        bucket[name].append(f"{_rel(f)}:{tok.start[0]}")
            if (
                i + 2 < len(toks)
                and tok.string == _KWARG_TRIGGER[0]
                and toks[i + 1].string == _KWARG_TRIGGER[1]
            ):
                val = _literal(toks[i + 2])
                if val and _NAME_RE.match(val):
                    emits[val].append(f"{_rel(f)}:{tok.start[0]}")
        if DYNAMIC_EMIT_RE.search(text):
            for m in _ANY_NAME_LITERAL_RE.finditer(text):
                name = m.group(1)
                if "*" not in name:
                    site = f"{_rel(f)}:{text.count(chr(10), 0, m.start()) + 1} (dynamic)"
                    if site not in emits[name]:
                        emits[name].append(site)
    return emits, listens


def scan_manifests() -> tuple[dict[str, set[str]], dict[str, str]]:
    """Return (declared EMITS per app id, app dir -> app id).

    Only `emits` declarations count — a manifest `listens` entry must never
    excuse a drifted listener (that would let the bug vouch for itself).
    """
    declared: dict[str, set[str]] = {}
    dir_to_id: dict[str, str] = {}
    for mf in _iter_files(["apps", "plugins"], ("manifest.toml",)):
        if mf.name != "manifest.toml":
            continue
        try:
            data = tomllib.loads(mf.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        app_id = (data.get("app") or data.get("plugin") or {}).get("id", "")
        if not app_id:
            continue
        dir_to_id[_rel(mf.parent)] = app_id
        ev = (data.get("provides") or {}).get("events") or {}
        declared[app_id] = set(ev.get("emits") or [])
    return declared, dir_to_id


def scan_frontend_text() -> str:
    """One big haystack of all pages/static JS+HTML, for dead-event checks.

    The realtime bridge forwards every bus event to browser subscribers, so a
    dead-in-Python event may have a real JS consumer — its name appearing
    anywhere in frontend source counts as consumed.
    """
    chunks: list[str] = []
    for f in _iter_files(["apps", "emptyos/web/static"], (".js", ".html")):
        try:
            chunks.append(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)


def app_id_for(path_str: str, dir_to_id: dict[str, str]) -> str:
    """Longest manifest-dir prefix match for a file path."""
    best = ""
    best_id = ""
    for d, app_id in dir_to_id.items():
        if path_str.startswith(d + "/") and len(d) > len(best):
            best, best_id = d, app_id
    return best_id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="agent-cli JSON envelope")
    ap.add_argument("--dead", action="store_true", help="list every dead event")
    args = ap.parse_args()

    emits, listens = scan_python()
    declared, dir_to_id = scan_manifests()
    all_declared = set().union(*declared.values()) if declared else set()

    # A) DRIFT — listener with no emitter anywhere.
    # A2) PHANTOM — listener whose only "emitter" is a manifest declaration.
    drift: list[dict] = []
    phantom: list[dict] = []
    for name, sites in sorted(listens.items()):
        if any(fnmatch.fnmatch(name, pat) for pat in ALLOW_ORPHAN_LISTENERS):
            continue
        if "*" in name:
            if any(fnmatch.fnmatch(e, name) for e in emits):
                continue
            if any(fnmatch.fnmatch(e, name) for e in all_declared):
                phantom.append({"event": name, "listeners": sites})
                continue
        elif name in emits:
            continue
        elif name in all_declared:
            phantom.append({"event": name, "listeners": sites})
            continue
        drift.append({"event": name, "listeners": sites})

    # B) UNDECLARED — code emits missing from the emitting app's manifest.
    undeclared_by_app: dict[str, list[str]] = defaultdict(list)
    for name, sites in emits.items():
        for site in sites:
            if site.endswith("(dynamic)"):
                continue  # heuristic literals aren't proven emit sites
            app_id = app_id_for(site.rsplit(":", 1)[0], dir_to_id)
            if app_id and name not in declared.get(app_id, set()):
                if name not in undeclared_by_app[app_id]:
                    undeclared_by_app[app_id].append(name)

    # C) DEAD — emitted, no Python listener (incl. wildcards), no frontend hit.
    frontend = scan_frontend_text()
    wildcard_listens = [n for n in listens if "*" in n]
    dead: list[str] = []
    for name in sorted(emits):
        if name in listens:
            continue
        if any(fnmatch.fnmatch(name, w) for w in wildcard_listens):
            continue
        if name in frontend:
            continue
        dead.append(name)

    n_undeclared = sum(len(v) for v in undeclared_by_app.values())
    n_broken = len(drift) + len(phantom)
    ok = n_broken == 0
    if args.json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "code": "ok" if ok else "drift",
                    "message": (
                        f"{len(drift)} drift + {len(phantom)} phantom, "
                        f"{n_undeclared} undeclared emits across "
                        f"{len(undeclared_by_app)} apps, {len(dead)} dead events"
                    ),
                    "data": {
                        "drift": drift,
                        "phantom": phantom,
                        "undeclared_by_app": {
                            k: sorted(v) for k, v in sorted(undeclared_by_app.items())
                        },
                        "dead": dead,
                    },
                }
            )
        )
        return n_broken

    print(f"Event wiring: {len(emits)} emitted names, {len(listens)} listened names")
    print()
    if drift:
        print(f"DRIFT — {len(drift)} listener(s) for events nobody emits (silent no-ops):")
        for d in drift:
            print(f"  {d['event']}")
            for s in d["listeners"]:
                print(f"      {s}")
    else:
        print("DRIFT: none — every listener has a matching code emitter.")
    print()
    if phantom:
        print(
            f"PHANTOM — {len(phantom)} listener(s) whose event is manifest-declared "
            "but never emitted in code (wire the emit or delete both sides):"
        )
        for d in phantom:
            print(f"  {d['event']}")
            for s in d["listeners"]:
                print(f"      {s}")
    else:
        print("PHANTOM: none — every declared emit a listener relies on is implemented.")
    print()
    print(
        f"UNDECLARED (advisory): {n_undeclared} code emits across "
        f"{len(undeclared_by_app)} apps missing from manifest [provides.events] emits"
    )
    worst = sorted(undeclared_by_app.items(), key=lambda kv: -len(kv[1]))[:8]
    for app_id, names in worst:
        print(f"  {app_id}: {len(names)} ({', '.join(sorted(names)[:4])}{'…' if len(names) > 4 else ''})")
    print()
    print(
        f"DEAD (informational): {len(dead)} emitted event names with no Python or "
        "frontend consumer (kept by design — audit stream)"
    )
    if args.dead:
        for name in dead:
            print(f"  {name}")
    return n_broken


if __name__ == "__main__":
    sys.exit(main())
