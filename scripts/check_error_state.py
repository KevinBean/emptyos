#!/usr/bin/env python3
"""Find app pages that render a FAILURE using empty-state vocabulary.

    python scripts/check_error_state.py [--json] [--verbose]

A dropped request, an unparseable response and a genuinely empty result are
three different things the user needs to tell apart. `EOS_UI.errorState`
(`emptyos/web/static/eos-components.js`) already renders the middle one
distinctly — ⚠ icon, its own surface, optional Retry. This scanner finds the
pages that route around it and paint a failure in the muted vocabulary reserved
for "there is nothing here".

Measured 2026-08-13 across 538 live page files: 24 use `EOS_UI.errorState`, 63
render a failure as an empty state. Re-measured 2026-09-03 across 567: 59 call
the helper, 98 blocks still paint a failure grey — the count *rose* that day
because the scan stopped exempting whole files (see `scan_text`). The worst is
`apps/personal/ai-queue/pages/index.html`, whose `catch` renders **"No tasks
found"** — not merely unstyled, but false: a failed request reported to the
user as an accurate statement about their data.

## Where this came from

The `prelim-sizing` calculator hit the same defect in one app, found not by a
test but by writing down what its interface owed the user
(`INTENT.md § Refusal and error experience`). It is a general class, so it
graduates here per `.claude/rules/audits.md` — a static scan needing no daemon
belongs in `scripts/check-*.py` + preflight.

## Advisory, and why it must stay advisory

"Was this degradation deliberate?" is a judgment the scanner cannot make. A
recent-items widget that silently shows an empty state when its fetch fails may
be exactly right. So this never gates; it reports, and a page that has made the
call says so at the call site.

## The two-part signal, and what it deliberately does not read

A block is flagged when it renders **empty-state vocabulary** and applies **no
error styling**.

Both tests read only class, style and CSS-variable values — never the prose.
That is the load-bearing choice. `catch (err)` and `console.error(e)` appear in
nearly every block, so a signal test over the raw block would suppress every
finding; and a signal test over the emitted *message* would excuse "Could not
load sessions." rendered in `.muted`, which is exactly the defect — the user is
told it failed, in the typography reserved for "there is nothing here".

The consequence worth knowing: this scanner reports a **styling** claim. It
cannot tell the difference between muted-but-honest ("Could not load sessions")
and actively false ("No tasks found" from a `catch`). The second is the one
worth fixing first, and finding it needs a human reading the `--verbose` column.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json, page_files  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Start of a catch block. The body is taken by `_block_at`, not by this.
CATCH_RE = re.compile(r"catch\s*\([^)]*\)\s*\{")


def _block_at(text: str, open_brace: int) -> str:
    """The body of the block whose `{` is at `open_brace`, brace-balanced.

    A bounded character window was tried first and produced a whole class of
    false positive: `catch(e){ return; }` closes on its own line, so a window
    looking for a newline-then-`}` ran straight past it and swallowed the
    *success* path that followed — which is how `devices/pages/index.html` was
    reported for a "No simulators registered." string in a ternary the catch
    never reaches.

    Quotes are tracked so a `}` inside a string does not close the block.
    Template literals are treated as code for brace-counting, which is correct
    for `${...}` (its braces balance) and wrong only for a literal brace in
    template text — rare, and it fails toward closing early, i.e. reporting
    less.
    """
    depth = 0
    quote = ""
    i = open_brace
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
            elif quote == "`" and ch in "{}":
                depth += 1 if ch == "{" else -1
        elif ch in "'\"`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1:i]
        i += 1
    return text[open_brace + 1:]

#: Vocabulary reserved for "there is nothing here".
EMPTY_VOCAB_RE = re.compile(
    r"""class(?:Name)?\s*=?\s*['"][^'"]*\b(?:empty|muted|hint|subtle)\b"""
    r"""|var\(--text-muted\)|var\(--muted\)"""
    r"""|EOS_UI\.emptyState\(""",
    re.I,
)

#: The helper call that exempts a catch block. A *call*, matched after comments
#: are stripped — `// TODO EOS_UI.errorState later` above a grey paint is the
#: marker-in-a-comment shape `.claude/rules/audits.md` § Failure mode 3 warns
#: about, and it exempted the block until 2026-09-03.
ERROR_HELPER_RE = re.compile(r"EOS_UI\.errorState\s*\(")
COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)

