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
    bool_validator,
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


def test_update_writes_coerced_int_not_raw_string():
    """The write boundary normalizes: "90" lands as int 90, not the string."""
    app = FakeApp()
    p = "20_Areas/Career/Job-Applications/acme/_app.md"
    app.notes[p] = {"company": "Acme", "status": "applied"}
    app.tags[p] = ["job-application"]

    res = JobApplication.update(app, p, match_score="90")
    assert res == {"ok": True}
    # Coerced to int on write — NOT the raw "90" string the caller passed.
    assert app.updates == [(p, {"match_score": 90})]


def test_update_coerces_falsy_int_bool_on_write():
    """A boards cell edit of active=0 must land as False, not 0 (the people bug)."""

    class Widget(VaultModel):
        TAG = "widget"
        active: bool = True
        _coerce_active = bool_validator("active")

    app = FakeApp()
    p = "x/w.md"
    app.notes[p] = {"active": True}
    app.tags[p] = ["widget"]

    res = Widget.update(app, p, active=0)
    assert res == {"ok": True}
    assert app.updates == [(p, {"active": False})]


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


def test_str_field_coerces_loose_yaml_shapes():
    """A `str` field handed an empty list / None / scalar must not drop the note.

    YAML can return a string-typed field as `[]` (block-style with no items),
    `None`, or a bare number. Pydantic refuses list→str, so without the base
    coercion the whole note would fail validation and vanish from read_all.
    """

    class Followup(VaultModel):
        TAG = "job-application"
        company: str
        follow_up_due: str = ""   # ISO date string in practice
        note: str = ""

    app = FakeApp()
    # The exact shape from the reported bug: follow_up_due parsed as [].
    p = "20_Areas/Career/Job-Applications/bess/_application.md"
    app.notes[p] = {"company": "Renewables EPC", "follow_up_due": [], "note": None}
    app.tags[p] = ["job-application"]
    # And a scalar where a string is expected.
    q = "20_Areas/Career/Job-Applications/acme/_application.md"
    app.notes[q] = {"company": "Acme", "follow_up_due": 20260701}
    app.tags[q] = ["job-application"]

    by_company = {r.company: r for r in Followup.read_all(app)}
    assert set(by_company) == {"Renewables EPC", "Acme"}     # neither dropped
    assert by_company["Renewables EPC"].follow_up_due == ""  # [] → ""
    assert by_company["Renewables EPC"].note == ""           # None → ""
    assert by_company["Acme"].follow_up_due == "20260701"    # scalar → str


def test_audit_reports_invalid_without_mutating():
    """audit() names the bad notes + why, and never writes."""
    app = FakeApp()
    good = "20_Areas/Career/Job-Applications/acme/_app.md"
    bad = "20_Areas/Career/Job-Applications/broken/_app.md"
    app.notes[good] = {"company": "Acme", "match_score": "85"}
    app.tags[good] = ["job-application"]
    app.notes[bad] = {"role": "Engineer"}  # missing required `company`
    app.tags[bad] = ["job-application"]

    report = JobApplication.audit(app)
    assert report["total"] == 2
    assert report["valid"] == 1
    assert len(report["invalid"]) == 1
    assert report["invalid"][0]["path"] == bad
    assert report["invalid"][0]["error"]
    # Report-only — no writes happened.
    assert app.updates == []



def test_an_empty_list_field_does_not_drop_the_note():
    """A `list[str]` field must survive the empty shape *both* parsers produce.

    An empty `tags:` closes as `[]` when another key follows it and as `""`
    when it is the last key in the block — so the same note validated or
    vanished depending on the order of its frontmatter. Pydantic raises
    `list_type` on the string, `read_all` swallows the failure by design, and
    the note silently left the result set. Nothing pointed at the cause.

    The sibling of `test_str_field_coerces_loose_yaml_shapes`, read from the
    other direction.
    """

    class Tagged(VaultModel):
        TAG = "job-application"
        company: str
        tags: list[str] = []

    app = FakeApp()
    mid = "20_Areas/Career/Job-Applications/mid/_application.md"
    app.notes[mid] = {"company": "Mid", "tags": []}          # empty, key follows
    app.tags[mid] = ["job-application"]
    last = "20_Areas/Career/Job-Applications/last/_application.md"
    app.notes[last] = {"company": "Last", "tags": ""}        # empty, trailing key
    app.tags[last] = ["job-application"]
    none = "20_Areas/Career/Job-Applications/none/_application.md"
    app.notes[none] = {"company": "None", "tags": None}
    app.tags[none] = ["job-application"]
    real = "20_Areas/Career/Job-Applications/real/_application.md"
    app.notes[real] = {"company": "Real", "tags": ["bess", "hv"]}
    app.tags[real] = ["job-application"]

    by_company = {r.company: r for r in Tagged.read_all(app)}
    assert set(by_company) == {"Mid", "Last", "None", "Real"}   # none dropped
    assert by_company["Mid"].tags == []
    assert by_company["Last"].tags == []
    assert by_company["None"].tags == []
    assert by_company["Real"].tags == ["bess", "hv"]


def test_a_scalar_in_a_list_field_still_fails_loudly():
    """Only the *empty* shapes are widened. A real scalar must still fail.

    Inventing `["20260701"]` from a hand-typed `tags: 20260701` would hide a
    mis-authored note; `audit()` exists to name it instead.
    """

    class Tagged(VaultModel):
        TAG = "job-application"
        company: str
        tags: list[str] = []

    app = FakeApp()
    bad = "20_Areas/Career/Job-Applications/bad/_application.md"
    app.notes[bad] = {"company": "Bad", "tags": "20260701"}
    app.tags[bad] = ["job-application"]

    assert Tagged.read_all(app) == []                  # fail-soft skip
    report = Tagged.audit(app)                          # ...but named here
    assert report["valid"] == 0
    assert len(report["invalid"]) == 1
    assert report["invalid"][0]["path"] == bad


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
