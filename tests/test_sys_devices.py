"""System tests: devices app — registry register / list / detail / heartbeat / delete.

LLM-free, fast. Registry state lives in data/ (not the vault), so this module
cleans up its own PLAYWRIGHT-TEST- devices after each test.
"""

import pytest

PREFIX = "PLAYWRIGHT-TEST-"


@pytest.fixture(autouse=True)
def _cleanup(http_client):
    yield
    try:
        data = http_client.get("/devices/api/devices").json()
        for d in data.get("devices", []):
            if str(d.get("id", "")).startswith(PREFIX):
                http_client.delete("/devices/api/devices/" + d["id"])
        conns = http_client.get("/devices/api/connections").json()
        for c in conns.get("connections", []):
            if str(c.get("name", "")).startswith(PREFIX):
                http_client.delete("/devices/api/connections/" + c["id"])
    except Exception:
        pass


def _register(http_client, did, category="controller", **extra):
    return http_client.post("/devices/api/register", json={"id": did, "category": category, **extra})


@pytest.mark.api
class TestDevicesAPI:
    def test_register_and_list(self, http_client):
        did = PREFIX + "dev1"
        r = _register(http_client, did, board="atoms3r", name=PREFIX + "Puck")
        assert r.status_code == 200 and r.json().get("ok")
        data = http_client.get("/devices/api/devices").json()
        assert did in [d["id"] for d in data["devices"]]
        assert "controller" in data["categories"]

    def test_bad_category_rejected(self, http_client):
        r = _register(http_client, PREFIX + "badcat", category="nonsense")
        assert "error" in r.json()

    def test_missing_id_rejected(self, http_client):
        r = http_client.post("/devices/api/register", json={"category": "sensor"})
        assert "error" in r.json()

    def test_detail_and_heartbeat(self, http_client):
        did = PREFIX + "dev2"
        _register(http_client, did, category="sensor")
        d = http_client.get("/devices/api/devices/" + did).json()
        assert d["id"] == did and d["category"] == "sensor"
        assert d["online"] is True            # just registered → online
        assert http_client.post("/devices/api/devices/" + did + "/heartbeat").json().get("ok")

    def test_register_is_idempotent(self, http_client):
        did = PREFIX + "dev-idem"
        _register(http_client, did, board="atoms3r")
        _register(http_client, did, board="core-s3")     # re-register updates board
        d = http_client.get("/devices/api/devices/" + did).json()
        assert d["board"] == "core-s3"

    def test_delete(self, http_client):
        did = PREFIX + "dev3"
        _register(http_client, did)
        assert http_client.delete("/devices/api/devices/" + did).json().get("ok")
        assert "error" in http_client.get("/devices/api/devices/" + did).json()

    def test_category_filter(self, http_client):
        _register(http_client, PREFIX + "aud1", category="audio")
        data = http_client.get("/devices/api/devices?category=audio").json()
        assert all(x["category"] == "audio" for x in data["devices"])

    def test_simulators_aggregated(self, http_client):
        # Each device type's [[contributes.devices.simulator]] is aggregated.
        sims = http_client.get("/devices/api/simulators").json().get("simulators", [])
        cats = {s["category"] for s in sims}
        assert "sensor" in cats, "the devices app's own sensor sim should appear"
        assert all(s.get("url") for s in sims)

    def test_sim_emit_registers_and_buses(self, http_client):
        # A sim emit registers the device + returns the bused payload.
        did = PREFIX + "sensorX"
        r = http_client.post("/devices/api/sim/emit", json={
            "event": "sensor:reading", "id": did, "category": "sensor",
            "data": {"type": "light", "value": 42}})
        body = r.json()
        assert body.get("ok") and body.get("emitted") == "sensor:reading"
        d = http_client.get("/devices/api/devices/" + did).json()
        assert d.get("category") == "sensor"

    def test_connection_crud(self, http_client):
        r = http_client.post("/devices/api/connections", json={
            "name": PREFIX + "c1", "source_event": "device:motion",
            "target_type": "emit", "target_event": "device:wake"})
        cid = r.json()["connection"]["id"]
        assert any(c["id"] == cid for c in http_client.get("/devices/api/connections").json()["connections"])
        assert http_client.post("/devices/api/connections/" + cid + "/toggle").json().get("ok")
        assert http_client.delete("/devices/api/connections/" + cid).json().get("ok")

    def test_connection_fires_on_matching_event(self, http_client):
        import time
        src = PREFIX + ":trigger"
        http_client.post("/devices/api/connections", json={
            "name": PREFIX + "wire", "source_event": src,
            "target_type": "emit", "target_event": PREFIX + ":fired"})
        http_client.post("/devices/api/sim/emit", json={"event": src, "data": {}})
        time.sleep(0.5)   # dispatch is create_task (async)
        recent = http_client.get("/devices/api/connections").json().get("recent", [])
        assert any(r.get("source_event") == src and r.get("ok") for r in recent)


