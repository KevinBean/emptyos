"""check_prose_tone.py — lint published prose for AI-tells + banned voice patterns.

The mechanical half of the vault voice guide (30_Resources/Published/_voice.md):
runs emptyos.sdk.prose_lint over post markdown and reports banned vocabulary
(high) and structural AI-tells (medium/low). Advisory — it never edits; the
human reconciles (audits.md posture).

Usage:
    python scripts/check_prose_tone.py <file.md | dir> [more paths...] [--json] [--strict]

Paths may be files or directories (directories rglob *.md; filenames starting
with "_" are skipped — style guides lint their own examples hot).

Exit code: number of files with high-severity findings (0 = clean); with
--strict, medium counts too. --json emits the agent-cli envelope
(.claude/rules/agent-cli.md) on stdout and nothing else.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emptyos.sdk.prose_lint import lint_prose  # noqa: E402


def _collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(f for f in path.rglob("*.md")
                                if not f.name.startswith("_")))
        elif path.is_file():
            files.append(path)
        else:
            print(f"WARN: no such path: {p}", file=sys.stderr)
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="markdown files or directories")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--strict", action="store_true",
                    help="medium-severity findings also gate the exit code")
    args = ap.parse_args()

    files = _collect(args.paths)
    reports = []
    failing = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as e:
            print(f"WARN: cannot read {f}: {e}", file=sys.stderr)
            continue
        rep = lint_prose(text, source=str(f))
        reports.append(rep)
        gate = {"high"} | ({"medium"} if args.strict else set())
        if any(x["severity"] in gate for x in rep["findings"]):
            failing += 1

    total_findings = sum(len(r["findings"]) for r in reports)
    if args.as_json:
        ok = failing == 0
        print(json.dumps({
            "ok": ok,
            "code": "ok" if ok else "tone_findings",
            "message": f"{len(reports)} file(s), {total_findings} finding(s), {failing} failing",
            "data": {"reports": reports},
        }))
        return failing

    for rep in reports:
        m = rep["metrics"]
        print(f"\n== {rep['source']}")
        print(f"   {m['words']} words · {m['em_dash_per_100']} em-dash/100w · "
              f"{m['not_x_its_y']} 'isn't X. It's Y.' pivots · "
              f"{m['thats_the_openers']} \"That's the\" openers")
        if not rep["findings"]:
            print("   clean")
            continue
        for x in rep["findings"]:
            print(f"   [{x['severity']:6}] L{x['line']:<4} {x['rule']:18} "
                  f"{x['excerpt']!r} — {x['message']}")
    print(f"\n{len(reports)} file(s), {total_findings} finding(s), {failing} failing")
    return failing


if __name__ == "__main__":
    sys.exit(main())