#: Class, style and CSS-variable values — the only text either test reads.
#: Run against the raw block, where `class="db-empty db-warn"` is contiguous
#: even though the surrounding JS string literal is split by those quotes.
STYLING_RE = re.compile(
    r"""class(?:Name)?\s*=?\s*['"]([^'"]*)['"]"""
    r"""|style\s*=?\s*['"]([^'"]*)['"]"""
    r"""|var\(--([a-z-]+)\)""",
    re.I,
)

#: A class or colour role that says "this is a failure". Prose is not consulted
#: — see the module docstring.
SIGNAL_RE = re.compile(r"\b(?:err|error|warn|warning|danger|bad|alert|fail)\w*\b", re.I)

#: String literals in the block — reported for triage, never used to decide.
STRING_RE = re.compile(r"""['"]([^'"\n]{4,}?)['"]""")


def styling_tokens(block: str) -> str:
    return " ".join(g for m in STYLING_RE.finditer(block) for g in m.groups() if g)

#: Inline opt-out at the call site, never a central allowlist: an allowlist
#: turns every new legitimate case into a build break, and separates the
#: decision from the code it is about (`.claude/rules/audits.md`).
#:
#:     // error-state: intentional — a stale recent-list beats an error card
OPT_OUT_RE = re.compile(r"error-state:\s*intentional", re.I)

#: How far above the block an opt-out marker may sit and still apply.
OPT_OUT_LOOKBEHIND = 200

def scan_text(text: str) -> list[dict]:
    """Findings for one file's source. Pure — the unit under test.

    The helper exempts the *block* that calls it, never the file. The first
    version returned `[]` for any file containing `EOS_UI.errorState`, on the
    theory that a page which adopted the helper had made its calls
    deliberately — and that exemption hid 23 findings in 8 files (measured
    2026-09-03), because adoption is per catch block: a page converts three
    of four and the fourth stays grey, invisible to this scan for as long as
    the other three exist. A file-wide skip is the vacuous-pass shape
    `.claude/rules/audits.md` § Failure mode 3 names.
    """
    findings = []
    for match in CATCH_RE.finditer(text):
        block = _block_at(text, match.end() - 1)
        if ERROR_HELPER_RE.search(COMMENT_RE.sub("", block)):
            continue
        if "innerHTML" not in block and "textContent" not in block:
            continue
        if not EMPTY_VOCAB_RE.search(block):
            continue
        if SIGNAL_RE.search(styling_tokens(block)):
            continue
        emitted = " ".join(STRING_RE.findall(block))
        before = text[max(0, match.start() - OPT_OUT_LOOKBEHIND):match.start()]
        if OPT_OUT_RE.search(block) or OPT_OUT_RE.search(before):
            continue
        findings.append({
            "line": text.count("\n", 0, match.start()) + 1,
            "emitted": emitted.strip()[:120],
        })
    return findings


def scan(root: Path = REPO_ROOT) -> tuple[list[dict], int]:
    """Findings, and how many page files were read to get them.

    Both in one walk — the count is reported next to the finding total, and
    rglob over `apps/` is the slowest thing this script does.
    """
    out: list[dict] = []
    files = page_files(root)
    for path in files:
        for finding in scan_text(path.read_text(encoding="utf-8", errors="ignore")):
            out.append({"file": path.relative_to(root).as_posix(), **finding})
    return out, len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true",
                        help="print the emitted text for every finding")
    parser.add_argument("--root", default=str(REPO_ROOT))
    args = parser.parse_args()

    root = Path(args.root).resolve()
    findings, total = scan(root)
    message = (
        f"{len(findings)} failure(s) rendered as an empty state "
        f"across {total} page file(s)"
    )

    if args.json:
        return emit_json(not findings, "error_as_empty_state", message,
                         {"findings": findings})

    for finding in findings:
        print(f"  {finding['file']}:{finding['line']}")
        if args.verbose:
            print(f"      {finding['emitted']}")
    if findings:
        print("\n  Use EOS_UI.errorState({message, onRetry}) — it is already in "
              "eos-components.js.\n  If the empty state is deliberate, say so at "
              "the call site:  // error-state: intentional — <why>\n")
    # The summary goes LAST because `preflight.py::_run_one` reports
    # `stdout.splitlines()[-1]` as the row for this check. Printing it first
    # put the advice block in the registry view instead of the count.
    print(message)
    # Advisory: "was this deliberate?" is not a judgment a scanner can make.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
