"""check_prose_tone.py — lint published prose for AI-tells + banned voice patterns.

The mechanical half of the vault voice guide (30_Resources/Published/_voice.md):
runs emptyos.sdk.prose_lint over post markdown and reports banned vocabulary
(high) and structural AI-tells (medium/low). Advisory — it never edits; the
human reconciles (audits.md posture).

Usage:
    python scripts/check_prose_tone.py <file.md | dir> [more paths...] [--json] [--strict] [--spoken]

--spoken adds the spoken-register family for anything that will be SAID rather
than read (interview answers, phone-screen scripts, talking points). The default
families are blind to register: a contraction-free recruiter email lints clean
here while reading unmistakably machine-written.

A file may opt itself in instead, with ``spoken: true`` in its frontmatter. That
marker is what makes an unattended run possible: without it there is no way to
tell an interview answer from a CV, and a blanket scan of a career folder would
fire register rules on 42 trackers and strategy docs (audits.md — an ambiguous
signal must never gate).

--vault runs the two corpora that have a standing home, resolving the vault from
emptyos.toml: every published post (default families) and every ``spoken: true``
note anywhere in the vault (default + spoken families). No vault configured ⇒
skipped with exit 0, so a public clone never fails on it. This is the mode
preflight registers; before it existed the linter had no runner at all and was
run by hand roughly once.

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
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from emptyos.frontmatter import parse_frontmatter  # noqa: E402
from emptyos.sdk.prose_lint import lint_prose  # noqa: E402

# Where the two standing corpora live, relative to the vault root.
PUBLISHED_POSTS = "30_Resources/Published/posts"


def vault_root() -> Path | None:
    """Vault path from emptyos.toml, or None when unconfigured/absent."""
    cfg = REPO_ROOT / "emptyos.toml"
    if not cfg.exists():
        return None
    try:
        raw = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    p = (raw.get("notes") or {}).get("path") or ""
    root = Path(p) if p else None
    return root if root and root.is_dir() else None


def is_spoken(text: str) -> bool:
    """True when the note opts into spoken-register rules via frontmatter.

    ``spoken: true``. The parser hands back either a bool or the bare string
    depending on how the value was written, so accept both rather than trusting
    one shape (emptyos/frontmatter.py — one syntax, one type, but ``true``
    unquoted and ``"true"`` quoted are two syntaxes).
    """
    try:
        v = parse_frontmatter(text).get("spoken")
    except Exception:  # noqa: BLE001 — a malformed note is simply not spoken
        return False
    return v is True or str(v).strip().lower() == "true"


def vault_corpus(root: Path) -> tuple[list[Path], list[Path]]:
    """(published posts, spoken-marked notes) — the two files that have a home.

    Kept narrow on purpose. Everything else in the vault is unmarked prose the
    linter has no business judging unattended.
    """
    posts = sorted(f for f in (root / PUBLISHED_POSTS).rglob("*.md")
                   if not f.name.startswith("_")) if (root / PUBLISHED_POSTS).is_dir() else []
    spoken: list[Path] = []
    for f in root.rglob("*.md"):
        if f.name.startswith("_") or ".trash" in f.parts:
            continue
        try:
            if is_spoken(f.read_text(encoding="utf-8")):
                spoken.append(f)
        except (OSError, UnicodeDecodeError):
            # A vault holds attachments and hand-edited notes in other encodings;
            # an unreadable file is not spoken material, it is just not our file.
            continue
    return posts, sorted(spoken)


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
    ap.add_argument("paths", nargs="*", help="markdown files or directories")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--strict", action="store_true",
                    help="medium-severity findings also gate the exit code")
    ap.add_argument("--spoken", action="store_true",
                    help="force spoken-register rules (contractions, semicolons, "
                         "breath-length) on every file given — for anything that "
                         "will be said aloud: interview answers, phone-screen "
                         "scripts, talking points. A note can opt itself in "
                         "instead with `spoken: true` in its frontmatter.")
    ap.add_argument("--vault", action="store_true",
                    help="scan the standing corpora from emptyos.toml: published "
                         "posts, plus every `spoken: true` note. No vault ⇒ skip.")
    args = ap.parse_args()

    if args.vault:
        root = vault_root()
        if root is None:
            msg = "skipped — no vault configured (a public clone has no vault; not a failure)"
            if args.as_json:
                print(json.dumps({"ok": True, "code": "skipped", "message": msg, "data": None}))
            else:
                print(f"skip  {msg}")
            return 0
        posts, spoken_files = vault_corpus(root)
        files = posts + spoken_files
        forced_spoken = set(spoken_files)
    elif args.paths:
        files = _collect(args.paths)
        forced_spoken = set()
    else:
        ap.error("give at least one path, or --vault")

    reports = []
    failing = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"WARN: cannot read {f}: {e}", file=sys.stderr)
            continue
        # Three ways in: the global flag, the vault corpus, or the note itself.
        spoken = args.spoken or f in forced_spoken or is_spoken(text)
        rep = lint_prose(text, source=str(f), spoken=spoken)
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
        if "contraction_ratio" in m:
            print(f"   spoken: {m['contractions_used']}/{m['contraction_opportunities']} "
                  f"contractions ({m['contraction_ratio']:.0%}) · "
                  f"{m['semicolons']} semicolon(s) · "
                  f"{m['long_sentences']} sentence(s) over 25 words")
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
