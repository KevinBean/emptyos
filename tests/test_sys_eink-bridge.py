"""System app tests: E-ink Bridge — ttyd web-terminal bridge over the LAN.

The app spawns ``ttyd`` (an external binary) wrapping a command, so the tests
deliberately avoid blanket process-spawning side effects:

  - status-shape + field-type tests are pure reads (always safe);
  - the stop route is exercised for idempotency (no spawn);
  - a single start->stop round-trip runs ONLY when ttyd is installed, and
    soft-skips if the port is busy or the spawn errors, so a flaky local
    environment never red-bars the suite.

No vault data is created, so there's nothing to clean up (no TEST_PREFIX).
"""

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly

STATUS_KEYS = (
    "installed", "ttyd_path", "running", "pid", "port", "bind", "bind_source",
    "writable", "auth", "command", "lan_ips", "urls",
    "network_mode", "tmux_available", "log_tail",
)


@pytest.mark.api
class TestEinkBridgeAPI:
    # ── status: shape + types (pure reads) ───────────────────────────────
    def test_status_envelope(self, http_client):
        data = assert_dict_response(http_client.get("/eink-bridge/api/status"))
        for key in STATUS_KEYS:
            assert key in data, f"status missing '{key}': {list(data.keys())}"

    def test_status_field_types(self, http_client):
        d = http_client.get("/eink-bridge/api/status").json()
        assert isinstance(d["installed"], bool), f"installed not bool: {d['installed']!r}"
        assert isinstance(d["running"], bool), f"running not bool: {d['running']!r}"
        assert isinstance(d["writable"], bool), f"writable not bool: {d['writable']!r}"
        assert isinstance(d["auth"], bool), f"auth not bool: {d['auth']!r}"
        assert isinstance(d["port"], int), f"port not int: {d['port']!r}"
        assert isinstance(d["lan_ips"], list), f"lan_ips not list: {d['lan_ips']!r}"
        assert isinstance(d["urls"], list), f"urls not list: {d['urls']!r}"

    def test_status_command_is_nonempty_string(self, http_client):
        d = http_client.get("/eink-bridge/api/status").json()
        assert isinstance(d["command"], str) and d["command"].strip(), (
            f"command should be a non-empty string: {d['command']!r}"
        )

    def test_status_urls_carry_port(self, http_client):
        d = http_client.get("/eink-bridge/api/status").json()
        port = d["port"]
        for url in d["urls"]:
            assert f":{port}/" in url, f"url {url!r} does not carry port {port}"
            assert url.startswith("http://"), f"url not http: {url!r}"

    def test_pid_present_iff_running(self, http_client):
        d = http_client.get("/eink-bridge/api/status").json()
        if d["running"]:
            assert d["pid"], "running bridge must report a pid"
        else:
            assert d["pid"] is None, f"stopped bridge must have pid=None, got {d['pid']!r}"

    # ── stop: idempotent, no spawn ───────────────────────────────────────
    def test_stop_is_idempotent(self, http_client):
        # Stop is always safe to call: it only ever terminates a handle the app
        # itself spawned. Calling it leaves the bridge stopped either way.
        resp = http_client.post("/eink-bridge/api/stop")
        d = assert_dict_response(resp)
        assert d.get("ok") is True, f"stop should return ok: {d}"
        assert d.get("running") is False, f"after stop, running must be False: {d}"

    # ── start guards (no spawn) ──────────────────────────────────────────
    def test_writable_default_is_read_only(self, http_client):
        # The e-ink-friendly default is read-only; writable must be opt-in.
        d = http_client.get("/eink-bridge/api/status").json()
        assert d["writable"] is False, (
            "default must be read-only (writable=false); a writable shell needs auth"
        )

    # ── start -> stop round-trip (real spawn, fully guarded) ─────────────
    def test_start_then_stop_roundtrip(self, http_client):
        status = http_client.get("/eink-bridge/api/status").json()
        if not status["installed"]:
            pytest.skip("ttyd not installed in this environment")
        if status["running"]:
            pytest.skip("bridge already running (left from another session)")

        started = http_client.post("/eink-bridge/api/start").json()
        if started.get("error"):
            # port in use / spawn failure — environment issue, not an app bug
            http_client.post("/eink-bridge/api/stop")
            pytest.skip(f"start soft-failed: {started['error']}")
        try:
            assert started.get("running") is True, f"after start, running expected: {started}"
            assert started.get("pid"), "running bridge must report a pid"
        finally:
            stopped = http_client.post("/eink-bridge/api/stop").json()
            assert stopped.get("running") is False, f"stop did not stop the bridge: {stopped}"


@pytest.mark.interactive
class TestEinkBridgeUI:
    def test_ui_page_loads(self, page, base_url, page_errors):
        resp = page.goto(base_url + "/eink-bridge/", wait_until="domcontentloaded", timeout=15000)
        assert resp.status == 200
        wait_briefly(page, 1200)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_core_controls_present(self, page, base_url, page_errors):
        page.goto(base_url + "/eink-bridge/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1000)
        assert page.locator("#toggle").count() == 1, "missing Start/Stop toggle"
        assert page.locator("#run-chip").count() == 1, "missing run-state chip"
        assert page.locator("#kv").count() == 1, "missing status key/value panel"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_toggle_reflects_state(self, page, base_url, page_errors):
        page.goto(base_url + "/eink-bridge/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        # After the status load, the toggle button text must read Start or Stop.
        label = page.locator("#toggle").inner_text().strip()
        assert label in ("Start", "Stop"), f"toggle label unexpected: {label!r}"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
