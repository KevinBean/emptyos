"""Unit tests for KB reference-review freshness.

Pure in-process — no daemon, no kernel. Covers the three frontmatter fields on
``kind: reference`` notes (``effective_date`` / ``source_checked_at`` /
``review_due``), the ``stale_reference`` health bucket they feed, and the
coverage roll-up that reports the *unscheduled* references the health sweep
deliberately stays quiet about.

Run standalone (root conftest skips suites when :9000 is down):
    python -m pytest tests/test_unit_kb_reference_freshness.py --noconftest -v
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import types
from pathlib import Path

from helpers import app_path

_KB = app_path("kb")

# Register the package so the modules' `from .shared import ...` resolves.
_pkg = types.ModuleType("eos_kb_pkg")
_pkg.__path__ = [str(_KB)]
sys.modules["eos_kb_pkg"] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"eos_kb_pkg.{name}", str(_KB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"eos_kb_pkg.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


shared = _load("shared")
refcov = _load("reference_coverage")
notes_mod = _load("notes")

TODAY = dt.date(2026, 7, 25)


def _ref(slug, **props):
    """A minimally-shaped indexed reference note."""
    return {
        "path": f"30_Resources/EmptyOS/kb/notes/{slug}.md",
        "name": slug,
        "properties": {"kind": "reference", "standard_id": slug, "title": slug, **props},
    }


def _clause(slug, **props):
    """A minimally-shaped indexed clause note."""
    return {
        "path": f"30_Resources/EmptyOS/kb/sources/{slug}.md",
        "name": slug,
        "properties": {"kind": "clause", **props},
    }


class TestReferenceFreshness:
    def test_no_review_due_is_unscheduled_not_stale(self):
        """The default state of 98 real references. Calling these stale would
        fire on every healthy note and train the reader to ignore the bucket."""
        r = shared.reference_freshness({}, TODAY)
        assert r["state"] == "unscheduled"
        assert r["days_overdue"] is None

    def test_empty_string_review_due_is_unscheduled(self):
        assert shared.reference_freshness({"review_due": "   "}, TODAY)["state"] == "unscheduled"

    def test_past_due_is_overdue_with_day_count(self):
        r = shared.reference_freshness({"review_due": "2026-01-01"}, TODAY)
        assert r["state"] == "overdue"
        assert r["days_overdue"] == 205

    def test_due_today_is_scheduled_not_overdue(self):
        """Boundary: due *today* has not been missed yet."""
        r = shared.reference_freshness({"review_due": "2026-07-25"}, TODAY)
        assert r["state"] == "scheduled"
        assert r["days_overdue"] == 0

    def test_due_tomorrow_is_scheduled(self):
        assert shared.reference_freshness({"review_due": "2026-07-26"}, TODAY)["state"] == "scheduled"

    def test_unparseable_review_due_is_malformed_not_silently_dropped(self):
        """A note whose author typed `review_due: soon` made a commitment. A
        sweep that skips what it cannot parse is lying about its coverage."""
        r = shared.reference_freshness({"review_due": "soon"}, TODAY)
        assert r["state"] == "malformed"
        assert r["review_due"] == "soon"  # echoed back so the UI can show the bad value

    def test_impossible_date_is_malformed(self):
        assert shared.reference_freshness({"review_due": "2026-13-45"}, TODAY)["state"] == "malformed"

    def test_accepts_a_real_date_object(self):
        """A YAML parser may coerce the field before we see it."""
        r = shared.reference_freshness({"review_due": dt.date(2020, 1, 1)}, TODAY)
        assert r["state"] == "overdue"

    def test_other_fields_normalised_to_iso(self):
        r = shared.reference_freshness(
            {"source_checked_at": "2025-06-01", "effective_date": "2016-01-01"}, TODAY
        )
        assert r["source_checked_at"] == "2025-06-01"
        assert r["effective_date"] == "2016-01-01"

    def test_next_review_due_books_forward(self):
        assert shared.next_review_due(TODAY, 365) == dt.date(2027, 7, 25)

    def test_next_review_due_never_books_the_past(self):
        assert shared.next_review_due(TODAY, 0) > TODAY
        assert shared.next_review_due(TODAY, -50) > TODAY


class TestCoverageFreshnessRollup:
    def test_counts_each_state(self):
        out = refcov.build_reference_coverage(
            [
                _ref("as-2067", review_due="2026-01-01"),
                _ref("iec-60287", review_due="2027-01-01"),
                _ref("cigre-tb-880"),
                _ref("as-1170", review_due="whenever"),
            ],
            today=TODAY,
        )
        s = out["summary"]
        assert (s["review_overdue"], s["review_scheduled"]) == (1, 1)
        assert (s["review_unscheduled"], s["review_malformed"]) == (1, 1)

    def test_rows_carry_the_freshness_fields(self):
        out = refcov.build_reference_coverage(
            [_ref("as-2067", review_due="2026-01-01", source_checked_at="2025-01-01",
                  effective_date="2016-03-01")],
            today=TODAY,
        )
        row = out["references"][0]
        assert row["freshness"] == "overdue"
        assert row["days_overdue"] == 205
        assert row["source_checked_at"] == "2025-01-01"
        assert row["effective_date"] == "2016-03-01"

    def test_unscheduled_references_are_reported_here_not_as_health_findings(self):
        """The coverage report is where the gap is visible; health stays quiet."""
        out = refcov.build_reference_coverage([_ref("a"), _ref("b")], today=TODAY)
        assert out["summary"]["review_unscheduled"] == 2
        assert out["summary"]["review_overdue"] == 0


class _HealthApp:
    kernel = types.SimpleNamespace(
        config=types.SimpleNamespace(path=str(Path(__file__).parents[1] / "emptyos.toml"))
    )

    def _supersession_enabled(self):
        return False

    def _summarize(self, note):
        return notes_mod._summarize(self, note)

    @staticmethod
    def _parse_impl_ref(_ref):
        return "", "", ""


def _uncited_slugs(notes, cited_by=None):
    return {
        slug
        for category, slug, _record in notes_mod._iter_health_findings(
            _HealthApp(), notes, cited_by or {}, today=TODAY
        )
        if category == "uncited_references"
    }


class TestUncitedClauseHealth:
    """Composition ownership is a real KB connection, not an uncited warning."""

    def test_composed_clause_matched_by_standard_id_is_not_uncited(self):
        notes = [
            _ref("as-2067", standard_id="AS-2067", edition="2016"),
            _clause("as-2067-4", standard_id="AS-2067", edition="2016", clause="4"),
        ]
        assert _uncited_slugs(notes) == set()

    def test_legacy_composed_clause_matched_by_standard_name_is_not_uncited(self):
        notes = [
            _ref("iec-60287", standard_id="", standard="IEC 60287", edition="2023"),
            _clause(
                "iec-60287-1-1-2-1",
                standard="IEC 60287-1-1",
                edition="2023",
                clause="2.1",
            ),
        ]
        assert _uncited_slugs(notes) == set()

    def test_ownerless_clause_remains_actionable(self):
        clause = _clause("iec-60228-4", standard="IEC 60228", edition="2023", clause="4")
        assert _uncited_slugs([clause]) == {"iec-60228-4"}

    def test_edition_mismatch_does_not_hide_uncited_clause(self):
        notes = [
            _ref("example-2023", standard_id="", standard="Example", edition="2023"),
            _clause("example-2024-1", standard="Example", edition="2024", clause="1"),
        ]
        assert _uncited_slugs(notes) == {"example-2024-1"}

    def test_explicit_citation_still_suppresses_ownerless_clause(self):
        clause = _clause("standalone-1", standard="Standalone", clause="1")
        assert _uncited_slugs([clause], {"standalone-1": [{"slug": "formula"}]}) == set()


class _FakeRequest:
    def __init__(self, slug, body=None):
        self.path_params = {"slug": slug}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _FakeLock:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        self.log.append("lock")
        return self

    async def __aexit__(self, *a):
        self.log.append("unlock")
        return False


class _FakeKb:
    """Minimal stand-in for the bound KbApp — only what the endpoint touches."""

    def __init__(self, notes, config=None):
        self.notes = notes           # {slug: props}
        self.config = config or {}
        self.written = {}
        self.events = []
        self.calls = []

    async def note_path(self, id=""):
        return f"30_Resources/EmptyOS/kb/notes/{id}.md" if id in self.notes else ""

    def vault_get_properties(self, path):
        slug = path.rsplit("/", 1)[-1][:-3]
        return dict(self.notes.get(slug, {}))

    def app_config(self, key, default=None):
        return self.config.get(key, default)

    def note_lock(self, path):
        return _FakeLock(self.calls)

    def vault_update(self, path, props):
        self.calls.append("write")
        self.written[path] = props

    async def emit(self, name, payload):
        self.events.append((name, payload))


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class TestMarkReferenceChecked:
    """The only writer of the freshness fields. Without it the health bucket
    reads a field nothing sets and renders an empty list forever."""

    def _app(self, **notes):
        return _FakeKb(notes or {"as-2067": {"kind": "reference"}})

    def test_stamps_both_fields_and_books_forward(self):
        app = self._app()
        out = _run(refcov.api_mark_reference_checked(app, _FakeRequest("as-2067", {})))
        assert out["ok"] is True
        written = app.written["30_Resources/EmptyOS/kb/notes/as-2067.md"]
        assert written["source_checked_at"] == dt.date.today().isoformat()
        assert dt.date.fromisoformat(written["review_due"]) > dt.date.today()

    def test_default_interval_comes_from_config(self):
        app = _FakeKb({"as-2067": {"kind": "reference"}},
                      config={"reference_review_interval_days": 30})
        out = _run(refcov.api_mark_reference_checked(app, _FakeRequest("as-2067", {})))
        due = dt.date.fromisoformat(out["review_due"])
        assert (due - dt.date.today()).days == 30

    def test_explicit_interval_overrides_config(self):
        app = _FakeKb({"as-2067": {"kind": "reference"}},
                      config={"reference_review_interval_days": 30})
        out = _run(refcov.api_mark_reference_checked(app, _FakeRequest("as-2067", {"interval_days": 90})))
        assert (dt.date.fromisoformat(out["review_due"]) - dt.date.today()).days == 90

    def test_write_happens_under_the_note_lock(self):
        """Vault read-modify-write races — the write must be inside the lock."""
        app = self._app()
        _run(refcov.api_mark_reference_checked(app, _FakeRequest("as-2067", {})))
        assert app.calls == ["lock", "write", "unlock"]

    def test_unknown_slug_is_not_found(self):
        app = self._app()
        out = _run(refcov.api_mark_reference_checked(app, _FakeRequest("nope", {})))
        assert out == {"ok": False, "error": "not_found", "slug": "nope"}
        assert not app.written

    def test_refuses_a_non_reference_note(self):
        app = _FakeKb({"some-formula": {"kind": "formula"}})
        out = _run(refcov.api_mark_reference_checked(app, _FakeRequest("some-formula", {})))
        assert out["ok"] is False and out["error"] == "not_a_reference"
        assert not app.written

    def test_rejects_nonsense_interval(self):
        app = self._app()
        for bad in ({"interval_days": 0}, {"interval_days": -5}, {"interval_days": "soon"}):
            out = _run(refcov.api_mark_reference_checked(app, _FakeRequest("as-2067", bad)))
            assert out["ok"] is False and out["error"] == "bad_interval_days", bad
        assert not app.written

    def test_rejects_a_malformed_effective_date_rather_than_storing_it(self):
        """Storing an unparseable date would create the `malformed` state the
        health bucket then reports — the writer must not manufacture findings."""
        app = self._app()
        out = _run(refcov.api_mark_reference_checked(
            app, _FakeRequest("as-2067", {"effective_date": "sometime in 2016"})))
        assert out["ok"] is False and out["error"] == "bad_effective_date"
        assert not app.written

    def test_accepts_and_normalises_a_valid_effective_date(self):
        app = self._app()
        out = _run(refcov.api_mark_reference_checked(
            app, _FakeRequest("as-2067", {"effective_date": "2016-03-01"})))
        assert out["effective_date"] == "2016-03-01"

    def test_missing_body_is_tolerated(self):
        """A bare POST with no JSON body should still record a check."""
        app = self._app()
        out = _run(refcov.api_mark_reference_checked(app, _FakeRequest("as-2067", None)))
        assert out["ok"] is True

    def test_emits_an_event(self):
        app = self._app()
        _run(refcov.api_mark_reference_checked(app, _FakeRequest("as-2067", {})))
        assert app.events and app.events[0][0] == "kb:reference_checked"


class TestHealthBucketWiring:
    def test_stale_reference_is_a_declared_bucket(self):
        assert notes_mod._HEALTH_SEVERITY["stale_reference"] == "warn"

    def test_butler_severity_mirror_has_not_drifted(self):
        """kb-butler hand-copies the bucket map. A bucket added to kb and not
        mirrored there is silently ignored by every butler cycle."""
        butler_path = app_path("kb-butler") / "app.py"
        spec = importlib.util.spec_from_file_location("kb_butler_app_mirror", butler_path)
        butler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(butler)
        assert set(butler._BUCKET_SEVERITY) == set(notes_mod._HEALTH_SEVERITY)
        assert butler._BUCKET_SEVERITY == dict(notes_mod._HEALTH_SEVERITY)
