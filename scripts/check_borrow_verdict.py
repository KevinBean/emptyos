#!/usr/bin/env python3
"""check_borrow_verdict — has EmptyOS already ruled on borrowing from <repo>?

On-demand pre-check for any agent (Claude, Codex, Antigravity, human) about to
propose borrowing from an external repo/library. Greps the two durable verdict
docs and reports whether a closed verdict already exists — so a borrow plan is
never re-derived against a question that's already answered.

    python scripts/check_borrow_verdict.py <repo-or-library-name> [--json]
    python scripts/check_borrow_verdict.py PrefectHQ/prefect
    python scripts/check_borrow_verdict.py https://github.com/PrefectHQ/prefect --json

Exit code IS the signal (agent-cli convention, `.claude/rules/agent-cli.md`):
    0  no prior verdict  → fresh evaluation is OK
    1  a verdict exists  → STOP, read it, don't re-derive
    2  bad usage / docs missing

Pure stdlib. No kernel import, no daemon, no network.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERDICT_DOCS = [
    REPO_ROOT / "docs" / "OPEN-SOURCE-BORROWING-PLAN.md",
    REPO_ROOT / "docs" / "DEFERRED-WORK.md",
]


def _candidate_terms(arg: str) -> list[str]:
    """Repo/library name → the lowercase substrings worth searching for.

    Accepts a bare name (`prefect`), an owner/repo (`PrefectHQ/prefect`), or a
    full URL (`https://github.com/PrefectHQ/prefect`). Yields the raw string,
    the owner/repo tail, and the repo name — every term >= 3 chars, deduped.
    """
    raw = arg.strip().rstrip("/")
    core = re.sub(r"^https?://", "", raw, flags=re.I)
    core = re.sub(r"^[^/]*(github|gitlab|bitbucket)\.[^/]+/", "", core, flags=re.I)
    segments = [s for s in core.split("/") if s]
    terms = {raw.lower(), core.lower()}
    if segments:
        terms.add(segments[-1].lower().removesuffix(".git"))
    return sorted({t for t in terms if len(t) >= 3})


def _scan(terms: list[str]) -> tuple[list[dict], list[str]]:
    """Return (matches, missing_docs). Each match: {file, line, text, term}."""
    matches: list[dict] = []
    missing: list[str] = []
    for doc in VERDICT_DOCS:
        rel = doc.relative_to(REPO_ROOT).as_posix()
        if not doc.exists():
            missing.append(rel)
            continue
        for n, line in enumerate(doc.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = line.lower()
            hit = next((t for t in terms if t in low), None)
            if hit:
                matches.append({"file": rel, "line": n, "text": line.strip(), "term": hit})
    return matches, missing


def _emit(ok: bool, code: str, message: str, data=None, *, as_json: bool, exit_code: int) -> None:
    if as_json:
        print(json.dumps({"ok": ok, "code": code, "message": message, "data": data}))
    else:
        print(message)
        for m in (data or {}).get("matches", []):
            snippet = m["text"] if len(m["text"]) <= 200 else m["text"][:197] + "…"
            print(f"  {m['file']}:{m['line']}  {snippet}")
    sys.exit(exit_code)


def main(argv: list[str]) -> None:
    args = [a for a in argv if a != "--json"]
    as_json = "--json" in argv
    if len(args) != 1 or args[0].startswith("-"):
        _emit(False, "invalid_args",
              "usage: check_borrow_verdict.py <repo-or-library-name> [--json]",
              as_json=as_json, exit_code=2)

    terms = _candidate_terms(args[0])
    if not terms:
        _emit(False, "invalid_args", f"no searchable term in {args[0]!r}",
              as_json=as_json, exit_code=2)

    matches, missing = _scan(terms)
    if missing:
        _emit(False, "docs_missing", f"verdict docs not found: {', '.join(missing)}",
              {"missing": missing}, as_json=as_json, exit_code=2)

    if matches:
        _emit(False, "verdict_exists",
              f"CLOSED VERDICT EXISTS for {args[0]!r} ({len(matches)} line(s)) — read it, don't re-derive",
              {"matches": matches, "terms": terms}, as_json=as_json, exit_code=1)
    else:
        _emit(True, "clear",
              f"no prior verdict for {args[0]!r} (searched {', '.join(terms)}) — fresh evaluation OK",
              {"matches": [], "terms": terms}, as_json=as_json, exit_code=0)


if __name__ == "__main__":
    main(sys.argv[1:])
