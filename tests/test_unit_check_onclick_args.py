"""Pin both directions of the inline-handler argument scanner.

A scanner sitting at zero on a healthy tree has proved nothing until it has been
watched going red for each failure shape it claims to cover — the lesson
`check_helper_bindings` left in `.claude/rules/audits.md`. This scanner's first
cut reported ~90 correct `escAttr(JSON.stringify(v))` call sites as dead
handlers, so the *quiet* direction is pinned here just as hard as the loud one.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_onclick_args", ROOT / "scripts" / "check_onclick_args.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def kinds(line: str) -> list[str]:
    return [k for _, k, _ in mod.scan(line)]


# ── the loud direction ───────────────────────────────────────────────────────

BROKEN = [
    # The four shapes that actually shipped.
    """var h = '<tr onclick="toggleGroup('+JSON.stringify(g)+')">';""",
    """s = '<span onclick="editSelectCell(this,\\'f\\',\\'c\\','+JSON.stringify(col.options||[])+')">';""",
    """a += '<button onclick="openPasteModal(' + JSON.stringify(it.id) + ')">x</button>';""",
    """b = '<button onclick="editProvider(' + JSON.stringify(JSON.stringify(v)).slice(1,-1) + ')">Edit</button>';""",
    # Other handler attributes, not just onclick.
    """x = '<input oninput="setVal(' + JSON.stringify(k) + ')">';""",
]


@pytest.mark.parametrize("line", BROKEN)
def test_a_bare_stringify_in_a_handler_is_reported(line):
    assert "raw-stringify" in kinds(line), (
        "this exact shape ships a handler that never binds; the control renders "
        "and does nothing when clicked"
    )


def test_a_hand_rolled_escape_is_reported_as_advisory_not_dead():
    line = """d = '<div onclick="CK.preview(' + JSON.stringify(p).replace(/"/g, "&quot;") + ')">x</div>';"""
    assert kinds(line) == ["hand-rolled"], "correct code must never be called dead"


# ── the quiet direction (the one that broke first) ───────────────────────────

CORRECT = [
    """h = '<button onclick="openDevChange(' + escAttr(JSON.stringify(r.app_id)) + ')">go</button>';""",
    """h = '<button onclick="openDevChange(' + EOS_UI.escAttr(JSON.stringify(id)) + ')">go</button>';""",
    """h = '<button onclick="openDevChange(' + EOS_UI.jsArg(id) + ')">go</button>';""",
    """h = '<button onclick="openDevChange(' + jsArg(id) + ')">go</button>';""",
    # Not in a handler attribute at all — a data attribute is inert.
    """h = '<div data-payload="' + JSON.stringify(v) + '">x</div>';""",
    # No interpolation into an attribute.
    """var payload = JSON.stringify({a: 1});""",
]


@pytest.mark.parametrize("line", CORRECT)
def test_correct_and_irrelevant_forms_stay_silent(line):
    assert kinds(line) == [], f"false positive on correct code: {line!r}"


def test_the_inline_opt_out_is_honoured():
    line = ("""h = '<b onclick="f(' + JSON.stringify(x) + ')">y</b>';"""
            """  // onclick-args: ignore — value is a bare integer""")
    assert kinds(line) == []


def test_the_real_tree_is_clean():
    """The whole point: on a healthy tree this must be silent, or it is noise."""
    from scanner_lib import page_files

    findings = []
    for path in page_files(ROOT):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line, kind, _ in mod.scan(text):
            if kind == "raw-stringify":
                findings.append(f"{path.relative_to(ROOT)}:{line}")
    assert findings == [], f"dead inline handlers present: {findings}"
