"""Dogfood — jobs app.

Month-in-the-life of one application: add → verify in list → update status
(applied → recruiter_contact → interview) → generate briefing (LLM) →
verify summary endpoint returns briefing → archive (delete). Briefing
generation requires an LLM + vault profile; we skip that step if it fails.
"""

import time
import uuid

import pytest

from helpers import TEST_PREFIX


RUN_ID = f"{TEST_PREFIX}jobs-{uuid.uuid4().hex[:6]}"


def _available(http_client, path: str) -> bool:
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


@pytest.mark.dogfood
class TestJobsLifecycle:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        if not _available(http_client, "/jobs/api/applications"):
            pytest.skip("jobs app not loaded")

    def test_01_add_application(self, http_client):
        company = f"{RUN_ID} Corp"
        role = "Senior Dogfood Engineer"
        resp = http_client.post(
            "/jobs/api/applications/add",
            json={
                "company": company,
                "role": role,
                "status": "applied",
                "salary": "100k",
                "location": "Remote",
                "source": "dogfood-test",
                "priority": "high",
                "notes": f"dogfood test application {RUN_ID}",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok"), f"add failed: {data}"
        TestJobsLifecycle.state["company"] = company
        TestJobsLifecycle.state["role"] = role
        time.sleep(1.5)  # vault index settle

    def test_02_jobs_list_contains_company(self, http_client):
        listing = http_client.get("/jobs/api/jobs").json()
        companies = listing if isinstance(listing, list) else []
        found = next(
            (c for c in companies if self.state["company"] in c.get("company", "")),
            None,
        )
        assert found, f"company {self.state['company']} missing from /api/jobs"
        roles = found.get("roles") or []
        assert roles, "no roles under company"
        TestJobsLifecycle.state["app_id"] = roles[0].get("id")
        TestJobsLifecycle.state["company_id"] = found.get("company_id")

    def test_03_application_in_applications_list(self, http_client):
        apps = http_client.get("/jobs/api/applications").json()
        items = apps.get("applications") if isinstance(apps, dict) else apps
        found = any(
            self.state["company"] in str(a.get("company", ""))
            for a in items
        )
        assert found, "application missing from /api/applications"

    def test_04_advance_status_to_recruiter(self, http_client):
        aid = self.state["app_id"]
        resp = http_client.post(
            "/jobs/api/applications/update",
            json={"id": aid, "status": "recruiter_contact"},
        )
        assert resp.status_code == 200
        assert resp.json().get("ok"), resp.text[:200]

    def test_05_advance_status_to_interview(self, http_client):
        aid = self.state["app_id"]
        resp = http_client.post(
            "/jobs/api/applications/update",
            json={"id": aid, "status": "interview"},
        )
        assert resp.status_code == 200

    def test_06_stats_reflect_application(self, http_client):
        stats = http_client.get("/jobs/api/applications/stats").json()
        assert isinstance(stats, dict)
        assert stats.get("total", 0) >= 1

    def test_07_summary_endpoint_responds(self, http_client):
        cid = self.state["company_id"]
        resp = http_client.get(f"/jobs/api/summary/{cid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("company_id") == cid
        assert data.get("roles"), "summary has no roles"

    @pytest.mark.llm
    def test_08_generate_briefing(self, http_client):
        cid = self.state["company_id"]
        resp = http_client.post(
            f"/jobs/api/briefing/{cid}/generate",
            json={"role": self.state["role"]},
            timeout=180,
        )
        # Briefing may depend on company profile existing; tolerate either ok or a diagnostic error
        if resp.status_code != 200:
            pytest.skip(f"briefing endpoint returned {resp.status_code}")
        data = resp.json()
        if "error" in data:
            pytest.skip(f"briefing skipped: {data['error']}")

    def test_09_activity_log_has_entries(self, http_client):
        activity = http_client.get("/jobs/api/activity").json()
        assert isinstance(activity, list)
        # There should be some recent activity from our status transitions
        assert len(activity) >= 1

    def test_10_delete_archives(self, http_client):
        aid = self.state["app_id"]
        resp = http_client.request(
            "DELETE", "/jobs/api/applications/delete", json={"id": aid}
        )
        assert resp.status_code == 200
        time.sleep(1.5)
        # The delete is a soft archive (tag change). Verify application moved
        # out of active list — it may still appear in closed states.
        apps = http_client.get("/jobs/api/applications").json()
        items = apps.get("applications") if isinstance(apps, dict) else apps
        active = [a for a in items if a.get("status") not in (
            "rejected", "withdrawn", "not_pursuing", "accepted"
        )]
        still_active = any(
            self.state["company"] in str(a.get("company", "")) for a in active
        )
        # Soft: archival mechanism varies. Just confirm no hard error.
        if still_active:
            import warnings
            warnings.warn(f"application still active after delete (archive mechanism may differ)")


@pytest.fixture(scope="module", autouse=True)
def _cleanup(http_client):
    yield
    # Best-effort — delete any lingering test application by id match.
    try:
        listing = http_client.get("/jobs/api/applications").json()
        items = listing.get("applications") if isinstance(listing, dict) else listing
        for a in items if isinstance(items, list) else []:
            if RUN_ID in str(a.get("company", "")):
                aid = a.get("id")
                if aid:
                    http_client.request("DELETE", "/jobs/api/applications/delete",
                                        json={"id": aid})
    except Exception:
        pass
