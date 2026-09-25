#!/usr/bin/env python3
"""Render `ALGORITHM.md` sections 2 and 3 from the corona-gradient spec.

    python scripts/gen_corona_tables.py [--check] [--json]

`--check` re-renders and compares without writing, exiting 1 on drift, so
`/preflight` and a release chain can treat a stale data dictionary as a failure.
That wiring is the point rather than an afterthought: a `--check` mode nothing
calls is prose with extra steps.

## Why these two tables are generated and the prose is not

The input and output tables are the package's data dictionary. Generating them
makes `ALGORITHM.md` a consumer of `spec.py` rather than a second copy of it —
in particular the **field name an API caller actually sends**, which a symbol
table leaves undocumented, and the **inputs that reached each output**, without
which a requirement stated over the output set is stated over a set nobody
wrote down.

Only the material between the markers is a build artefact. The prose around
them is authored and is where the reasoning lives; a generator that owned the
prose would be a generator nobody could argue with.

## A third implementation, deliberately

`scripts/gen_trust_loop_tables.py` does this for `trust-loop` and
`D:/prelim-sizing` has its own. Per `docs/TRUST-LOOP.md` § Adoption rule the
shared extraction so far is the *behaviour* (`emptyos/fieldspec.py`), not the
record and not the renderer — the column sets genuinely differ, and this one
renders a `slot` where trust-loop renders a `group` and prelim-sizing renders
neither. Collapsing them before reading them would destroy the evidence for
whether a shared generator is worth extracting at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_common import load_by_path  # noqa: E402
from scanner_lib import envelope  # noqa: E402

APP_DIR = REPO_ROOT / "apps" / "extension" / "engineering" / "corona-gradient"
DOC = APP_DIR / "ALGORITHM.md"

BEGIN = "<!-- generated:{name} — from apps/extension/engineering/corona-gradient/spec.py -->"
END = "<!-- /generated:{name} -->"


def _load_spec():
    """Import `spec.py` by path.

    Not `from apps.extension... import spec`: `apps/` is not a package, its
    directories carry hyphens, and the app loader gives them a namespace that
    only exists inside a running daemon. This script must work without one.
    """
    return load_by_path(
        "corona_gradient_spec",
        "apps/extension/engineering/corona-gradient/spec.py",
    )


def render_inputs(spec) -> str:
    """Section 2 — the inputs, as a reader and an API caller both need them."""
    lines = [
        "| Symbol | Field | Meaning | Unit | Domain | Section |",
        "|---|---|---|---|---|---|",
    ]
    for f in spec.INPUTS:
        unit = f"`{f.unit}`" if f.unit else "—"
        req = " **(required)**" if f.required else ""
        lines.append(
            f"| `{f.symbol}` | `{f.name}` | {f.label}{req} — {f.description} "
            f"| {unit} | {f.domain} | {f.slot} |"
        )
    return "\n".join(lines)


def render_outputs(spec) -> str:
    """Section 3 — the outputs, each naming the inputs that reached it."""
    lines = [
        "| Field | Symbol | Unit | Meaning | Derived from |",
        "|---|---|---|---|---|",
    ]
    all_names = set(spec.field_names())
    for o in spec.OUTPUTS:
        unit = f"`{o.unit}`" if o.unit else "—"
        # "every input" beats ten backticked names in a table cell, and it stays
        # true when an input is added — which a spelled-out list would not,
        # silently.
        derived = (
            "every input"
            if set(o.derived_from) == all_names
            else ", ".join(f"`{n}`" for n in o.derived_from)
        )
        mark = " ·" if o.headline else ""
        lines.append(
            f"| `{o.name}`{mark} | `{o.symbol}` | {unit} | {o.label} — "
            f"{o.description} | {derived} |"
        )
    lines.append("")
    lines.append("`·` marks the headline result the interface leads with.")
    return "\n".join(lines)


BLOCKS = {"inputs": render_inputs, "outputs": render_outputs}


def splice(text: str, name: str, body: str) -> str:
    """Replace the marked block, refusing rather than guessing where it goes."""
    begin, end = BEGIN.format(name=name), END.format(name=name)
    start, stop = text.find(begin), text.find(end)
    if start < 0 or stop < 0 or stop < start:
        raise SystemExit(
            f"{DOC.name}: missing marker for {name!r}. Expected:\n  {begin}\n  {end}"
        )
    return text[: start + len(begin)] + "\n\n" + body + "\n\n" + text[stop:]


def render_document(spec, text: str) -> str:
    for name, renderer in BLOCKS.items():
        text = splice(text, name, renderer(spec))
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="compare without writing; exit 1 on drift")
    ap.add_argument("--json", action="store_true", help="machine-readable envelope")
    args = ap.parse_args()

    spec = _load_spec()
    current = DOC.read_text(encoding="utf-8")
    updated = render_document(spec, current)

    if args.check:
        drifted = updated != current
        message = (
            "ALGORITHM.md sections 2 and 3 are stale — run "
            "scripts/gen_corona_tables.py"
            if drifted else
            "ALGORITHM.md sections 2 and 3 match spec.py"
        )
        if args.json:
            print(json.dumps(envelope(
                not drifted, "drift", message,
                {"doc": str(DOC.relative_to(REPO_ROOT)).replace("\\", "/")},
            )))
        elif drifted:
            print("DRIFT  ALGORITHM.md § 2 / § 3 no longer match spec.py.")
            print("       Run: python scripts/gen_corona_tables.py")
        else:
            print("ok  ALGORITHM.md § 2 / § 3 match corona-gradient/spec.py")
        return 1 if drifted else 0

    if updated != current:
        DOC.write_text(updated, encoding="utf-8")
        print(f"wrote {DOC.relative_to(REPO_ROOT)}")
    else:
        print("no change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
