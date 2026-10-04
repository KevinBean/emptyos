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
import re
from pathlib import Path
from typing import Any, Iterable

# Reserved by the envelope. A scanner returning one of these as a payload field
# would silently shadow it, which is how the drift started.
ENVELOPE_KEYS = frozenset({"ok", "code", "message", "data"})


def envelope(ok: bool, code: str, message: str, data: Any = None) -> dict:
    """Build the agent-cli envelope. `code` is 'ok' whenever `ok` is True."""
    return {"ok": bool(ok), "code": "ok" if ok else code, "message": message, "data": data}


_DECLARED_TOKEN = re.compile(r"--([\w-]+)\s*:")


def is_brand_island(
    css: str,
    *,
    global_prefixes: Iterable[str] = (),
    min_tokens: int = 3,
) -> bool:
    """True when a file declares its own `--xx-*` namespace.

    A brand island is a deliberate visual island — its palette is the design,
    not drift — so colour/structure scanners exempt it wholesale. The island
    table in `.claude/skills/eos-design-system-audit/SKILL.md` is the catalogue;
    this is the mechanical rule behind it.

    Two scanners grew their own copy and both were wrong, in opposite
    directions, because both bounded the *prefix length* instead of asking what
    the prefix means:

      * `check_ui_structure` looked only inside `:root {}` at `[a-z]{1,4}`, so
        it missed a namespace declared under a scoped selector (how
        cymcap-modifier's 12-token `--cm-*` palette went undetected) and any
        longer prefix (`--boards-*`, `--series-*`).
      * `check-text-tokens` scanned the whole file at `[a-z]{1,6}`, which reads
        theme.css's own `--accent-ink/-dim/-bg` as a private `accent-*`
        namespace.

    Pass `global_prefixes` (from `emptyos.sdk.theme_tokens.global_token_prefixes()`,
    or the `scripts/theme_css.py` shim that re-exports it) so the
    families theme.css owns are never counted as private. Length is then
    irrelevant and nothing needs an arbitrary bound.
    """
    globals_ = {p.lower() for p in global_prefixes}
    counts: dict[str, int] = {}
    for name in _DECLARED_TOKEN.findall(css):
        prefix, _, rest = name.partition("-")
        if not rest:
            continue                      # `--accent`, not a namespace
        prefix = prefix.lower()
        if prefix in globals_:
            continue
        counts[prefix] = counts.get(prefix, 0) + 1
    return any(n >= min_tokens for n in counts.values())


#: Directories no page scan should read. `_retired` is not served at all;
#: `_example` and `_catalog` are scaffolds, so a finding there describes a
#: template rather than an app; `vendor` / `node_modules` are third-party.
SKIP_PAGE_DIRS = ("/_retired/", "/_example/", "/_catalog/", "/vendor/", "/node_modules/")


def page_files(
    root: Path,
    *,
    suffixes: tuple[str, ...] = (".html", ".js"),
    skip: tuple[str, ...] = SKIP_PAGE_DIRS,
    skip_minified: bool = True,
) -> list[Path]:
    """Every app page file under `root/apps/**/pages/`, sorted.

    Twenty-three scanners walk this same tree, and they had twenty-three
    slightly different exclusion sets — which is not a style question. A
    scanner that forgets `_example` reports on a scaffold; one that forgets
    `.min.js` reports inside a third-party bundle, where the finding is
    unactionable and the fix impossible. `check_error_state.py` shipped
    missing both, silently, because it was written from the shape of its
    neighbours rather than from a shared definition.

    Note `.min.js` files are NOT all under `vendor/` — three sit directly in
    a `pages/` directory, so the suffix test is doing real work rather than
    duplicating the directory test.

    This module is deliberately tiny and holds plumbing, never heuristics; an
    exclusion set is closer to correctness than to style, which is what earns
    it a place here. Adopt on touch — do not migrate the other scanners
    wholesale, because several are gates whose reported denominators would
    shift.
    """
    out: list[Path] = []
    for path in (root / "apps").rglob("*"):
        if path.suffix not in suffixes or not path.is_file():
            continue
        posix = path.as_posix()
        if "/pages/" not in posix or any(s in posix for s in skip):
            continue
        if skip_minified and path.name.endswith(".min.js"):
            continue
        out.append(path)
    return sorted(out)


def emit_json(ok: bool, code: str, message: str, data: Any = None) -> int:
    """Print the envelope on stdout and return the process exit code.

    Exit code mirrors `ok` (0 / 1), per the rule's "exit code is the primary
    signal". Nothing else may go to stdout in `--json` mode — a parser does
    `json.loads(stdout)`, so diagnostics belong on stderr.
    """
    print(json.dumps(envelope(ok, code, message, data)))
    return 0 if ok else 1
