"""System tests: KB source documents — a client specification registered for one project.

  AC1 POST /kb/api/sources/ingest (dry_run) plans without writing
        → test_ingest_dry_run_plans
  AC2 POST /kb/api/sources/ingest (dry_run=0) writes reference + clause notes under the project
        → test_ingest_writes_project_scoped_notes
  AC3 GET /kb/api/sources?project= lists it as scope=project; bare list does not
        → test_sources_listing_scopes
  AC4 GET /kb/api/sources/{slug}/clauses returns ordered clauses with bodies
        → test_source_clauses_ordered_with_bodies
  AC5 resolve-reference resolves the client id INSIDE the project and nowhere else
        → test_resolve_inside_project_only
  AC6 default /kb/api/notes hides project-scoped notes; ?project= shows them
        → test_default_listing_hides_project_notes
Edge: test_ingest_twice_is_refused, test_ingest_requires_id_and_text
"""

import uuid

import pytest
from helpers import TEST_PREFIX, assert_dict_response

PROJECT = TEST_PREFIX + "kbsrc"
# A fresh document id per test run: the module fixture then always registers
# (never reuses a note written by an older build of this code), and the
# "twice is refused" case re-ingests this same id within the run.
DOC_ID = TEST_PREFIX + "SPEC-" + uuid.uuid4().hex[:6]
EDITION = "Rev A"

SPEC_TEXT = """\
<!-- Page 1 of 1 -->
1. Scope
1.1 General
This specification covers the design of the substation earthing system.

2. Requirements
2.1 Earthing design
The Contractor shall submit the earthing design for review before installation.
Touch voltages must not exceed the limits of AS 2067.

2.2 Cable installation
Cable trenches must be backfilled with thermally stabilised sand.
The Contractor may propose an alternative backfill.
"""


def _ingest(http_client, dry_run, **over):
    body = {"project": PROJECT, "standard_id": DOC_ID, "edition": EDITION,
            "text": SPEC_TEXT, "dry_run": "1" if dry_run else "0"}
    body.update(over)
    return http_client.post("/kb/api/sources/ingest", json=body)


@pytest.fixture(scope="module")
def registered(http_client):
    """The source, written once for the module under this run's fresh DOC_ID."""
    data = assert_dict_response(_ingest(http_client, dry_run=False))
    assert "error" not in data, data
    return data


