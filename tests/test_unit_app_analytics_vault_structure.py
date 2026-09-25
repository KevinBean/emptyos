"""Pure tests for the vault-structure purge-action helpers
(apps/.../app-analytics/app.py: vault_structure_record, should_notify_vault_structure).

app-analytics-vault-structure-purge-action: closes the gap-analysis finding
"vault-health findings have no one-click fix/purge action" — the endpoints
GET /api/vault-structure, POST /api/vault-structure/scan, POST
/api/vault-structure/purge wrap scripts/check_vault_structure.py --json
(optionally --purge). This file pins the pure envelope -> state-record
mapping and the notify gate; the subprocess call + load_state/save_state I/O
are exercised live (sandbox) rather than mocked here.
"""

from __future__ import annotations

import pytest

from helpers import load_app_module


@pytest.fixture(scope="module")
def app_mod():
    return load_app_module("app-analytics", "app", preload=("vault_mixin",))


def test_record_shapes_a_healthy_envelope(app_mod):
    env = {
        "ok": True,
        "message": "0 auto-fixable, 0 review, 0 advisory",
        "data": {"safe_count": 0, "review_count": 0, "advisory": {}},
    }
    record = app_mod.vault_structure_record(env, ts="2026-08-23T00:00:00")
    assert record == {
        "ts": "2026-08-23T00:00:00",
        "ok": True,
        "message": "0 auto-fixable, 0 review, 0 advisory",
        "safe_count": 0,
        "review_count": 0,
        "advisory": {},
        "purged": None,
    }


def test_record_carries_purge_result_when_present(app_mod):
    env = {
        "ok": True,
        "message": "0 auto-fixable, 2 review, 1 advisory",
        "data": {
            "safe_count": 0,
            "review_count": 2,
            "advisory": {"large_note": ["a.md"]},
            "purged": {"files_deleted": 5, "dirs_deleted": 3},
            "snapshot": "snap-123",
        },
    }
    record = app_mod.vault_structure_record(env, ts="t")
    assert record["purged"] == {"files_deleted": 5, "dirs_deleted": 3}
    assert record["review_count"] == 2
    assert record["advisory"] == {"large_note": ["a.md"]}


def test_record_degrades_on_a_malformed_or_crashed_envelope(app_mod):
    # A subprocess crash or non-JSON stdout parses to `{}` upstream — the
    # mapping must not raise and must fail closed on the counts.
    record = app_mod.vault_structure_record({}, ts="t")
    assert record == {
        "ts": "t",
        "ok": None,
        "message": "",
        "safe_count": 0,
        "review_count": 0,
        "advisory": {},
        "purged": None,
    }


def test_record_handles_a_none_data_block(app_mod):
    # data: null (rather than missing) must not crash `.get()` on it.
    record = app_mod.vault_structure_record({"ok": False, "data": None}, ts="t")
    assert record["safe_count"] == 0
    assert record["ok"] is False


@pytest.mark.parametrize(
    ("safe", "review", "expected"),
    [
        (0, 0, False),
        (1, 0, True),
        (0, 1, True),
        (3, 2, True),
    ],
)
def test_notify_gate_fires_only_when_theres_something_to_act_on(app_mod, safe, review, expected):
    record = {"safe_count": safe, "review_count": review}
    assert app_mod.should_notify_vault_structure(record) is expected


def test_argv_always_scopes_to_the_callers_own_vault(app_mod):
    """Regression pin: without an explicit --vault, check_vault_structure.py
    falls back to reading notes.path out of <repo>/emptyos.toml — the MAIN
    daemon's config — regardless of which daemon spawned the subprocess.
    Found live on a sandbox member: a scan (and a --purge!) triggered from a
    throwaway sandbox vault would silently target the REAL user vault.
    """
    argv = app_mod.vault_structure_argv("script.py", "/some/other/vault", purge=False)
    assert "--vault" in argv
    assert argv[argv.index("--vault") + 1] == "/some/other/vault"


def test_argv_purge_flag_is_opt_in(app_mod):
    scan_argv = app_mod.vault_structure_argv("script.py", "/v", purge=False)
    purge_argv = app_mod.vault_structure_argv("script.py", "/v", purge=True)
    assert "--purge" not in scan_argv
    assert "--purge" in purge_argv
    assert "--json" in scan_argv and "--json" in purge_argv
