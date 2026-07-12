"""System app tests: Vlog — daily video diary.

Smoke-level coverage of the read/validate surface against a stock :9000. The
capture → ffmpeg-thumbnail → listen() transcribe → montage round-trip needs a
real video clip + a listen provider, so it's verified on a sandbox-pool member
(see the plan's verification section / .claude/rules/sandbox-driven-testing.md).
Tests skip cleanly when the app isn't installed on the target daemon (new apps
need a Store install).
"""

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly

PREFIX = "/vlog"


def _installed(http_client) -> bool:
    return http_client.get(PREFIX + "/api/days").status_code == 200


@pytest.mark.api
class TestVlogAPI:
    def test_days_envelope(self, http_client):
        if not _installed(http_client):
            pytest.skip("vlog not installed on this daemon")
        data = assert_dict_response(http_client.get(PREFIX + "/api/days"))
        assert "days" in data and "heatmap" in data, f"days envelope: {list(data.keys())}"
        assert isinstance(data["days"], list)
        assert isinstance(data["heatmap"], dict)

    def test_day_bad_date_rejected(self, http_client):
        if not _installed(http_client):
            pytest.skip("vlog not installed")
        data = http_client.get(PREFIX + "/api/day/not-a-date").json()
        assert data.get("error") == "bad date", f"bad date should be rejected: {data}"

    def test_day_well_formed_returns_shape(self, http_client):
        if not _installed(http_client):
            pytest.skip("vlog not installed")
        data = assert_dict_response(http_client.get(PREFIX + "/api/day/2000-01-01"))
        # A day with no clips still returns the empty shape, not an error.
        for key in ("date", "clips", "transcript", "_vault_path"):
            assert key in data, f"day shape missing {key}: {list(data.keys())}"
        assert data["clips"] == [] and data["exists"] is False

    def test_clip_missing_is_404(self, http_client):
        if not _installed(http_client):
            pytest.skip("vlog not installed")
        r = http_client.get(PREFIX + "/api/clip/2000-01-01/0")
        assert r.status_code == 404

    def test_clip_traversal_guard(self, http_client):
        if not _installed(http_client):
            pytest.skip("vlog not installed")
        # Non-date path param must be rejected before any filesystem access.
        r = http_client.get(PREFIX + "/api/clip/..%2f..%2fetc/0")
        assert r.status_code in (400, 404)

    def test_update_day_bad_date(self, http_client):
        if not _installed(http_client):
            pytest.skip("vlog not installed")
        data = http_client.post(PREFIX + "/api/day/nope", json={"mood": "good"}).json()
        assert data.get("error") == "bad date", f"update bad date: {data}"

    def test_compile_no_clips_in_range(self, http_client):
        if not _installed(http_client):
            pytest.skip("vlog not installed")
        # 'week' on a fresh/clip-less daemon has nothing to stitch.
        data = http_client.post(PREFIX + "/api/compile", json={"period": "week"}).json()
        assert ("error" in data) or (data.get("status") == "running"), f"compile: {data}"

    def test_compile_status_unknown_run(self, http_client):
        if not _installed(http_client):
            pytest.skip("vlog not installed")
        data = http_client.get(PREFIX + "/api/compile/PLAYWRIGHT-TEST-nope").json()
        assert data.get("error") == "unknown run", f"unknown run: {data}"

    def test_search_empty_query(self, http_client):
        if not _installed(http_client):
            pytest.skip("vlog not installed")
        data = assert_dict_response(http_client.get(PREFIX + "/api/search?q="))
        assert data.get("results") == [], f"empty query should return no results: {data}"

    def test_search_envelope(self, http_client):
        if not _installed(http_client):
            pytest.skip("vlog not installed")
        data = assert_dict_response(http_client.get(PREFIX + "/api/search?q=PLAYWRIGHT-TEST-zzz"))
        assert isinstance(data.get("results"), list), f"search envelope: {data}"
        assert data.get("count") == 0


@pytest.mark.interactive
class TestVlogUI:
    def test_ui_page_loads(self, page, base_url, page_errors):
        resp = page.goto(base_url + PREFIX + "/", wait_until="domcontentloaded", timeout=15000)
        if resp.status == 404:
            pytest.skip("vlog not installed")
        assert resp.status == 200
        wait_briefly(page, 1200)
        # getUserMedia / fetch warnings are environmental, not app bugs.
        assert_no_js_errors(page_errors, allow_patterns=["fetch", "getUserMedia", "media"])

    def test_ui_has_capture_controls(self, page, base_url, page_errors):
        resp = page.goto(base_url + PREFIX + "/", wait_until="domcontentloaded", timeout=15000)
        if resp.status == 404:
            pytest.skip("vlog not installed")
        wait_briefly(page, 1000)
        assert page.locator("#record-btn").count() == 1
        assert page.locator("#heatmap").count() == 1