@pytest.mark.api
class TestKBSourcesAPI:
    def test_ingest_dry_run_plans(self, http_client):
        # A fresh id every run: a dry run writes nothing, so it can never be
        # "already registered" and the plan is exercised on every daemon.
        import uuid
        fresh = f"{DOC_ID}-{uuid.uuid4().hex[:6]}"
        data = assert_dict_response(_ingest(http_client, dry_run=True, standard_id=fresh))
        assert "error" not in data, data
        assert data.get("dry_run") is True
        assert data.get("clause_count", 0) >= 3  # 1.1, 2.1, 2.2
        assert f"/{PROJECT}/docs/sources/" in data.get("reference_path", "")
        clauses = {c["clause"] for c in data.get("clauses", [])}
        assert {"1.1", "2.1", "2.2"} <= clauses
        # Nothing was written: the source does not appear afterwards.
        listed = assert_dict_response(http_client.get("/kb/api/sources", params={"project": PROJECT}))
        assert all(s["standard_id"] != fresh for s in listed["sources"])

    def test_title_differs_from_id_still_resolves(self, http_client):
        """Reference `standard` = the document id; the clauses must key on the
        same value even when the title is prose and the edition a year, or the
        citation index files them apart (found by the 2026-09-30 review)."""
        import uuid
        doc = f"{DOC_ID}-T{uuid.uuid4().hex[:5]}"
        reg = assert_dict_response(_ingest(http_client, dry_run=False, standard_id=doc, edition="2021",
                                           title="Substation Earthing Specification"))
        assert "error" not in reg, reg
        hit = assert_dict_response(http_client.post("/kb/api/resolve-reference",
                                                     json={"reference": f"{doc} 2021 cl 2.1", "project": PROJECT}))
        assert hit.get("matched") is True and hit.get("clause") == "2.1", hit

    def test_source_notes_are_not_kb_tagged(self, http_client, registered):
        """Client documents carry the source tag, never `kb`, so every global
        KB consumer is blind to them by construction."""
        # The project listing carries each note's tags (get_note's `properties`
        # strips them), so read the reference and one clause from there.
        scoped = assert_dict_response(http_client.get("/kb/api/notes", params={"project": PROJECT}))
        by_slug = {n["slug"]: n for n in scoped["notes"]}
        assert registered["slug"] in by_slug, sorted(by_slug)
        tags = [str(t) for t in (by_slug[registered["slug"]].get("tags") or [])]
        assert "kb-source" in tags and "kb" not in tags, tags
        clauses = assert_dict_response(http_client.get(f"/kb/api/sources/{registered['slug']}/clauses"))
        assert clauses["count"] >= 3
        cl_slug = clauses["clauses"][0]["slug"]
        assert cl_slug in by_slug, cl_slug
        cl_tags = [str(t) for t in (by_slug[cl_slug].get("tags") or [])]
        assert "kb-source" in cl_tags and "kb" not in cl_tags, cl_tags
        # And a slug-addressed open still works for a client clause.
        assert "error" not in assert_dict_response(http_client.get(f"/kb/api/notes/{cl_slug}"))

    def test_ingest_writes_project_scoped_notes(self, http_client, registered):
        slug = registered["slug"]
        detail = assert_dict_response(http_client.get(f"/kb/api/notes/{slug}"))
        assert "error" not in detail, detail
        props = detail.get("properties") or detail.get("data", {}).get("properties") or {}
        assert str(props.get("project")) == PROJECT
        assert props.get("kind") == "reference"

    def test_sources_listing_scopes(self, http_client, registered):
        scoped = assert_dict_response(http_client.get("/kb/api/sources", params={"project": PROJECT}))
        mine = [s for s in scoped["sources"] if s["slug"] == registered["slug"]]
        assert mine and mine[0]["scope"] == "project" and mine[0]["clause_count"] >= 3
        bare = assert_dict_response(http_client.get("/kb/api/sources"))
        assert all(s["slug"] != registered["slug"] for s in bare["sources"])

    def test_source_clauses_ordered_with_bodies(self, http_client, registered):
        data = assert_dict_response(http_client.get(f"/kb/api/sources/{registered['slug']}/clauses"))
        nums = [c["clause"] for c in data["clauses"]]
        assert nums == sorted(nums, key=lambda s: [int(x) for x in s.split(".")])
        by = {c["clause"]: c for c in data["clauses"]}
        assert "shall submit the earthing design" in by["2.1"]["body"]
        assert by["2.1"]["project"] == PROJECT

    def test_resolve_inside_project_only(self, http_client, registered):
        cite = f"{DOC_ID} {EDITION} cl 2.1"
        inside = assert_dict_response(http_client.post("/kb/api/resolve-reference",
                                                        json={"reference": cite, "project": PROJECT}))
        assert inside.get("matched") is True and inside.get("scope") == "project"
        assert inside.get("clause") == "2.1"
        outside = assert_dict_response(http_client.post("/kb/api/resolve-reference", json={"reference": cite}))
        assert outside.get("matched") is False
        other = assert_dict_response(http_client.post("/kb/api/resolve-reference",
                                                       json={"reference": cite, "project": PROJECT + "-other"}))
        assert other.get("matched") is False

    def test_default_listing_hides_project_notes(self, http_client, registered):
        default = assert_dict_response(http_client.get("/kb/api/notes"))
        assert all(n.get("project", "") == "" for n in default["notes"])
        assert all(n["slug"] != registered["slug"] for n in default["notes"])
        scoped = assert_dict_response(http_client.get("/kb/api/notes", params={"project": PROJECT}))
        assert any(n["slug"] == registered["slug"] for n in scoped["notes"])
        assert all(n.get("project") == PROJECT for n in scoped["notes"])

    def test_reference_without_id_claims_no_clauses(self, http_client):
        """A reference with no standard_id must report 0 clauses — an empty join
        key would otherwise match every clause note that also lacks one (624 on
        the real vault, 2026-10-01). Needs at least one id-less reference to
        prove anything; a vault without one SKIPS loudly rather than passing."""
        data = assert_dict_response(http_client.get("/kb/api/sources"))
        idless = [s for s in data["sources"] if not s["standard_id"]]
        if not idless:
            pytest.skip("no id-less reference in this vault — the empty-key join cannot be exercised")
        assert all(s["clause_count"] == 0 for s in idless), [s["slug"] for s in idless if s["clause_count"]][:5]
        res = assert_dict_response(http_client.get(f"/kb/api/sources/{idless[0]['slug']}/clauses"))
        assert "error" in res and "standard_id" in res["error"]

    def test_ingest_twice_is_refused(self, http_client, registered):
        again = assert_dict_response(_ingest(http_client, dry_run=False))
        assert "error" in again and again.get("slug") == registered["slug"]

    def test_ingest_requires_id_and_text(self, http_client):
        assert "error" in assert_dict_response(_ingest(http_client, dry_run=True, standard_id=""))
        assert "error" in assert_dict_response(_ingest(http_client, dry_run=True, text=""))
        assert "error" in assert_dict_response(_ingest(http_client, dry_run=True, project=""))
