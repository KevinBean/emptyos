"""interference's field-profile sheet against its own REPORT-SPEC.md.

Daemon-free. The app uses a relative import, so its parent package is registered
before loading — the pattern in `.claude/rules/multi-module-apps.md`.

Two clauses here were live defects, both of values the result already carried and
the sheet never printed: which E-field solver ran (ipl / splc / mom3d differ in
accuracy), and that the sample table is decimated.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_BASE = ROOT / "apps/extension/engineering/interference"
_SPEC = _BASE / "REPORT-SPEC.md"


def _App():
    pkg = types.ModuleType("_itf_pkg")
    pkg.__path__ = [str(_BASE)]
    sys.modules["_itf_pkg"] = pkg
    for sub in ("references",):
        sp = importlib.util.spec_from_file_location(f"_itf_pkg.{sub}", _BASE / f"{sub}.py")
        mm = importlib.util.module_from_spec(sp)
        sys.modules[f"_itf_pkg.{sub}"] = mm
        sp.loader.exec_module(mm)
    sp = importlib.util.spec_from_file_location("_itf_pkg.app", _BASE / "app.py")
    m = importlib.util.module_from_spec(sp)
    sys.modules["_itf_pkg.app"] = m
    sp.loader.exec_module(m)
    return m.InterferenceApp


STUDY = {
    "name": "22kV trench", "frequency_hz": 50, "line_length_m": 100,
    "conductors": [{
        "x_start": 0, "y_start": -0.3, "z_start": 8,
        "x_end": 100, "y_end": -0.3, "z_end": 8,
        "current_a": 400, "current_phase_deg": 0, "voltage_kv": 12.7,
    }],
}


def _result(n=101, **over):
    samples = [{"position": -50 + i, "B": 1e-6 * (1 + i % 3), "E": 900.0 + i}
               for i in range(n)]
    base = {"ok": True, "axis": "y", "n_samples": n, "samples": samples,
            "max_B": 3e-6, "has_efield": True, "e_model": "splc",
            "max_E": 1500.0, "e_approximate": False}
    base.update(over)
    return base


def _sheet(result=None, study=None):
    return _App()._profile_report_markdown(study or STUDY, result or _result())


# ── § 2 — the arrangement the field derives from ─────────────────────────────

def test_conductor_arrangement_is_printed():
    """A profile without the arrangement cannot be reproduced at all."""
    md = _sheet()
    assert "## 2 Conductors" in md
    assert "(0.00, -0.30, 8.00)" in md
    assert "| 400.0 |" in md


def test_conductor_role_is_shown():
    md = _sheet()
    assert "| phase |" in md


# ── § 4 — which solver produced the E-field ──────────────────────────────────

def test_efield_names_its_solver():
    """ipl / splc / mom3d differ in accuracy; the result recorded which ran and
    the sheet never printed it."""
    md = _sheet()
    assert "Solver: **splc**" in md


def test_approximate_flag_reaches_the_sheet():
    md = _sheet(_result(e_approximate=True))
    assert "flagged this result **approximate**" in md


def test_no_voltage_says_e_was_not_computed():
    md = _sheet(_result(has_efield=False, max_E=None, e_model=None))
    assert "E-field was not computed" in md
    assert "Solver:" not in md


# ── § 5 — decimation must not be silent ──────────────────────────────────────

def test_decimated_table_states_what_it_dropped():
    md = _sheet(_result(n=101))
    assert "Showing **26 of 101** samples" in md
    assert "every 4th" in md


def test_undecimated_table_says_all_samples_shown():
    md = _sheet(_result(n=10))
    assert "All **10** samples shown" in md
    assert "Showing **" not in md


# ── disclosures ──────────────────────────────────────────────────────────────

def test_sheet_states_it_does_not_model_victim_conductors():
    """The app warns when a study *name* implies a victim study; a name that
    avoided the warning does not widen the scope, so the sheet says it always."""
    md = _sheet()
    assert "does **not** model victim conductors" in md
    assert "induced voltage" in md


def test_reference_level_population_is_named():
    """Per-line, not document-wide. Both the B and the E line quote a
    percentage against a reference level, so an `in md` assertion passes while
    one of them silently loses its population — which is exactly what survived
    the mutation pass that produced this assertion. Same shape as the earthing
    preset test; `.claude/rules/audits.md` names the class."""
    md = _sheet()
    b_line = [ln for ln in md.splitlines() if ln.startswith("Max |B|:")]
    e_line = [ln for ln in md.splitlines() if ln.startswith("Max |E|:")]
    assert b_line and e_line, "both field lines must be present"
    for line in (b_line[0], e_line[0]):
        assert "**public**" in line, f"population not named on: {line[:60]}"
    assert "occupational" in md


def test_sheet_says_a_reference_level_is_not_a_limit():
    assert "A reference level is not a limit" in _sheet()


def test_validation_anchors_b_and_disclaims_e():
    md = _sheet()
    body = md.split("## Validation")[1]
    assert "long-wire-1ka-2m" in body
    assert "Not anchored" in body
    assert "mom3d" in body


# ── the grid report carries the same disclosures ─────────────────────────────

def test_grid_report_carries_the_disclosure_block():
    App = _App()
    grid = {"ok": True, "max_B": 3e-6, "rows": [], "grid": [],
            "has_efield": False, "n": 0}
    md = App._grid_report_markdown(STUDY, grid, {})
    assert "does **not** model victim conductors" in md
    assert "## Validation" in md


@pytest.mark.parametrize("heading", [
    "## 1 Study", "## 2 Conductors", "## 3 Magnetic field",
    "## 4 Electric field", "## 5 Profile samples",
    "## Source and ambiguity disclosure", "## Validation", "## Hand check",
])
def test_required_section_present(heading):
    assert heading in _sheet()


# ── the contract itself ──────────────────────────────────────────────────────

def test_spec_satisfies_the_assurance_document_contract():
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_engineering_assurance import DOCS
    text = _SPEC.read_text(encoding="utf-8")
    missing = [h for h in DOCS["report_spec"] if h not in text]
    assert not missing, f"REPORT-SPEC.md missing {missing}"


def test_reports_render_from_the_run_the_ui_shows():
    """The sheet renders the run's numbers; it must never recompute them.

    Reads the source off the bound method rather than grepping app.py between
    two `def` boundaries. That grep pinned the report renderer to one FILE, so
    it broke the moment the app was decomposed into helper modules — while the
    property it actually cares about had not changed at all. `getsource` also
    drops the fragile "next function name" end-marker.
    """
    import inspect

    body = inspect.getsource(_App()._profile_report_markdown)
    for forbidden in ("field_along_axis(", "efield_profile(", "solve_charges("):
        assert forbidden not in body, f"{forbidden} recomputes what run_profile produced"
