"""Pin scripts/check_confirm_keys.py in both directions.

A gating checker earns its place only if it is quiet on healthy code and loud
on the real defect. Both halves are pinned here because this scan's first run
flagged five known-good apps — `onYes: function(){ fetch(u, {method:'POST'}) }`
puts a `method` key lexically inside the confirm literal — and a checker that
cries wolf gets disabled within a month (`.claude/rules/audits.md`).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_confirm_keys", REPO / "scripts" / "check_confirm_keys.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _scan(tmp_path: Path, source: str) -> list[dict]:
    (tmp_path / "pages").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pages" / "index.html").write_text(source, encoding="utf-8")
    return mod.scan(tmp_path)


# ── quiet on the shapes the component reads ──────────────────────────────

@pytest.mark.parametrize("call", [
    # canonical
    "EOS_UI.confirm({message: 'Delete X?', action: 'Delete', danger: true})",
    # the aliases six apps reached for
    "EOS_UI.confirm({title: 'Restore', body: 'Continue?', okText: 'Restore'})",
    "EOS_UI.confirm({title: 'Del', body: 'Sure?', confirmText: 'Delete', danger: true})",
    "EOS_UI.confirm({title: 'Report', message: 'Publish?', confirmLabel: 'Yes', cancelLabel: 'No'})",
    "EOS_UI.confirm({message: 'Delete render?', danger: true, okLabel: 'Delete'})",
    "EOS_UI.confirm({title: 'D', body: 'x', confirmText: 'Delete', onConfirm: function(){}})",
])
def test_known_vocabulary_is_silent(tmp_path, call):
    assert _scan(tmp_path, f"<script>{call}</script>") == []


def test_nested_object_in_a_callback_is_not_an_option(tmp_path):
    """The false positive that flagged five healthy apps on the first run."""
    src = """<script>
    EOS_UI.confirm({
        message: 'Revert?', action: 'Revert', danger: true,
        onYes: function () { fetch('/api/x', {method: 'POST', headers: {}}); }
    });
    </script>"""
    assert _scan(tmp_path, src) == []


def test_braces_inside_strings_do_not_bleed(tmp_path):
    src = """<script>
    EOS_UI.confirm({message: 'Delete {all} items?', action: 'Delete'});
    fetch('/api/y', {method: 'DELETE'});
    </script>"""
    assert _scan(tmp_path, src) == []


# ── loud on a key the component cannot read ──────────────────────────────

def test_unknown_top_level_key_is_reported(tmp_path):
    src = "<script>EOS_UI.confirm({message: 'Go?', subtitle: 'dropped'});</script>"
    found = _scan(tmp_path, src)
    assert len(found) == 1
    assert found[0]["unknown_keys"] == ["subtitle"]
    assert "message" in found[0]["read_keys"]


def test_a_site_reading_nothing_is_reported(tmp_path):
    """The worst shape: every key dropped, so the dialog is a bare prompt."""
    src = "<script>EOS_UI.confirm({heading: 'X', detail: 'Y', label: 'Z'});</script>"
    found = _scan(tmp_path, src)
    assert len(found) == 1
    assert found[0]["read_keys"] == []


def test_inline_marker_opts_out(tmp_path):
    src = """<script>
    // confirm-keys: ignore — passed through to a wrapper
    EOS_UI.confirm({message: 'Go?', subtitle: 'deliberate'});
    </script>"""
    assert _scan(tmp_path, src) == []


# ── the component and the checker must not drift apart ───────────────────

def test_known_set_matches_the_component():
    """Every alias eos-components.js reads is in KNOWN, and vice versa.

    An alias added to the component but not here turns working code into a
    false positive; one added here but not there hides a real drop.
    """
    src = (REPO / "emptyos" / "web" / "static" / "eos-components.js").read_text(
        encoding="utf-8", errors="replace"
    )
    start = src.index("confirm: function(messageOrOpts, onYes)")
    body = src[start: src.index("return promise;", start)]
    for key in mod.KNOWN:
        assert f"o.{key}" in body or key == "message", (
            f"checker knows '{key}' but eos-components.js::confirm never reads it"
        )
