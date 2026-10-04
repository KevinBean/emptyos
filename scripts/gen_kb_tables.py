#!/usr/bin/env python3
"""Render the IEC 60949 Tables I–III blocks of the KB tables note from the one
transcription, `engines/cables/data/iec60949.json`.

    python scripts/gen_kb_tables.py            # write the blocks into the vault note
    python scripts/gen_kb_tables.py --check    # compare only; exit 1 on drift
    python scripts/gen_kb_tables.py --json

Only the material between the markers is a build artefact:

    <!-- generated:iec60949-table-i — from engines/cables/data/iec60949.json -->
    ...
    <!-- /generated:iec60949-table-i -->

The prose around them — footnotes, the corrections callouts, the A1:2008 rows,
"why it matters" — is authored and stays authored. Each block ends with a
caption naming the source record's tier, page and how it was checked
(`iec60949_tables.provenance_sentence`), so a reader cannot mistake the note
for the source: edit the JSON, not the block.

Why a fourth instance of the `gen_*.py --check` drift gate (after
gen_trust_loop_tables, gen_vowel_crosswalk_artifact, gen_competency_focus):
on 2026-09-30 this note's Table I had the Bronze and Aluminium sheath rows
swapped and Table II had wrong rows, while the engine and the app carried their
own copies of the same tables. Four copies, no check. Now the engine, the app's
defaults and anchors, and this note all read one file, and `--check` is a
preflight gate (`kb` + `apps` scopes).

**No vault ⇒ skipped with exit 0.** A public clone has no vault and must not
fail on it (`gen_competency_focus.py` precedent).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT))

from scanner_lib import emit_json  # noqa: E402

DATA_REL = "engines/cables/data/iec60949.json"
TARGET_REL = "30_Resources/EmptyOS/kb/sources/iec-60949-1988-tables.md"
BEGIN = "<!-- generated:{name} — from " + DATA_REL + " -->"
END = "<!-- /generated:{name} -->"
_META = frozenset({"columns"})


def vault_root() -> Path | None:
    """The vault, or None when unconfigured/absent — never raises."""
    try:
        from vault_paths import require_vault_root
        root = require_vault_root()
    except (SystemExit, Exception):
        return None
    return root if root and Path(root).is_dir() else None


def load_table(path: Path = REPO_ROOT / DATA_REL) -> dict:
    from engines.provenance import load_table_file
    return load_table_file(path)


def _rows(table: dict, group: str) -> dict[str, dict]:
    return {k: v for k, v in table[group].items() if k not in _META}


def _sci(value: float, exp: int) -> str:
    """3450000 → '3.45 × 10⁶'; 1.7241e-8 → '1.7241 × 10⁻⁸'."""
    sup = {"-": "⁻", "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
           "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}
    mant = value / (10 ** exp)
    return f"{mant:g} × 10{''.join(sup[c] for c in str(exp))}"


def _caption(table: dict, source_id: str) -> str:
    from engines.provenance import provenance_sentence_for, source_record
    sentence = provenance_sentence_for(source_record(table, source_id))
    return (f"*Rendered from `{DATA_REL}` — {sentence} — edit the JSON, not this block.*")


def render_table_i(table: dict) -> str:
    rows = _rows(table, "table_i")
    out = ["| Material | `K` (A·s^½/mm²) | `β` (K) | `σc` (J/K·m³) | `ρ20` (Ω·m) |",
           "|---|---|---|---|---|"]
    part_label = {"conductor": "**a) Conductors**", "sheath": "**b) Sheaths, screens and armour**"}
    seen = []
    for key, r in rows.items():
        if r["part"] not in seen:
            seen.append(r["part"])
            out.append(f"| {part_label.get(r['part'], r['part'])} | | | | |")
        out.append(f"| {r['label']} | {r['K']:g} | {r['beta']:g} | {_sci(r['sigma_c'], 6)} | {_sci(r['rho20'], -8)} |")
    out += ["", _caption(table, "iec60949-t1")]
    return "\n".join(out)


def render_table_ii(table: dict) -> str:
    rows = _rows(table, "table_ii")
    out = ["| Material | `ρ` (K·m/W) | `σ` (J/K·m³) |", "|---|---|---|"]
    group_label = {"insulating": "***Insulating materials***", "covering": "***Protective coverings***",
                   "other": "***Other components***"}
    seen = []
    for key, r in rows.items():
        if r["group"] not in seen:
            seen.append(r["group"])
            out.append(f"| {group_label.get(r['group'], r['group'])} | | |")
        out.append(f"| {r['label']} | {r['rho']:g} | {_sci(r['sigma'], 6)} |")
    out += ["", _caption(table, "iec60949-t2")]
    return "\n".join(out)


def render_table_iii(table: dict) -> str:
    rows = _rows(table, "table_iii")
    out = ["| Insulation | F | Cu `X` (mm²/s)^½ | Cu `Y` (mm²/s) | Al `X` (mm²/s)^½ | Al `Y` (mm²/s) |",
           "|---|---|---|---|---|---|"]
    for key, r in rows.items():
        cu, al = r["copper"], r["aluminium"]
        out.append(f"| {r['label']} | {r['F']:g} | {cu['X']:.2f} | {cu['Y']:.2f} | {al['X']:.2f} | {al['Y']:.2f} |")
    out += ["", _caption(table, "iec60949-t3")]
    return "\n".join(out)


BLOCKS = {
    "iec60949-table-i": render_table_i,
    "iec60949-table-ii": render_table_ii,
    "iec60949-table-iii": render_table_iii,
}


def splice(text: str, name: str, body: str) -> str:
    """Replace the marked block, refusing rather than guessing where it goes."""
    begin, end = BEGIN.format(name=name), END.format(name=name)
    start, stop = text.find(begin), text.find(end)
    if start < 0 or stop < 0 or stop < start:
        raise SystemExit(f"{TARGET_REL}: missing marker for {name!r}. Expected:\n  {begin}\n  {end}")
    return text[: start + len(begin)] + "\n\n" + body + "\n\n" + text[stop:]


def render_document(table: dict, text: str) -> str:
    for name, renderer in BLOCKS.items():
        text = splice(text, name, renderer(table))
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="compare without writing; exit 1 on drift")
    ap.add_argument("--json", action="store_true", help="machine-readable envelope")
    ap.add_argument("--target", default="", help="note path (default: the vault's tables note)")
    ap.add_argument("--data", default="", help="JSON path (default: " + DATA_REL + ")")
    args = ap.parse_args(argv)

    if args.target:
        target = Path(args.target)
    else:
        root = vault_root()
        target = (root / TARGET_REL) if root else None
    if target is None or not target.is_file():
        why = "vault not configured" if target is None else f"note absent: {target}"
        msg = f"skipped — {why} (a public clone has no vault; not a failure)"
        if args.json:
            return emit_json(True, "skipped", msg)
        print(f"skip  {msg}")
        return 0

    data = Path(args.data) if args.data else REPO_ROOT / DATA_REL
    if not args.data and not data.parent.parent.is_dir():
        # The public snapshot drops the cables engine, and with it the
        # transcription; there is nothing to drift. The engine present with
        # its data file missing still fails, in load_table.
        msg = f"skipped — engine absent at {Path(DATA_REL).parent.parent.as_posix()} (not a failure)"
        if args.json:
            return emit_json(True, "skipped", msg)
        print(f"skip  {msg}")
        return 0
    table = load_table(data)
    current = target.read_text(encoding="utf-8")
    updated = render_document(table, current)

    if args.check:
        drifted = updated != current
        msg = (f"{TARGET_REL} Tables I–III no longer match {DATA_REL} — run scripts/gen_kb_tables.py"
               if drifted else f"{TARGET_REL} Tables I–III match {DATA_REL}")
        if args.json:
            return emit_json(not drifted, "drift" if drifted else "ok", msg, {"target": str(target)})
        print(("DRIFT  " if drifted else "ok  ") + msg)
        return 1 if drifted else 0

    if updated != current:
        target.write_text(updated, encoding="utf-8")
        print(f"wrote {target}")
    else:
        print("no change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
