"""Tests for emptyos.sdk.run_status — the cross-harness run phase map.

Pure unit tests; no daemon required. Guards the contract the harness-runs
board (and any future Run Center surface) depends on: every native status
each harness app emits maps to exactly one of the four phases, and unknown
statuses degrade by finished-ness rather than vanishing.
"""

import pytest

from emptyos.sdk.run_status import (
    RUN_PHASE_COLORS,
    RUN_PHASES,
    normalize_run_status,
)


# Native vocabularies the three harness apps actually emit (2026-05-30).
APP_BUILDER_FIX_STATUSES = {
    "queued": "running",
    "running": "running",
    "verifying": "running",
    "ready": "needs-review",
    "merged": "done",
    "verified": "done",
    "no-changes": "done",
    "discarded": "done",
    "error": "failed",
    "timeout": "failed",
    "interrupted": "failed",
    "verify-failed": "failed",
    "verify-timeout": "failed",
    "reverted": "failed",
}

DOGFOOD_STATUSES = {
    "running": "running",
    "ok": "done",
    "error": "failed",
    "interrupted": "failed",
}


@pytest.mark.parametrize("native,phase", APP_BUILDER_FIX_STATUSES.items())
def test_app_builder_fix_statuses(native, phase):
    assert normalize_run_status(native) == phase


@pytest.mark.parametrize("native,phase", DOGFOOD_STATUSES.items())
def test_dogfood_statuses(native, phase):
    assert normalize_run_status(native) == phase


def test_every_phase_is_canonical():
    for native in {**APP_BUILDER_FIX_STATUSES, **DOGFOOD_STATUSES}:
        assert normalize_run_status(native) in RUN_PHASES


def test_unknown_status_degrades_by_finishedness():
    # A future app's unfamiliar status should still land in a real column,
    # chosen by whether the run has finished — never dropped.
    assert normalize_run_status("brand-new-status") == "running"
    assert normalize_run_status("brand-new-status", finished="2026-01-01T00:00:00") == "done"


def test_empty_and_none_are_safe():
    assert normalize_run_status(None) == "running"
    assert normalize_run_status("") == "running"
    assert normalize_run_status(None, finished="2026-01-01") == "done"


def test_case_and_whitespace_insensitive():
    assert normalize_run_status("  READY ") == "needs-review"
    assert normalize_run_status("Merged") == "done"


def test_phase_colors_cover_every_phase():
    assert set(RUN_PHASE_COLORS) == set(RUN_PHASES)
