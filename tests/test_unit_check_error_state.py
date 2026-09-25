"""Both-direction pins for scripts/check_error_state.py.

Per `.claude/skills/eos-graduate-audit`, a graduated checker ships with tests
pinning BOTH "fires on the real regression" and "silent on healthy code". The
silent half matters more here than usual: the scanner reports on ~24% of live
page files, so a widening of its signal would be very expensive and very quiet.

`scan_text` is pure, so every case below is a string — no fixture tree, no
daemon, no I/O.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_error_state.py"
_spec = importlib.util.spec_from_file_location("check_error_state", SCRIPT)
ces = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ces)


def wrap(body: str) -> str:
    return "async function load() {\n  try {\n    await go();\n  } catch (e) {\n" + body + "\n  }\n}\n"


# ── Fires ────────────────────────────────────────────────────────────


def test_fires_on_a_failure_rendered_with_an_empty_class():
    src = wrap("    el.innerHTML = '<div class=\"eos-empty\">Failed to load</div>';")
    assert len(ces.scan_text(src)) == 1


def test_fires_on_a_muted_classname_assignment():
    """The class need not be inside a markup string — `className='muted'` is
    the same defect written differently."""
    src = wrap("    el.className = 'muted';\n    el.textContent = 'Rollup failed.';")
    assert len(ces.scan_text(src)) == 1


def test_fires_on_an_inline_muted_colour():
    src = wrap("    el.innerHTML = '<p style=\"color:var(--text-muted)\">Gone</p>';")
    assert len(ces.scan_text(src)) == 1


def test_prose_saying_it_failed_does_not_excuse_the_styling():
    """The defect is the visual treatment, not the wording. "Could not load"
    tells the user it failed *in the typography reserved for "nothing here"*,
    which is precisely the collapse this scanner exists to find."""
    src = wrap("    el.innerHTML = '<div class=\"x-empty\">Could not load sessions.</div>';")
    assert len(ces.scan_text(src)) == 1


# ── Silent ───────────────────────────────────────────────────────────


def test_silent_when_the_page_styles_the_failure():
    src = wrap("    el.innerHTML = '<p class=\"re-empty re-status-err\">Gone</p>';")
    assert ces.scan_text(src) == []


def test_silent_on_a_danger_colour_role():
    src = wrap("    el.innerHTML = '<div style=\"color:var(--danger)\">Gone</div>';")
    assert ces.scan_text(src) == []


def test_silent_when_the_catch_uses_the_shared_helper():
    src = wrap("    el.innerHTML = EOS_UI.errorState({message: 'x', onRetry: 'load()'});")
    assert ces.scan_text(src) == []


def test_helper_named_only_in_a_comment_does_not_excuse_a_grey_catch():
    """The exemption is a *call*. A marker string in a comment is the grep-
    satisfying shape audits.md § Failure mode 3 names."""
    src = wrap("    // was: el.innerHTML = EOS_UI.errorState({message: 'x'});\n"
               "    el.innerHTML = '<div class=\"empty\">x</div>';")
    assert len(ces.scan_text(src)) == 1


def test_fires_on_a_catch_that_renders_the_shared_empty_state():
    """`EOS_UI.emptyState` carries the empty-state class inside the helper, so a
    class-string scan never sees it — yet a catch rendering it is exactly the
    defect: a failure painted as "nothing here"."""
    src = wrap("    el.innerHTML = EOS_UI.emptyState({message: 'No items'});")
    assert len(ces.scan_text(src)) == 1


def test_helper_elsewhere_in_the_file_does_not_excuse_a_grey_catch():
    """Adoption is per catch block. The first version exempted the whole file
    on one `EOS_UI.errorState` call and hid 23 findings in 8 files — a page
    that converted three catches of four kept the fourth grey, unseen."""
    src = wrap("    el.innerHTML = '<div class=\"empty\">x</div>';") + \
        "\nfunction other() { EOS_UI.errorState({message: 'x'}); }\n"
    assert len(ces.scan_text(src)) == 1


def test_silent_when_the_catch_renders_nothing():
    assert ces.scan_text(wrap("    return;")) == []


def test_silent_on_an_empty_state_outside_a_catch():
    src = "function render(rows) {\n  if (!rows.length) " \
          "el.innerHTML = '<div class=\"empty\">No tasks yet</div>';\n}\n"
    assert ces.scan_text(src) == []


def test_a_single_line_catch_does_not_swallow_the_code_after_it():
    """The false positive that retired the bounded-window match.

    `catch(e){ return; }` closes on its own line, so a window looking for a
    newline-then-`}` ran past it into the success path and reported an empty
    state the catch never reaches — measured on
    `devices/pages/index.html` (then under extension/others, now extension/dev).
    """
    src = (
        "async function loadSims() {\n"
        "  let d; try { d = await go(); } catch(e){ return; }\n"
        "  const sims = d.simulators || [];\n"
        "  el.innerHTML = sims.length ? render(sims) "
        ": '<p class=\"muted\">No simulators registered.</p>';\n"
        "}\n"
    )
    assert ces.scan_text(src) == []


# ── Opt-out ──────────────────────────────────────────────────────────


def test_opt_out_marker_inside_the_block_silences_it():
    src = wrap(
        "    // error-state: intentional — a stale list beats an error card\n"
        "    el.innerHTML = '<div class=\"empty\">x</div>';"
    )
    assert ces.scan_text(src) == []


def test_opt_out_marker_above_the_block_silences_it():
    src = "// error-state: intentional — deliberate silent degrade\n" + \
        wrap("    el.innerHTML = '<div class=\"empty\">x</div>';")
    assert ces.scan_text(src) == []


def test_an_unrelated_comment_does_not_silence_it():
    src = wrap(
        "    // TODO: show something better here one day\n"
        "    el.innerHTML = '<div class=\"empty\">x</div>';"
    )
    assert len(ces.scan_text(src)) == 1


# ── Live tree ────────────────────────────────────────────────────────


def test_the_repo_scan_runs_and_reports_a_plausible_shape():
    """Not a threshold — a count assertion here would fail on every honest fix.
    This pins that the scan executes over the real tree and returns findings
    with the fields the report and the JSON envelope both read.
    """
    findings, total = ces.scan(Path(__file__).resolve().parent.parent)
    assert total > 100, "the page-file walk found almost nothing"
    assert findings, "scanner found nothing at all — the file walk is broken"
    for f in findings[:5]:
        assert f["file"].startswith("apps/")
        assert isinstance(f["line"], int) and f["line"] > 0
        assert "emitted" in f
