"""System app tests: Worklog Capture — capture-source review queue contract.

Contract-only against a live :9000 (no live LLM, no real screen grab). The full
capture→OCR→correlate→draft→Apply round-trip needs a screen + local think and is
verified by hand / on a sandbox member (see the plan's verification section).
Skips cleanly when the app isn't installed (new apps need a Store install).
"""
import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors

PREFIX = "/worklog-capture"


def _installed(http_client) -> bool:
    return http_client.get(PREFIX + "/api/config").status_code == 200


@pytest.mark.api
class TestWorklogCaptureAPI:
    def test_config_envelope(self, http_client):
        if not _installed(http_client):
            pytest.skip("worklog-capture not installed on this daemon")
        data = assert_dict_response(http_client.get(PREFIX + "/api/config"))
        for k in ("watch_dir", "ocr_backend", "counts", "group_window_min"):
            assert k in data, f"config missing {k!r}: {list(data.keys())}"

    def test_queue_shape(self, http_client):
        if not _installed(http_client):
            pytest.skip("worklog-capture not installed")
        data = assert_dict_response(http_client.get(PREFIX + "/api/queue"))
        assert isinstance(data.get("flagged"), list)
        assert isinstance(data.get("unflagged"), list)

    def test_apply_unknown_id_clean_error(self, http_client):
        if not _installed(http_client):
            pytest.skip("worklog-capture not installed")
        data = http_client.post(PREFIX + "/api/apply/cap-does-not-exist",
                                json={"text": "x"}).json()
        assert data.get("error"), f"expected error for unknown id, got {data}"

    def test_dismiss_unknown_id_clean_error(self, http_client):
        if not _installed(http_client):
            pytest.skip("worklog-capture not installed")
        data = http_client.post(PREFIX + "/api/dismiss/cap-does-not-exist", json={}).json()
        assert data.get("error"), f"expected error, got {data}"

    # NOTE: no /api/digest test here — a digest reads the real session + browser
    # trail (heavy, and touches the user's actual history), so it's exercised by
    # hand / on a sandbox member, not in a smoke run. The offline pipeline test in
    # tests/test_unit_worklog_capture.py covers the digest logic end to end.


@pytest.mark.interactive
class TestWorklogCaptureUI:
    def test_page_loads(self, page, page_errors, base_url, http_client):
        if not _installed(http_client):
            pytest.skip("worklog-capture not installed")
        page.goto(base_url + PREFIX + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#queue", timeout=8000)
        assert_no_js_errors(page_errors)

    def test_cards_render(self, page, page_errors, base_url, http_client):
        """Render a card from injected STATE.

        An empty queue never calls card(), so a page-loads check passes while the
        review UI is dead — that is exactly how a missing `escAttr` global (from
        an unloaded /static/eos.js) shipped. Drive render() with real card shapes
        so the surface the user actually reviews on is the thing under test.
        """
        if not _installed(http_client):
            pytest.skip("worklog-capture not installed")
        page.goto(base_url + PREFIX + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#queue", timeout=8000)
        page.evaluate("""() => {
            const draft = {text: 'wrote the thing', project: 'General',
                           status: 'in-progress', date: '2026-01-01',
                           employer: '', confidence: 'high'};
            STATE.flagged = [{id: 'cap-test-1', source: 'ui', created: '2026-01-01T09:00:00',
                              app_name: 'Editor', has_image: false, member_count: 0,
                              evidence: [{summary: '3 visit(s) to example.com'}],
                              ocr_excerpt: 'on-screen text', draft: draft}];
            STATE.unflagged = [{id: 'cap-test-2', source: 'trail', created: '2026-01-01T10:00:00',
                                evidence_only: true, has_image: false, member_count: 0,
                                evidence: [], ocr_excerpt: '', draft: draft}];
            STATE.failed = [{id: 'cap-test-3', source: 'folder', created: '2026-01-01T11:00:00',
                             has_image: false, evidence: [], ocr_excerpt: '',
                             draft: {}, error: 'model unreachable', attempts: 1}];
            render();
        }""")
        assert page.locator('.wc-card[data-id="cap-test-1"]').count() == 1
        assert page.locator('.wc-card[data-id="cap-test-3"]').count() == 1, "failed captures must stay visible"
        assert_no_js_errors(page_errors)