PANEL = {
    "views": [{"id": "home", "sections": [
        {"source": "hub:task.todays-tasks", "accent": "blue", "max_lines": 5},
        {"source": "builtin:companion"},
        {"source": "builtin:devices-online", "accent": "green"},
    ]}],
    "refresh_min": 20,
}


@pytest.mark.api
class TestDisplayDashboard:
    def test_dashboard_png_and_refresh_header(self, http_client):
        did = PREFIX + "paper"
        _register(http_client, did, category="display", board="m5paper-color")
        r = http_client.get(f"/devices/api/dashboard/{did}")
        assert r.status_code == 200
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert r.headers.get("x-eos-refresh-min", "").isdigit()

    def test_panel_config_round_trip_and_hint(self, http_client):
        did = PREFIX + "paper2"
        _register(http_client, did, category="display")
        r = http_client.put(f"/devices/api/devices/{did}/panel", json=PANEL)
        assert r.json().get("ok"), r.json()
        d = http_client.get(f"/devices/api/devices/{did}").json()
        assert d.get("panel", {}).get("refresh_min") == 20
        # The configured cadence shows up in the poll hint (no quiet hours set).
        r = http_client.get(f"/devices/api/dashboard/{did}")
        assert r.headers.get("x-eos-refresh-min") == "20"

    def test_panel_validation_rejects_bad_config(self, http_client):
        did = PREFIX + "paper3"
        _register(http_client, did, category="display")
        bad_accent = {"views": [{"sections": [
            {"source": "builtin:devices-online", "accent": "chartreuse"}]}]}
        assert "error" in http_client.put(
            f"/devices/api/devices/{did}/panel", json=bad_accent).json()
        bad_source = {"views": [{"sections": [{"source": "builtin:nope"}]}]}
        assert "error" in http_client.put(
            f"/devices/api/devices/{did}/panel", json=bad_source).json()
        no_views = {"views": []}
        assert "error" in http_client.put(
            f"/devices/api/devices/{did}/panel", json=no_views).json()

    def test_dashboard_data_composes_configured_sections(self, http_client):
        did = PREFIX + "paper4"
        _register(http_client, did, category="display")
        http_client.put(f"/devices/api/devices/{did}/panel", json=PANEL)
        data = http_client.get(f"/devices/api/dashboard/{did}/data").json()
        assert data.get("clock") and data.get("view") == "home"
        assert data.get("refresh_min") == 20
        # devices-online must be present (this registry always has devices here);
        # task/companion sections depend on the deployment — fail-soft is the contract.
        headings = [s.get("heading", "") for s in data.get("sections", [])]
        assert any(h.startswith("Devices") for h in headings), headings

    def test_panel_sources_lists_builtins_and_hub_panels(self, http_client):
        srcs = http_client.get("/devices/api/panel-sources").json().get("sources", [])
        ids = [s["source"] for s in srcs]
        assert "builtin:companion" in ids and "builtin:devices-online" in ids
        hub_ones = [s for s in srcs if s["source"].startswith("hub:")]
        assert hub_ones and all("adaptable" in s for s in hub_ones)

    def test_display_dirty_shortens_next_poll_then_clears(self, http_client):
        did = PREFIX + "paper5"
        _register(http_client, did, category="display")
        assert http_client.post(f"/devices/api/displays/{did}/dirty").json().get("ok")
        r = http_client.get(f"/devices/api/dashboard/{did}")
        assert r.headers.get("x-eos-refresh-min") == "2"     # dirty → fast follow-up
        r = http_client.get(f"/devices/api/dashboard/{did}")
        assert r.headers.get("x-eos-refresh-min") != "2"     # consumed → back to base

    def test_reregister_preserves_panel_and_records_telemetry(self, http_client):
        """The board re-registers on every wake — that must NOT wipe the panel
        config, and battery/rssi telemetry should land on the record."""
        did = PREFIX + "paper7"
        _register(http_client, did, category="display")
        http_client.put(f"/devices/api/devices/{did}/panel", json=PANEL)
        _register(http_client, did, category="display", battery=87, rssi=-61)
        d = http_client.get(f"/devices/api/devices/{did}").json()
        assert d.get("panel", {}).get("refresh_min") == 20, "panel wiped by re-register"
        assert d.get("battery") == 87 and d.get("rssi") == -61
        # battery -1 = "can't read" → previous value kept, not overwritten.
        _register(http_client, did, category="display", battery=-1)
        d = http_client.get(f"/devices/api/devices/{did}").json()
        assert d.get("battery") == 87
        # Battery shows in the dashboard footer (plain text — emoji get stripped).
        data = http_client.get(f"/devices/api/dashboard/{did}/data").json()
        assert "bat 87%" in data.get("footer", "")

    def test_view_cycling_with_next(self, http_client):
        did = PREFIX + "paper6"
        _register(http_client, did, category="display")
        two_views = {"views": [
            {"id": "home", "sections": [{"source": "builtin:devices-online"}]},
            {"id": "agenda", "sections": [{"source": "builtin:devices-online"}]},
        ]}
        http_client.put(f"/devices/api/devices/{did}/panel", json=two_views)
        assert http_client.get(f"/devices/api/dashboard/{did}/data").json()["view"] == "home"
        http_client.get(f"/devices/api/dashboard/{did}?view=next")   # button wake
        assert http_client.get(f"/devices/api/dashboard/{did}/data").json()["view"] == "agenda"


