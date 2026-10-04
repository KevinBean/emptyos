#!/usr/bin/env python3
"""Render worklog's EA competency table from the vault's evidence inventory.

    python scripts/gen_competency_focus.py [--check] [--json]

`--check` re-renders and compares without writing, exiting 1 on drift, so
`/preflight` can treat a stale competency table as a failure. That wiring is the
point: a `--check` nothing calls is prose with extra steps.

## Why this exists

`apps/public/standard/worklog/shared.py` holds a copy of the sixteen Engineers
Australia Stage 2 elements so the CPEng roll-up can name them — and so the
**standalone offline bundle** can, since an exported HTML file has no server and
no vault to ask. That copy has an upstream: the vault note
`10_Projects/cpeng/docs/competency-evidence-inventory.md`, which transcribes the
approved 2012 standard verbatim and carries a per-element **Strength** column.

Names are stable. **Strength is not** — it is Kevin's live assessment, and it
moves the moment an element gets evidenced. So the copy drifts in the direction
that matters most: a `gap` the code still advertises after the gap closed, or an
`Adequate` the code never learned about. That is not hypothetical. The first run
of this script found `COMPETENCY_FOCUS` listing element 1 as thin while the note
had marked **5 and 7** Adequate too — two elements the hub panel and the CPEng
tab were silently treating as Strong.

This is the shape `.claude/rules/self-audit-loops.md` catalogues as
"published-artifact data": an artifact that cannot fetch its data inlines a copy,
and the copy goes quietly wrong. The answer there is a generator with a
`--check`, not a reminder.

## What is generated, and what deliberately is not

Generated from the note: the element **numbers**, **names**, and the
**focus map** (Strength → `gap` / `thin`; `Strong` is simply absent).

**`COMPETENCY_AREAS` is NOT generated.** The note tables elements 8-16 together
for readability and states the real split in prose under its `Unit 4` heading —
*"elements 12-16 above sit under Technical Proficiency; elements 8-11 sit under
Value in the Workplace… split at the 11/12 boundary"*. Parsing unit membership
from the table headings would yield 3/4/9/0 and silently contradict both the
standard and the note's own instruction. A generator that guesses at prose is
worse than a hand-maintained constant, so the areas stay authored in `shared.py`
and the partition is pinned by `tests/test_unit_worklog_competency.py`.

## No vault, no failure

The vault is external and swappable (CLAUDE.md rule 8) and a public clone has
none. A missing vault or note is reported as **skipped with exit 0** — a check
that breaks every fresh clone is a check somebody disables.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scanner_lib import emit_json  # noqa: E402

TARGET = REPO_ROOT / "apps/public/standard/worklog/shared.py"
NOTE_REL = "10_Projects/cpeng/docs/competency-evidence-inventory.md"

BEGIN = "# --- generated: EA competency table (scripts/gen_competency_focus.py) ---"
END = "# --- /generated: EA competency table ---"

# `| 11 | Judgement | <evidence> | **Gap** |` — the evidence column is prose and
# may itself contain pipes inside code spans, so anchor on the leading number and
# take the Strength keyword from the LAST bolded run on the line.
ROW_RE = re.compile(r"^\|\s*(\d{1,2})\s*\|\s*([^|]+?)\s*\|.*\|\s*\*\*([A-Za-z]+)")

# Strength key, verbatim from the note's own "## Strength key" section.
#   Strong   — a dateable verifiable instance exists        -> no focus entry
#   Adequate — real but generic/unquantified                -> "thin"
#   Gap      — no instance identified yet                   -> "gap"
STRENGTH_TO_FOCUS = {"Strong": None, "Adequate": "thin", "Gap": "gap"}


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
    if not p:
        return None
    root = Path(p)
    return root if root.is_dir() else None


def parse_note(text: str) -> tuple[dict[int, str], dict[int, str], list[str]]:
    """(names, focus, problems) parsed from the inventory note's element tables."""
    names: dict[int, str] = {}
    focus: dict[int, str] = {}
    problems: list[str] = []
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        n, name, strength = int(m.group(1)), m.group(2).strip(), m.group(3)
        if not 1 <= n <= 16:
            continue
        if n in names:
            problems.append(f"element {n} appears twice in the note")
            continue
        if strength not in STRENGTH_TO_FOCUS:
            problems.append(
                f"element {n}: strength '{strength}' is not in the note's own "
                f"Strength key ({'/'.join(STRENGTH_TO_FOCUS)})"
            )
            continue
        names[n] = name
        if (f := STRENGTH_TO_FOCUS[strength]) is not None:
            focus[n] = f
    missing = [n for n in range(1, 17) if n not in names]
    if missing:
        problems.append(f"note does not table element(s): {missing}")
    return names, focus, problems


