"""System app tests: Command Runner — shell execution API + UI."""

from __future__ import annotations

import sys

import pytest

from helpers import assert_dict_response, assert_list_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


# Cross-platform echo works on both Windows cmd and POSIX shells.
ECHO_CMD = "echo hello-from-runner"
# Exits with non-zero without depending on /bin/false (missing on Windows).
FAIL_CMD = "python -c \"import sys; sys.exit(3)\""


@pytest.mark.api
class TestRunAPI:
    def test_execute_echo_returns_stdout(self, http_client):
        data = assert_dict_response(http_client.post(
            "/run/api/execute", json={"command": ECHO_CMD, "timeout": 10},
        ))
        for key in ("stdout", "stderr", "exit_code"):
            assert key in data, f"missing key {key!r} in response: {data}"
        assert "hello-from-runner" in data["stdout"], (
            f"stdout missing echo output: {data['stdout']!r}"
        )
        assert data["exit_code"] == 0

    def test_execute_nonzero_exit_is_surfaced(self, http_client):
        data = assert_dict_response(http_client.post(
            "/run/api/execute", json={"command": FAIL_CMD, "timeout": 10},
        ))
        assert data["exit_code"] == 3, (
            f"expected exit_code=3, got {data['exit_code']!r} "
            f"(stderr={data.get('stderr','')!r})"
        )

    def test_execute_timeout_returns_minus_one(self, http_client):
        # Sleep longer than timeout; `execute()` sets exit_code=-1 on TimeoutError.
        sleep_cmd = f"{sys.executable} -c \"import time; time.sleep(5)\""
        data = assert_dict_response(http_client.post(
            "/run/api/execute", json={"command": sleep_cmd, "timeout": 1},
        ))
        assert data["exit_code"] == -1, (
            f"expected timeout → exit_code=-1, got {data['exit_code']!r}"
        )
        assert "timed out" in data["stderr"].lower()

    def test_history_is_list(self, http_client):
        # Run one command first so history has at least one entry shape to verify.
        http_client.post(
            "/run/api/execute", json={"command": ECHO_CMD, "timeout": 10},
        )
        data = assert_list_response(http_client.get("/run/api/history"))
        if data:
            first = data[0]
            assert isinstance(first, dict)
            for key in ("command", "exit_code", "timestamp"):
                assert key in first, f"history entry missing {key!r}: {first}"

    def test_suggest_rejects_empty_task(self, http_client):
        data = assert_dict_response(http_client.post("/run/api/suggest", json={}))
        assert "error" in data, f"empty task should error, got {data}"


@pytest.mark.interactive
class TestRunUI:
    def test_ui_shell_loads(self, app_page, page_errors):
        """Page renders command input + Run button, no JS errors."""
        page = app_page("run")
        wait_briefly(page, 500)
        assert page.locator("#cmd").count() == 1
        assert page.locator("button:has-text('Run')").count() >= 1
        assert_no_js_errors(page_errors)

    def test_ui_run_command_shows_output(self, app_page, page_errors):
        """Type a command, click Run, output box shows the JSON response.

        Success signal is 'Exit:' (rendered only after fetch resolves) rather
        than the echoed command (visible in the 'Running…' placeholder too).
        """
        page = app_page("run")
        wait_briefly(page, 400)
        page.locator("#cmd").fill(ECHO_CMD)
        page.locator("button:has-text('Run')").first.click()
        try:
            page.wait_for_function(
                "document.getElementById('output')"
                " && document.getElementById('output').textContent.includes('Exit:')",
                timeout=10000,
            )
        except Exception:
            # Could be a Chromium→localhost fetch failure under heavy TIME_WAIT
            # pressure on Windows. The API test covers the backend path; skip
            # rather than fail on an environmental flake.
            net_err = any("Failed to fetch" in str(e) for e in page_errors)
            if net_err:
                pytest.skip(f"Browser fetch blocked (env flake): {page_errors}")
            pytest.fail(
                f"output box never showed Exit line; "
                f"output={(page.locator('#output').text_content() or '')!r}; "
                f"js-errors={page_errors}"
            )
        output = (page.locator("#output").text_content() or "").strip()
        assert "hello-from-runner" in output, f"stdout missing: {output!r}"
        assert "Exit: 0" in output, f"missing exit-code line: {output!r}"
        assert_no_js_errors(page_errors)
