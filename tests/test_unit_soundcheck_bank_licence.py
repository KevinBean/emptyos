"""The soundcheck bank ships no wordfreq data.

wordfreq's code is Apache-2.0 but its frequency data is CC BY-SA 4.0. The bank
builder uses Zipf values at build time only (word floors, difficulty); writing
them into the committed bank would put share-alike data in every build that
ships soundcheck, including the public EnglishOS edition (editions M10). Both
halves are pinned: the committed files, and the builder that regenerates them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from helpers import app_path, requires_app  # noqa: E402

pytestmark = requires_app("soundcheck")

LICENSED_KEYS = {"freq"}


def _bank_lines():
    bank = app_path("soundcheck") / "bank"
    files = sorted(bank.glob("*.jsonl"))
    assert files, "no bank files found — the check below would pass vacuously"
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                yield f.name, n, json.loads(line)


def test_the_committed_bank_carries_no_frequency_data():
    rows = list(_bank_lines())
    assert len(rows) > 100
    leaks = [(name, n) for name, n, item in rows if LICENSED_KEYS & set(item)]
    assert not leaks, f"CC BY-SA frequency data in the shipped bank: {leaks[:5]}"


def test_the_builder_does_not_write_frequency_data():
    from check_common import load_by_path

    builder = load_by_path("soundcheck_builder_under_test", "scripts/build_soundcheck_bank.py")
    out = builder.render([{"id": "x", "freq": [4.4, 4.0], "answer": ["a"]}])
    assert json.loads(out) == {"id": "x", "answer": ["a"]}
    assert LICENSED_KEYS <= set(builder.BUILD_ONLY_KEYS)
