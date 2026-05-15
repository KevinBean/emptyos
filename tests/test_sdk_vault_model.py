"""Tests for emptyos.sdk.vault_model.VaultModel.

Exercises the read/write/validate path against a fake VaultIndex stub —
no daemon, no real vault. Catches the four pain points VaultModel exists
to fix:

  1. YAML returns numbers as strings ("85" not 85)
  2. Required fields missing in legacy notes
  3. Old frontmatter shapes (Application_Sent: true) → new (status)
  4. set_field whitelist drift from boards integration
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import pytest

from emptyos.sdk.vault_model import (
    VaultModel,
    coerce_int_or_none,
    int_or_none_validator,
    date_or_none_validator,
)


# ── A realistic subclass — modelled after apps/personal/jobs/ ──


class JobApplication(VaultModel):
    TAG = "job-application"
    FOLDER = "20_Areas/Career/Job-Applications"

    company: str
    role: str = ""
    status: Literal[
        "shortlisted", "applied", "phone_screen", "interview",
        "offer", "accepted", "rejected", "withdrawn", "not_pursuing",
    ] = "applied"
    match_score: int | None = None
    salary: str = ""
    priority: int | None = None
    created: date | None = None

    _coerce_score = int_or_none_validator("match_score")
    _coerce_priority = int_or_none_validator("priority")
    _coerce_created = date_or_none_validator("created")

    @classmethod
    def _legacy_aliases(cls, raw: dict) -> dict:
        if "status" not in raw or not raw.get("status"):
            if str(raw.get("Application_Sent", "")).lower() == "true":
                raw["status"] = "interview"
            elif str(raw.get("Application", "")).lower() == "true":
                raw["status"] = "applied"
            elif str(raw.get("Active", "")).lower() == "false":
                raw["status"] = "rejected"
        return raw


# ── Fake app stub ──


class FakeApp:
    """Minimal stand-in for BaseApp with the four vault methods we touch."""

    def __init__(self):
        # path -> frontmatter dict
        self.notes: dict[str, dict] = {}
        # path -> tags list
        self.tags: dict[str, list[str]] = {}
        self.updates: list[tuple[str, dict]] = []

    def vault_query(self, tags=None, folder=None, **props):
        out = []
        want_tags = set(tags or [])
        for path, fm in self.notes.items():
            if want_tags and not want_tags.issubset(set(self.tags.get(path, []))):
                continue
            if folder and not path.startswith(folder):
                continue
            out.append({
                "path": path,
                "name": path.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                "properties": dict(fm),
                "tags": list(self.tags.get(path, [])),
            })
        return out

    def vault_get_properties(self, path):
        fm = self.notes.get(path)
        return dict(fm) if fm else {}

    def vault_update(self, path, properties):
        self.updates.append((path, dict(properties)))
        self.notes.setdefault(path, {}).update(properties)


# ── Tests ──


def test_coerce_int_handles_yaml_string_numbers():
    assert coerce_int_or_none("85") == 85
    assert coerce_int_or_none(85) == 85
    assert coerce_int_or_none("") is None
    assert coerce_int_or_none(None) is None
    assert coerce_int_or_none("not a number") is None
    # YAML sometimes returns floats: "85.0"
    assert coerce_int_or_none("85.0") == 85


def test_read_all_skips_invalid_notes_without_crashing():
    """One bad note must not blow up the whole list."""
    app = FakeApp()
    # Valid record
    app.notes["20_Areas/Career/Job-Applications/acme/_app.md"] = {"company": "Acme", "match_score": "85"}
    app.tags["20_Areas/Career/Job-Applications/acme/_app.md"] = ["job-application"]
    # Invalid: missing required `company`
    app.notes["20_Areas/Career/Job-Applications/broken/_app.md"] = {"role": "Engineer"}
    app.tags["20_Areas/Career/Job-Applications/broken/_app.md"] = ["job-application"]
    # Wrong tag — should be filtered out before validation
    app.notes["20_Areas/Career/Job-Applications/other/_app.md"] = {"company": "Other"}
    app.tags["20_Areas/Career/Job-Applications/other/_app.md"] = ["wrong-tag"]

    results = JobApplication.read_all(app)
    assert len(results) == 1
    assert results[0].company == "Acme"
    assert results[0].match_score == 85   # coerced from string


def test_legacy_aliases_translate_old_boolean_fields():
    """Old vault notes with Application_Sent: true should resolve to status='interview'."""
    app = FakeApp()
    app.notes["20_Areas/Career/Job-Applications/old/_app.md"] = {
        "company": "OldCo",
        "Application_Sent": "true",
    }
    app.tags["20_Areas/Career/Job-Applications/old/_app.md"] = ["job-application"]
    app.notes["20_Areas/Career/Job-Applications/older/_app.md"] = {
        "company": "OlderCo",
        "Active": "false",
    }
    app.tags["20_Areas/Career/Job-Applications/older/_app.md"] = ["job-application"]

    by_company = {r.company: r for r in JobApplication.read_all(app)}
    assert by_company["OldCo"].status == "interview"
    assert by_company["OlderCo"].status == "rejected"


def test_settable_fields_derived_from_model():
    """No hand-maintained whitelist drift — derived from declared fields."""
    fields = JobApplication.settable_fields()
    assert "status" in fields
    assert "match_score" in fields
    assert "company" in fields
    assert "_vault_path" not in fields  # private, excluded


def test_update_rejects_invalid_status_value():
    """Validating before write catches typos / wrong enum values."""
    app = FakeApp()
    app.notes["20_Areas/Career/Job-Applications/acme/_app.md"] = {"company": "Acme", "status": "applied"}
    app.tags["20_Areas/Career/Job-Applications/acme/_app.md"] = ["job-application"]

    res = JobApplication.update(app, "20_Areas/Career/Job-Applications/acme/_app.md", status="acceptd")
    assert "error" in res
    assert "validation" in res["error"].lower()
    # And the vault write must NOT have happened
    assert app.updates == []


def test_update_rejects_unsettable_field():
    """Fields outside settable_fields() are refused."""
    app = FakeApp()
    app.notes["20_Areas/Career/Job-Applications/acme/_app.md"] = {"company": "Acme"}
    app.tags["20_Areas/Career/Job-Applications/acme/_app.md"] = ["job-application"]

    # Field that's not on the model at all (extra="allow" lets it through
    # on read, but settable_fields gates writes).
    res = JobApplication.update(app, "20_Areas/Career/Job-Applications/acme/_app.md", random_field="x")
    assert res == {"error": "field 'random_field' not settable"}
    assert app.updates == []


def test_update_writes_valid_partial():
    """Happy path — valid field, vault_update is called once."""
    app = FakeApp()
    app.notes["20_Areas/Career/Job-Applications/acme/_app.md"] = {"company": "Acme", "status": "applied"}
    app.tags["20_Areas/Career/Job-Applications/acme/_app.md"] = ["job-application"]

    res = JobApplication.update(app, "20_Areas/Career/Job-Applications/acme/_app.md", status="interview")
    assert res == {"ok": True}
    assert app.updates == [("20_Areas/Career/Job-Applications/acme/_app.md", {"status": "interview"})]


def test_extra_fields_preserved_through_roundtrip():
    """Unknown frontmatter (recruiter notes, source URL) must not be dropped."""
    app = FakeApp()
    app.notes["20_Areas/Career/Job-Applications/acme/_app.md"] = {
        "company": "Acme",
        "recruiter": "Jane Doe",      # not declared on the model
        "source_url": "https://...",  # ditto
    }
    app.tags["20_Areas/Career/Job-Applications/acme/_app.md"] = ["job-application"]

    inst = JobApplication.read(app, "20_Areas/Career/Job-Applications/acme/_app.md")
    fm = inst.to_frontmatter()
    assert fm["recruiter"] == "Jane Doe"
    assert fm["source_url"] == "https://..."


def test_date_coercion_handles_strings_and_objects():
    app = FakeApp()
    app.notes["20_Areas/Career/Job-Applications/a/_app.md"] = {"company": "A", "created": "2026-04-01"}
    app.tags["20_Areas/Career/Job-Applications/a/_app.md"] = ["job-application"]
    inst = JobApplication.read(app, "20_Areas/Career/Job-Applications/a/_app.md")
    assert inst.created == date(2026, 4, 1)


def test_read_returns_none_for_missing_path():
    app = FakeApp()
    assert JobApplication.read(app, "nope.md") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
