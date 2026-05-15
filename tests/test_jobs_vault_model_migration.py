"""Migration verification: jobs/_to_app preserves dict shape via VaultModel.

Pure in-process — no daemon, no real vault. Pins the behavior that the
JobApplication VaultModel migration must NOT change:

  - Same dict keys as before
  - String-typed match_score still coerced to int
  - Legacy Application_Sent/Active/Application booleans still map to status
  - Folder-name fallback for company still works
  - Records with unknown status values still surface (don't get dropped)
  - id derivation (company_id--role_slug) still works
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from apps.personal.jobs.app import JobApplication, JobsApp


def _mk_app(tmp_path: Path) -> JobsApp:
    """Build a JobsApp without booting the kernel."""
    config = MagicMock()
    config.notes_path = tmp_path
    config.data_dir = tmp_path / "data"
    services = MagicMock()
    services.get_optional.return_value = None
    kernel = SimpleNamespace(config=config, services=services, vault_map=MagicMock())
    manifest = SimpleNamespace(id="jobs")
    app = JobsApp.__new__(JobsApp)
    app.kernel = kernel
    app.manifest = manifest
    app._activity = []
    app._app_cache = None
    app._cache_ts = 0
    return app


def _row(path: str, company: str, **props) -> dict:
    """Build a fake VaultIndex result row."""
    folder = path.rsplit("/", 1)[0]
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return {
        "path": path,
        "name": name,
        "folder": folder,
        "properties": {"company": company, **props},
        "tags": ["job-application"],
    }


def test_to_app_dict_shape_unchanged(tmp_path):
    """The dict shape callers depend on must not regress."""
    app = _mk_app(tmp_path)
    result = app._to_app(_row(
        "20_Areas/Career/Job-Applications/acme/_application-engineer.md",
        company="Acme",
        role="Engineer",
        status="interview",
        match_score="85",
    ))
    assert result is not None
    # Old keys
    expected_keys = {
        "id", "company_id", "company", "role", "source", "recruiter",
        "location", "salary", "status", "match_score", "priority", "notes",
        "created", "updated", "history", "_vault_path",
    }
    assert set(result.keys()) == expected_keys


def test_to_app_coerces_string_match_score(tmp_path):
    """YAML returns numbers as strings; old code did try/except int."""
    app = _mk_app(tmp_path)
    result = app._to_app(_row(
        "20_Areas/Career/Job-Applications/acme/_application-eng.md",
        company="Acme", role="Eng", match_score="85",
    ))
    assert result["match_score"] == 85
    assert isinstance(result["match_score"], int)


def test_to_app_legacy_application_sent_maps_to_interview(tmp_path):
    """Old vault notes used boolean Application_Sent flag pre-status enum."""
    app = _mk_app(tmp_path)
    result = app._to_app(_row(
        "20_Areas/Career/Job-Applications/old/_application.md",
        company="OldCo", Application_Sent="true",
    ))
    assert result["status"] == "interview"


def test_to_app_legacy_active_false_maps_to_rejected(tmp_path):
    app = _mk_app(tmp_path)
    result = app._to_app(_row(
        "20_Areas/Career/Job-Applications/old/_application.md",
        company="OldCo", Active="false",
    ))
    assert result["status"] == "rejected"


def test_to_app_folder_fallback_when_company_missing(tmp_path):
    """Notes without a `company` frontmatter field should fall back to folder name."""
    app = _mk_app(tmp_path)
    result = app._to_app({
        "path": "20_Areas/Career/Job-Applications/acme-corp/_application-eng.md",
        "name": "_application-eng",
        "folder": "20_Areas/Career/Job-Applications/acme-corp",
        "properties": {"role": "Engineer"},  # no `company` key
        "tags": ["job-application"],
    })
    assert result is not None
    assert result["company"] == "Acme Corp"


def test_to_app_preserves_unknown_status_value(tmp_path):
    """Stricter enum would silently drop notes with custom status — must not."""
    app = _mk_app(tmp_path)
    result = app._to_app(_row(
        "20_Areas/Career/Job-Applications/acme/_application-eng.md",
        company="Acme", role="Eng", status="custom-value",
    ))
    assert result is not None
    assert result["status"] == "custom-value"


def test_to_app_id_derivation(tmp_path):
    """id = company_id--role_slug when role differs from company name."""
    app = _mk_app(tmp_path)
    result = app._to_app(_row(
        "20_Areas/Career/Job-Applications/acme/_application-senior-engineer.md",
        company="Acme", role="Senior Engineer",
    ))
    assert result["company_id"] == "acme"
    # role_slug from filename "_application-senior-engineer" → "senior-engineer"
    assert result["id"] == "acme--senior-engineer"


def test_settable_fields_derived_from_model():
    """Boards integration whitelist must match the VaultModel-declared set."""
    assert JobsApp.SETTABLE_FIELDS == JobApplication.settable_fields()
    # And it covers exactly the boards-edit fields
    assert JobsApp.SETTABLE_FIELDS == {
        "status", "salary", "match_score", "priority",
        "recruiter", "source", "location",
    }


def test_job_application_validates_status_on_update(tmp_path):
    """Direct model use: update() must accept valid status values."""

    class FakeApp:
        def __init__(self):
            self.notes = {"20_Areas/Career/Job-Applications/acme/_app.md":
                          {"company": "Acme", "status": "applied"}}
            self.updates = []

        def vault_get_properties(self, path):
            return dict(self.notes.get(path, {}))

        def vault_update(self, path, props):
            self.updates.append((path, dict(props)))
            self.notes.setdefault(path, {}).update(props)

    fa = FakeApp()
    # Off-whitelist field
    res = JobApplication.update(fa, "20_Areas/Career/Job-Applications/acme/_app.md",
                                company="Different")
    assert "error" in res
    assert "not settable" in res["error"]
    assert fa.updates == []

    # Valid field
    res = JobApplication.update(fa, "20_Areas/Career/Job-Applications/acme/_app.md",
                                status="interview")
    assert res == {"ok": True}
    assert fa.updates == [("20_Areas/Career/Job-Applications/acme/_app.md",
                          {"status": "interview"})]
