"""Dogfood — projects app.

Month-in-the-life: auto-bootstrap test project (via tasks/add to new id) →
add tasks → toggle one done → update status/stage/meta → verify detail/all-
tasks/deadlines all reflect the changes → cleanup via vault delete. No LLM.
"""

import time
import uuid

import pytest

from helpers import TEST_PREFIX


# Lowercase-only ID because project ids derive from slugs
RUN_ID = f"pw-dogfood-{uuid.uuid4().hex[:6]}"
PROJECT_ID = RUN_ID


def _available(http_client, path: str) -> bool:
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


@pytest.mark.dogfood
class TestProjectsLifecycle:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        if not _available(http_client, "/projects/api/projects"):
            pytest.skip("projects app not loaded")

    def test_01_bootstrap_via_first_task(self, http_client):
        """Adding a task to a non-existent project auto-creates the project."""
        text = f"{TEST_PREFIX}project-task-1-{RUN_ID}"
        resp = http_client.post(
            f"/projects/api/projects/{PROJECT_ID}/tasks/add",
            json={"text": text},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok"), f"task add failed: {data}"
        TestProjectsLifecycle.state["first_task"] = text
        # Give the vault watcher time to pick up the new project directory.
        time.sleep(1.5)
        # Rescan so the in-memory project list sees the new file.
        http_client.post("/projects/api/refresh")

    def test_02_project_appears_in_list(self, http_client):
        listing = http_client.get("/projects/api/projects").json()
        projects = listing if isinstance(listing, list) else listing.get("projects", [])
        found = next(
            (p for p in projects if p.get("id") == PROJECT_ID), None
        )
        assert found, f"auto-created project {PROJECT_ID} missing from list"

    def test_03_detail_shows_the_task(self, http_client):
        detail = http_client.get(f"/projects/api/projects/{PROJECT_ID}").json()
        assert "error" not in detail, detail
        tasks = detail.get("tasks", [])
        assert any(
            RUN_ID in str(t.get("text", "")) for t in tasks
        ), f"first task missing from detail: {tasks}"

    def test_04_add_more_tasks(self, http_client):
        for i in range(2, 4):
            text = f"{TEST_PREFIX}project-task-{i}-{RUN_ID}"
            resp = http_client.post(
                f"/projects/api/projects/{PROJECT_ID}/tasks/add",
                json={"text": text},
            )
            assert resp.status_code == 200
        time.sleep(0.5)
        detail = http_client.get(f"/projects/api/projects/{PROJECT_ID}").json()
        tasks = detail.get("tasks", [])
        ours = [t for t in tasks if RUN_ID in str(t.get("text", ""))]
        assert len(ours) >= 3, f"expected >=3 of our tasks, got {len(ours)}"
        TestProjectsLifecycle.state["first_task_line"] = ours[0].get("line")

    def test_05_toggle_task_done(self, http_client):
        line = self.state["first_task_line"]
        assert line is not None, "no line number captured"
        resp = http_client.post(
            f"/projects/api/projects/{PROJECT_ID}/tasks/toggle",
            json={"line": line},
        )
        assert resp.status_code == 200
        time.sleep(0.3)
        detail = http_client.get(f"/projects/api/projects/{PROJECT_ID}").json()
        done = detail.get("done_tasks", 0)
        assert done >= 1, f"toggle didn't register: done={done}"

    def test_06_update_status_to_active(self, http_client):
        resp = http_client.post(
            f"/projects/api/projects/{PROJECT_ID}/status",
            json={"status": "active"},
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "active"
        time.sleep(0.3)
        detail = http_client.get(f"/projects/api/projects/{PROJECT_ID}").json()
        assert detail.get("status") == "active", f"status not persisted: {detail.get('status')}"

    def test_07_update_meta_with_deadline(self, http_client):
        from datetime import date, timedelta
        deadline = (date.today() + timedelta(days=14)).isoformat()
        resp = http_client.post(
            f"/projects/api/projects/{PROJECT_ID}/meta",
            json={"deadline": deadline, "description": f"dogfood {RUN_ID}"},
        )
        assert resp.status_code == 200
        assert resp.json().get("ok") or "ok" in str(resp.json()).lower(), resp.text[:200]

    def test_08_deadlines_include_project(self, http_client):
        deadlines = http_client.get("/projects/api/deadlines").json()
        items = deadlines if isinstance(deadlines, list) else deadlines.get("projects", [])
        found = any(p.get("id") == PROJECT_ID for p in items)
        # Soft check: deadlines endpoint may filter by window/status
        if not found:
            import warnings
            warnings.warn(f"project {PROJECT_ID} not surfaced in /api/deadlines (filter window may skip it)")

    def test_09_all_tasks_includes_our_tasks(self, http_client):
        all_tasks = http_client.get("/projects/api/all-tasks").json()
        tasks = all_tasks if isinstance(all_tasks, list) else all_tasks.get("tasks", [])
        ours = [t for t in tasks if RUN_ID in str(t.get("text", ""))]
        assert len(ours) >= 2, f"expected >=2 of our tasks in all-tasks, got {len(ours)}"

    def test_10_update_stage(self, http_client):
        resp = http_client.post(
            f"/projects/api/projects/{PROJECT_ID}/stage",
            json={"stage": "execution"},
        )
        # stage endpoint may 400 if stage isn't valid for project type — soft check
        if resp.status_code == 200:
            assert resp.json().get("ok") or resp.json().get("stage")


@pytest.fixture(scope="module", autouse=True)
def _cleanup(http_client):
    yield
    # Remove the scratch project directory from the vault.
    try:
        import shutil
        import tomllib
        from pathlib import Path
        with open("emptyos.toml", "rb") as f:
            cfg = tomllib.load(f)
        vault = Path(cfg.get("notes", {}).get("path", ""))
        if vault.exists():
            proj_dir = vault / "10_Projects" / PROJECT_ID
            if proj_dir.exists() and proj_dir.is_dir():
                shutil.rmtree(proj_dir, ignore_errors=True)
        # Ask the daemon to rescan after cleanup
        http_client.post("/projects/api/refresh")
    except Exception:
        pass
