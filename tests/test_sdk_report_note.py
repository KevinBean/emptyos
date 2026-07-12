"""Unit tests for BaseApp.save_report_note.

The shared "AI report → outputs/ snapshot note" pattern used by expense,
finance, and braindump. Pure SDK — binds the real BaseApp method onto a stub
``self`` writing to a temp vault (vault_index absent → direct-write fallback),
so it needs no kernel/daemon and is CI-safe.
"""
from __future__ import annotations

import types

from emptyos.sdk.base_app import BaseApp


def _stub_app(vault_root, app_id="report-test"):
    """A minimal object carrying just the BaseApp methods the helper touches."""
    Stub = type("Stub", (), {})
    for n in (
        "save_report_note",
        "vault_create_note",
        "vault_rel",
        "vault_path",
        "vault_dir",
        "_infer_lifecycle",
    ):
        setattr(Stub, n, getattr(BaseApp, n))
    s = Stub()
    s.manifest = types.SimpleNamespace(id=app_id)
    s.vault_root = vault_root
    # get_optional(None) for both the vault_dir prefix-settings lookup and the
    # vault_index lookup inside vault_create_note → direct-write fallback path.
    s.kernel = types.SimpleNamespace(
        services=types.SimpleNamespace(get_optional=lambda k: None),
        config=types.SimpleNamespace(notes_path=vault_root),
    )
    return s


def test_save_report_note_stamps_authorship_and_returns_rel(tmp_path):
    app = _stub_app(tmp_path)
    rel = app.save_report_note(
        "2026-07-expense-report.md",
        title="Expense report 2026-07",
        body="## Summary\n\nSpent a lot.\n",
        tags=["expense", "report"],
        extra={"month": "2026-07", "total": 1234.5},
    )
    assert rel == "30_Resources/EmptyOS/report-test/outputs/2026-07-expense-report.md"
    note = (tmp_path / rel).read_text(encoding="utf-8")
    # authorship-boundary convention stamped on every report
    assert "author: ai" in note
    assert "lifecycle: snapshot" in note
    assert "as_of:" in note
    # caller-supplied fields
    assert "title: Expense report 2026-07" in note
    assert "month:" in note and "2026-07" in note
    assert "Spent a lot." in note


def test_save_report_note_default_tags_and_as_of(tmp_path):
    app = _stub_app(tmp_path)
    rel = app.save_report_note("r.md", title="R", body="body")
    note = (tmp_path / rel).read_text(encoding="utf-8")
    assert "- report" in note  # default tag
    assert "as_of:" in note   # defaults to today, always present


def test_save_report_note_explicit_as_of_wins(tmp_path):
    app = _stub_app(tmp_path)
    rel = app.save_report_note(
        "2020-01-dump.md", title="Old", body="x", as_of="2020-01-15"
    )
    note = (tmp_path / rel).read_text(encoding="utf-8")
    assert "as_of: '2020-01-15'" in note or "as_of: 2020-01-15" in note


def test_save_report_note_extra_merges_last(tmp_path):
    app = _stub_app(tmp_path)
    rel = app.save_report_note(
        "n.md", title="N", body="b", extra={"run_id": "abc123", "net_worth_aud": 500000}
    )
    note = (tmp_path / rel).read_text(encoding="utf-8")
    assert "run_id: abc123" in note
    assert "net_worth_aud:" in note and "500000" in note