def render_block(names: dict[int, str], focus: dict[int, str]) -> str:
    """The generated region's body — deterministic, ascending, no trailing space."""
    out = [
        BEGIN,
        "# Generated from the vault note; edit there, then re-run the script.",
        "#   10_Projects/cpeng/docs/competency-evidence-inventory.md",
        "COMPETENCIES: dict[int, str] = {",
    ]
    out += [f'    {n}: "{names[n]}",' for n in sorted(names)]
    out += [
        "}",
        "",
        "# Strength -> focus. \"Strong\" is absent by design: only elements that still",
        "# need hunting appear here, so an empty map means nothing is outstanding.",
        "COMPETENCY_FOCUS: dict[int, str] = {",
    ]
    out += [f'    {n}: "{focus[n]}",' for n in sorted(focus)]
    out += ["}", END]
    return "\n".join(out)


def splice(current: str, block: str) -> str:
    """Replace the marked region, preserving everything around it."""
    i, j = current.find(BEGIN), current.find(END)
    if i == -1 or j == -1:
        raise SystemExit(
            f"markers not found in {TARGET.relative_to(REPO_ROOT)} — expected\n"
            f"  {BEGIN}\n  ...\n  {END}"
        )
    return current[:i] + block + current[j + len(END):]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="compare without writing; exit 1 on drift")
    ap.add_argument("--json", action="store_true", help="machine-readable envelope")
    args = ap.parse_args()

    root = vault_root()
    note = (root / NOTE_REL) if root else None
    if note is None or not note.is_file():
        why = "vault not configured" if root is None else f"note absent: {NOTE_REL}"
        msg = f"skipped — {why} (a public clone has no vault; not a failure)"
        if args.json:
            return emit_json(True, "skipped", msg)
        print(f"skip  {msg}")
        return 0

    names, focus, problems = parse_note(note.read_text(encoding="utf-8"))
    if problems:
        msg = "; ".join(problems)
        if args.json:
            return emit_json(False, "unparsable", msg, {"note": NOTE_REL})
        print("FAIL  the inventory note did not parse cleanly:")
        for p in problems:
            print(f"      - {p}")
        return 1

    current = TARGET.read_text(encoding="utf-8")
    updated = splice(current, render_block(names, focus))
    rel = str(TARGET.relative_to(REPO_ROOT)).replace("\\", "/")

    if args.check:
        drifted = updated != current
        msg = (
            f"{rel} no longer matches the inventory note — run "
            "scripts/gen_competency_focus.py"
            if drifted else
            f"{rel} matches the inventory note ({len(names)} elements, "
            f"{len(focus)} needing work)"
        )
        if args.json:
            return emit_json(not drifted, "drift" if drifted else "ok", msg,
                             {"target": rel, "note": NOTE_REL,
                              "focus": {str(k): v for k, v in sorted(focus.items())}})
        if drifted:
            print(f"DRIFT  {rel} no longer matches the EA competency inventory.")
            print("       Run: python scripts/gen_competency_focus.py")
        else:
            print(f"ok  {rel} matches the note "
                  f"({len(names)} elements, {len(focus)} needing work)")
        return 1 if drifted else 0

    if updated != current:
        TARGET.write_text(updated, encoding="utf-8")
        print(f"wrote {rel}  ({len(names)} elements, "
              f"focus: {ings if (ings := ', '.join(f'{n}={focus[n]}' for n in sorted(focus))) else 'none'})")
    else:
        print("no change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