@pytest.mark.api
class TestInteractiveTwin:
    def test_interactive_endpoint_structure(self, http_client):
        did = PREFIX + "twin1"
        _register(http_client, did, category="display")
        http_client.put(f"/devices/api/devices/{did}/panel", json=PANEL)
        d = http_client.get(f"/devices/api/panel/{did}/interactive").json()
        assert d.get("view") == "home"
        assert d.get("device", {}).get("id") == did
        secs = d.get("sections", [])
        assert secs, "at least devices-online should resolve"
        for s in secs:
            assert s.get("source", "").startswith(("hub:", "builtin:"))
            assert isinstance(s.get("rows"), list) and s["rows"]
            for r in s["rows"]:
                assert r.get("text") or r.get("href")

    def test_interactive_unregistered_device_errors(self, http_client):
        d = http_client.get("/devices/api/panel/" + PREFIX + "nope/interactive").json()
        assert "error" in d

    def test_reminder_act_round_trip(self, http_client):
        """A twin `act` block must actually complete the reminder it points at."""
        import datetime
        # Purge leftovers from prior failed runs — duplicate PREFIX texts break
        # the completed-gone assertion (bit us on :9000).
        pre = http_client.get("/reminders/api/reminders")
        if pre.status_code == 404:
            pytest.skip("reminders app not installed on this daemon")
        existing = pre.json()
        for rem in (existing if isinstance(existing, list) else existing.get("reminders", [])):
            if PREFIX in str(rem.get("text", "")) and rem.get("id"):
                http_client.delete(f"/reminders/api/reminders/{rem['id']}")
        # due today: sorts early so a lived-in vault's other reminders can't
        # push the fixture past the section's max_lines cap.
        due = datetime.date.today().isoformat()
        r = http_client.post("/reminders/api/reminders",
                             json={"text": PREFIX + "twin reminder", "due": due})
        rid = (r.json().get("reminder") or r.json()).get("id")
        assert rid, r.json()
        did = PREFIX + "twin2"
        _register(http_client, did, category="display")
        http_client.put(f"/devices/api/devices/{did}/panel", json={
            "views": [{"id": "home", "sections": [
                {"source": "builtin:reminders", "days": 2, "max_lines": 12},
                {"source": "builtin:devices-online"}]}]})
        d = http_client.get(f"/devices/api/panel/{did}/interactive").json()
        rem_rows = [row for s in d.get("sections", []) if s.get("source") == "builtin:reminders"
                    for row in s["rows"]
                    if any(rid in a.get("url", "") for a in row.get("acts", []))]
        assert rem_rows, "the created reminder should appear as a twin row (by rid)"
        act = rem_rows[0]["acts"][0]
        assert act["label"] == "Done"
        res = http_client.request(act["method"], act["url"], json=act.get("body") or {})
        assert res.status_code == 200 and not res.json().get("error")
        # Completed → gone from the next interactive read (matched by rid).
        d2 = http_client.get(f"/devices/api/panel/{did}/interactive").json()
        left = [row for s in d2.get("sections", []) if s.get("source") == "builtin:reminders"
                for row in s["rows"]
                if any(rid in a.get("url", "") for a in row.get("acts", []))]
        assert not left
        # Cleanup the completed reminder record.
        http_client.delete(f"/reminders/api/reminders/{rid}")


@pytest.mark.interactive
class TestDevicesUI:
    def test_panel_modal_configures_and_saves(self, http_client, page, base_url):
        """UI walk: display card → panel link → modal renders sections → Save round-trips."""
        did = PREFIX + "paper-ui"
        _register(http_client, did, category="display", name=PREFIX + "Paper")
        page.goto(base_url + "/devices/")
        card = page.locator(f".card:has-text('{PREFIX}Paper')").first
        card.locator("text=panel").click()
        page.wait_for_selector("#pc-rows .card")
        assert page.locator("#pc-rows .card").count() >= 3, \
            "default sections should populate the editor"
        page.click("button:has-text('Save')")
        page.wait_for_timeout(800)
        assert page.locator("#pc-err").inner_text().strip() == ""
        d = http_client.get(f"/devices/api/devices/{did}").json()
        assert d.get("panel", {}).get("views"), "saved panel must persist on the device"

    def test_twin_page_renders_sections(self, http_client, page, base_url):
        did = PREFIX + "twin-ui"
        _register(http_client, did, category="display", name=PREFIX + "TwinUI")
        http_client.put(f"/devices/api/devices/{did}/panel", json=PANEL)
        page.goto(base_url + f"/devices/pages/panel.html?id={did}")
        page.wait_for_selector(".sec .row", timeout=15000)
        assert page.locator(".sec").count() >= 1
        assert PREFIX + "TwinUI" in page.locator("#p-title").inner_text()
