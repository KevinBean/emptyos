"""Unit tests for `scripts/check_deferred_work.py` (eos-insights, 2026-08-05).

Pins BOTH directions, per `.claude/rules/audits.md`: the scanner must fire on a
genuinely malformed or aged row, and must stay silent on a healthy registry.

The silence half is the load-bearing one. A first cut of this scanner also
flagged rows whose Reference path existed on disk; measured against the real
registry that fired on **77 of 128 deferred rows (60%)**, because a Reference is
usually a model to copy rather than a precondition. It was cut, and
`test_a_healthy_table_is_silent` is what stops it coming back — a scanner that
flags the majority is one nobody reads.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_deferred_work", _SCRIPTS / "check_deferred_work.py"
    )
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        pytest.skip(f"scanner not importable: {e}")
    return mod


_m = _load()

_HEADER = (
    "| Feature | Build / deploy trigger | Reference | Full verdict / source | Added | Status |\n"
    "|---|---|---|---|---|---|\n"
)


def _row(feature, added, status="deferred", ref="`emptyos/sdk/pipeline.py`"):
    return f"| {feature} | some prose trigger | {ref} | a verdict | {added} | {status} |\n"


# ── parsing ──────────────────────────────────────────────────────────────────

def test_a_healthy_table_parses_with_no_malformed_rows():
    rows, malformed = _m.parse_rows(_HEADER + _row("A", "2026-07-01") + _row("B", "2026-07-02"))
    assert len(rows) == 2
    assert malformed == []
    assert rows[0]["feature"] == "A"
    assert rows[0]["status"] == "deferred"


def test_a_short_row_is_malformed_with_its_line_number():
    """A row missing a column puts Added/Status in the wrong slots, so nothing
    can age or close it. Two such rows were live in the real doc."""
    short = "| Missing columns | trigger only | 2026-07-01 |\n"
    rows, malformed = _m.parse_rows(_HEADER + _row("A", "2026-07-01") + short)
    assert len(rows) == 1
    assert len(malformed) == 1
    lineno, starts = malformed[0]
    assert lineno == 4  # header, separator, row A, short row
    assert "Missing columns" in starts


def test_columns_come_from_the_header_not_a_hardcoded_list():
    """A renamed or added column must not silently mis-slot every Status."""
    header = (
        "| Feature | Build / deploy trigger | Reference | Full verdict / source "
        "| Owner | Added | Status |\n|---|---|---|---|---|---|---|\n"
    )
    line = "| A | t | r | v | kevin | 2026-07-01 | deferred |\n"
    rows, malformed = _m.parse_rows(header + line)
    assert malformed == []
    assert rows[0]["status"] == "deferred"
    assert rows[0]["added"] == "2026-07-01"


def test_prose_outside_a_table_is_ignored():
    text = "Some intro paragraph.\n\n" + _HEADER + _row("A", "2026-07-01")
    rows, malformed = _m.parse_rows(text)
    assert len(rows) == 1 and malformed == []


# ── classification ───────────────────────────────────────────────────────────

def test_an_old_deferred_row_ages():
    today = date(2026, 8, 5)
    row = {"status": "deferred", "added": "2026-01-01"}
    cls, age = _m.classify(row, today, 90)
    assert cls == "AGING"
    assert age > 200


def test_a_recent_deferred_row_is_soaking():
    today = date(2026, 8, 5)
    cls, age = _m.classify({"status": "deferred", "added": "2026-07-30"}, today, 90)
    assert cls == "soaking"
    assert age == 6


def test_a_closed_row_never_ages():
    today = date(2026, 8, 5)
    for status in ("built", "shipped", "dropped", "**triggered 2026-08-05** — built as X"):
        cls, age = _m.classify({"status": status, "added": "2020-01-01"}, today, 90)
        assert cls == "closed", status
        assert age is None


def test_a_dateless_row_is_flagged_not_silently_soaking():
    """Without an Added date a row can never age — that is worse than being old,
    so it must surface rather than sit in the quiet bucket forever."""
    cls, age = _m.classify({"status": "deferred", "added": ""}, date(2026, 8, 5), 90)
    assert cls == "undated"
    assert age is None


# ── the cut heuristic must not come back ─────────────────────────────────────

def test_reference_path_existing_does_not_flag_a_row():
    """The 60%-false-positive heuristic. `emptyos/sdk/pipeline.py` genuinely
    exists in this repo; a recent row citing it must still be `soaking`."""
    recent = (date.today() - timedelta(days=3)).isoformat()
    rows, _ = _m.parse_rows(_HEADER + _row("A", recent, ref="`emptyos/sdk/pipeline.py`"))
    cls, _age = _m.classify(rows[0], date.today(), 90)
    assert cls == "soaking"


def test_a_healthy_table_is_silent():
    """End-to-end: nothing aged, nothing malformed → nothing to report."""
    recent = (date.today() - timedelta(days=5)).isoformat()
    rows, malformed = _m.parse_rows(_HEADER + _row("A", recent) + _row("B", recent))
    flagged = [r for r in rows if _m.classify(r, date.today(), 90)[0] in ("AGING", "undated")]
    assert flagged == []
    assert malformed == []


# ── the live registry stays parseable ────────────────────────────────────────

def test_the_real_registry_has_no_malformed_rows():
    """Guards the repair done on 2026-08-05 — two rows were short a column."""
    doc = _REPO / "docs" / "DEFERRED-WORK.md"
    if not doc.exists():
        pytest.skip("registry not present")
    rows, malformed = _m.parse_rows(doc.read_text(encoding="utf-8"))
    assert malformed == [], f"malformed rows at lines {[n for n, _ in malformed]}"
    assert len(rows) > 50, "parser stopped finding rows — header detection may have broken"
