"""System tests: proactive engine — policy gate, dark default, scan, test nudge.

LLM-free, fast. The proactive policy is a SHARED store (data/proactive/), not
per-app and not PLAYWRIGHT-prefixed, so this module snapshots the policy on entry
and restores it on exit — safe to run against the live daemon without flipping the
user's real proactive settings on. The delivering test routes voice-only (an event
emit, no vault write) so it never pollutes the notifications vault file.
"""

import pytest

POLICY_KEYS = ("enabled", "quiet_start", "quiet_end", "daily_cap",
               "min_gap_sec", "critical_bypasses_quiet", "default_channels", "kinds")


@pytest.fixture(autouse=True)
def _restore_policy(http_client):
    orig = http_client.get("/proactive/api/policy").json()
    snapshot = {k: orig[k] for k in POLICY_KEYS if k in orig}
    yield
    try:
        http_client.post("/proactive/api/policy", json=snapshot)
    except Exception:
        pass


def _set_policy(http_client, **fields):
    return http_client.post("/proactive/api/policy", json=fields)


@pytest.mark.api
class TestProactiveAPI:
    def test_policy_shape_and_catalog(self, http_client):
        p = http_client.get("/proactive/api/policy").json()
        assert isinstance(p.get("enabled"), bool)
        cat = p.get("_kinds_catalog") or {}
        for k in ("journaling-gap", "budget", "deadline", "today-load", "reminder",
                  "eod-wins", "wellbeing", "milestone", "system", "runbook"):
            assert k in cat

    def test_dark_default_suppresses_test_nudge(self, http_client):
        _set_policy(http_client, enabled=False)
        r = http_client.post("/proactive/api/test", json={"text": "should not deliver"}).json()
        assert r["delivered"] is False
        assert r["reason"] == "disabled"

    def test_enable_then_test_delivers_voice_only(self, http_client):
        _set_policy(http_client, enabled=True, quiet_start="00:00", quiet_end="00:00",
                    min_gap_sec=0, daily_cap=1000, default_channels=["voice"])
        r = http_client.post("/proactive/api/test", json={"text": "live test nudge"}).json()
        assert r["delivered"] is True
        assert r["channels"] == ["voice"]

    def test_log_records_delivery(self, http_client):
        _set_policy(http_client, enabled=True, quiet_start="00:00", quiet_end="00:00",
                    min_gap_sec=0, daily_cap=1000, default_channels=["voice"])
        marker = "log-roundtrip-probe"
        http_client.post("/proactive/api/test", json={"text": marker})
        log = http_client.get("/proactive/api/log?limit=20").json().get("log", [])
        assert any(marker in str(r.get("text", "")) for r in log)

    def test_quiet_hours_suppresses(self, http_client):
        # Quiet window covering the whole day → any nudge suppressed regardless of clock.
        _set_policy(http_client, enabled=True, quiet_start="00:00", quiet_end="23:59",
                    min_gap_sec=0, default_channels=["voice"])
        r = http_client.post("/proactive/api/test", json={"text": "quiet probe"}).json()
        assert r["delivered"] is False
        assert r["reason"] == "quiet-hours"

    def test_mute_toggle_persists(self, http_client):
        r = http_client.post("/proactive/api/mute", json={"kind": "today-load", "mute": True}).json()
        assert r["ok"] is True
        p = http_client.get("/proactive/api/policy").json()
        assert p["kinds"]["today-load"]["mute"] is True

    def test_mute_unknown_kind_rejected(self, http_client):
        r = http_client.post("/proactive/api/mute", json={"kind": "nonsense"}).json()
        assert r["ok"] is False

    def test_scan_runs_when_disabled_is_noop(self, http_client):
        _set_policy(http_client, enabled=False)
        r = http_client.post("/proactive/api/scan").json()
        assert r["ok"] is True
        assert r["enabled"] is False
        assert r["delivered"] == 0

    def test_scan_runs_when_enabled(self, http_client):
        _set_policy(http_client, enabled=True, quiet_start="00:00", quiet_end="00:00",
                    min_gap_sec=0, daily_cap=1000, default_channels=["voice"])
        r = http_client.post("/proactive/api/scan").json()
        assert r["ok"] is True
        assert r["enabled"] is True
        assert isinstance(r.get("generated"), int)


@pytest.mark.interactive
class TestProactiveUI:
    def test_page_loads(self, http_client):
        r = http_client.get("/proactive/")
        assert r.status_code == 200
        assert "Proactive" in r.text
