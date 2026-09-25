#!/usr/bin/env python3
"""Integrity check for the MV prompt + asset library (advisory, preflight `vault`).

Reads ``attempts.jsonl``, ``assets.jsonl`` and ``patterns/*.md`` under
``{vault}/10_Projects/YouTube-Music-Channel/library/`` and reports:

* unparseable lines, non-object rows, wrongly typed list fields, duplicate ids
* required fields, and vocabulary drawn from the library's own ``SCHEMA.md`` /
  ``failure-codes.md`` (verdict, stage, platform→model, mode, codes, asset
  kind/status/reusable)
* sha256 format, vault-relative paths (no ``..``) that resolve
* prompt non-empty, or null carrying ``prompt_not_persisted``
* fail/partial carrying codes; fail/partial/rejected-human carrying a reason;
  reviewed verdicts carrying an output (path or sha256) and evidence
* ``lyrics`` fields (the library never stores lyrics)
* patterns: unreadable notes, status vocabulary, ``proven`` without pass
  evidence from two projects, and evidence counts ``refresh-patterns`` would move

All validation lives in ``mv_library.py`` so the writer and this checker cannot
disagree. A malformed row or unreadable file is a FINDING. An unexpected
exception is reported as a ``crash`` envelope carrying the exception — loud,
never swallowed into a clean result. No vault or no library → exit 0 (a fresh
clone is healthy). Exit 1 when there are findings.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mv_library as lib  # noqa: E402
from scanner_lib import emit_json  # noqa: E402


def _finding(file, row, id_, code, message) -> dict:
    return {"file": file, "row": row, "id": id_, "code": code, "message": message}


def _row_id(r, key):
    v = r.get(key) if isinstance(r, dict) else None
    return v if isinstance(v, str) else None


def _dupes(rows: list, key: str, fname: str, out: list[dict]) -> None:
    seen: set = set()
    for n, r in enumerate(rows, 1):
        k = _row_id(r, key)
        if k is None:
            continue
        if k in seen:
            out.append(_finding(fname, n, k, "duplicate_id", f"{key} {k!r} appears more than once"))
        seen.add(k)


def scan(library: Path, vault: Path | None) -> dict:
    findings: list[dict] = []
    vocab = lib.load_vocab(library)            # VocabError propagates: loud, not empty
    loaded: dict[str, list] = {}
    for fname in (lib.ATTEMPTS, lib.ASSETS):
        try:
            loaded[fname] = lib.read_jsonl(library / fname)
        except (lib.RowError, OSError) as e:
            findings.append(_finding(fname, None, None, "bad_json", str(e)))
            loaded[fname] = []
    attempts, assets = loaded[lib.ATTEMPTS], loaded[lib.ASSETS]
    patterns = lib.pattern_ids(library)
    attempt_ids = {_row_id(r, "attempt_id") for r in attempts} - {None}
    asset_ids = {_row_id(r, "asset_id") for r in assets} - {None}

    _dupes(attempts, "attempt_id", lib.ATTEMPTS, findings)
    _dupes(assets, "asset_id", lib.ASSETS, findings)
    for n, r in enumerate(attempts, 1):
        for code, msg in lib.validate_attempt(r, vocab, vault, patterns=patterns):
            findings.append(_finding(lib.ATTEMPTS, n, _row_id(r, "attempt_id"), code, msg))
    for n, r in enumerate(assets, 1):
        for code, msg in lib.validate_asset(r, vocab, vault, attempt_ids=attempt_ids,
                                            asset_ids=asset_ids):
            findings.append(_finding(lib.ASSETS, n, _row_id(r, "asset_id"), code, msg))

    for p in sorted((library / "patterns").glob("*.md")):
        rel = f"patterns/{p.name}"
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            findings.append(_finding(rel, None, None, "bad_json", f"unreadable: {e}"))
            continue
        fm = lib._frontmatter(text)[0]
        pid = re.search(r"^pattern_id:\s*(\S+)", fm, re.M)
        status = re.search(r"^status:\s*([\w-]+)", fm, re.M)
        if not pid:
            findings.append(_finding(rel, None, None, "missing_field", "pattern note has no pattern_id"))
            continue
        if not status or status.group(1) not in vocab["pattern_status"]:
            findings.append(_finding(rel, None, pid.group(1), "bad_vocab",
                                     f"status must be one of {sorted(vocab['pattern_status'])}"))
        ev = lib.pattern_evidence(attempts, pid.group(1))
        if status and status.group(1) == "proven" and ev["pass_projects"] < 2:
            findings.append(_finding(rel, None, pid.group(1), "unproven",
                                     f"status proven but pass evidence from {ev['pass_projects']} project(s); needs 2"))
        if lib.refresh_pattern_text(text, attempts, "0000-00-00") != text:
            findings.append(_finding(rel, None, pid.group(1), "stale_evidence",
                                     "evidence counts out of date — run mv_library.py refresh-patterns"))
    return {"attempts": len(attempts), "assets": len(assets), "patterns": len(patterns),
            "findings": findings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check the MV prompt + asset library.")
    ap.add_argument("--vault", default="")
    ap.add_argument("--library", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=40, help="findings printed in human mode")
    args = ap.parse_args(argv)

    vault, library = lib.resolve(args)
    if library is None or not library.is_dir():
        msg = f"no library at {library} — skipped" if library else "no vault configured — skipped"
        if args.json:
            return emit_json(True, "ok", msg, {"findings": []})
        print(msg)
        return 0
    try:
        res = scan(library, vault)
    except lib.VocabError as e:
        if args.json:
            return emit_json(False, "vocab", str(e), None)
        print(f"error: {e}")
        return 1
    except Exception as e:  # noqa: BLE001 — reported loudly as a crash, never as a clean pass
        msg = f"checker crashed: {type(e).__name__}: {e}"
        if args.json:
            return emit_json(False, "crash", msg, None)
        print(msg)
        return 1

    f = res["findings"]
    msg = f"{res['attempts']} attempts · {res['assets']} assets · {res['patterns']} patterns · {len(f)} findings"
    if args.json:
        return emit_json(not f, "library_findings", msg, res)
    print(msg)
    for x in f[: args.limit]:
        where = f"{x['file']}:{x['row']}" if x["row"] else x["file"]
        print(f"  [{x['code']}] {where} {x['id'] or ''}: {x['message']}")
    if len(f) > args.limit:
        print(f"  … {len(f) - args.limit} more (--json for all)")
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())
