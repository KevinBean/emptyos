"""System app tests: BESS Analyser — AEMO fetch + MILP dispatch + sizing sweep.

The optimise/sweep tests hit live AEMO data + solve a real MILP, so they're slower
and need network; they're marked so they can be deselected offline.
"""

from __future__ import annotations

import pytest

from helpers import TEST_PREFIX, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


def _connection_enabled(http_client) -> bool:
    """Read the dark-default flag gate the way the page does."""
    data = assert_ok(http_client.get("/bess-analyser/api/connection/config"))
    return bool(data.get("enabled"))


@pytest.fixture(scope="module", autouse=True)
def _sweep_bess_project_dirs(server_health):
    """Filesystem backstop for the project DIRECTORIES this file creates.

    ``vault_project_delete`` is a *soft* delete (sets ``archived: true``), and the
    conftest vault leak-guard only removes the prefix-named main ``.md`` — neither
    removes the project directory or its non-prefixed ``milestone-log.md`` sidecar.
    So after these tests run, ``…/projects/PLAYWRIGHT-TEST-…/`` dirs would linger.
    This module-scoped teardown removes any TEST_PREFIX-named project dir under the
    bess-analyser projects root. Pure file I/O (no kernel import), runs only when a
    live daemon could have written this session.
    """
    yield
    if not server_health:
        return
    try:
        import shutil
        import tomllib
        from pathlib import Path
        with open("emptyos.toml", "rb") as f:
            vault = Path((tomllib.load(f).get("notes") or {}).get("path") or "")
        base = vault / "30_Resources/EmptyOS/bess-analyser/projects"
        if not base.exists():
            return
        lower = TEST_PREFIX.lower()
        for d in base.iterdir():
            if d.is_dir() and (TEST_PREFIX in d.name or lower in d.name.lower()):
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


@pytest.mark.api
class TestBessAnalyserAPI:
    # --- fast: no network, exercise routing + validation -----------------
    def test_regions_lists_nem(self, http_client):
        data = assert_ok(http_client.get("/bess-analyser/api/regions"))
        assert "SA1" in data["regions"]
        assert data["default"] == "SA1"
        assert len(data["regions"]) == 5  # all NEM regions

    def test_unknown_region_optimise_returns_error(self, http_client):
        data = assert_ok(http_client.post("/bess-analyser/api/optimise", json={"region": "ZZ9"}))
        assert "error" in data

    def test_unknown_region_sweep_returns_error(self, http_client):
        data = assert_ok(http_client.post("/bess-analyser/api/sweep", json={"region": "ZZ9"}))
        assert "error" in data

    def test_negative_power_rejected(self, http_client):
        data = assert_ok(http_client.post(
            "/bess-analyser/api/optimise", json={"region": "SA1", "power_mw": -5}))
        assert "error" in data

    def test_zero_duration_rejected(self, http_client):
        data = assert_ok(http_client.post(
            "/bess-analyser/api/optimise", json={"region": "SA1", "duration_h": 0}))
        assert "error" in data

    def test_bad_efficiency_rejected(self, http_client):
        data = assert_ok(http_client.post(
            "/bess-analyser/api/optimise", json={"region": "SA1", "round_trip_efficiency": 1.5}))
        assert "error" in data

    @pytest.mark.slow
    def test_optimise_returns_revenue_and_sample_day(self, http_client):
        r = http_client.post(
            "/bess-analyser/api/optimise",
            json={"region": "SA1", "month": "2024-07", "power_mw": 50, "duration_h": 2},
            timeout=90,
        )
        data = assert_ok(r)
        assert "error" not in data, data
        assert data["total_revenue"] > 0
        assert data["energy_mwh"] == 100
        sd = data["sample_day"]
        assert len(sd["charge"]) == len(sd["discharge"]) == len(sd["soc"]) == len(sd["price"])
        # physical invariant: no simultaneous charge + discharge
        assert all(min(c, d) < 1e-3 for c, d in zip(sd["charge"], sd["discharge"]))

    @pytest.mark.slow
    def test_optimise_reports_round_trip_efficiency(self, http_client):
        data = assert_ok(http_client.post(
            "/bess-analyser/api/optimise",
            json={"region": "SA1", "month": "2024-07", "round_trip_efficiency": 0.90},
            timeout=90,
        ))
        assert "error" not in data, data
        # the requested RTE should round-trip back in the response
        assert abs(data["round_trip_efficiency"] - 0.90) < 0.01
        # totals are internally consistent
        assert data["total_revenue"] == pytest.approx(
            data["avg_daily_revenue"] * data["days"], rel=0.02)

    @pytest.mark.slow
    def test_optimise_soc_stays_in_band(self, http_client):
        data = assert_ok(http_client.post(
            "/bess-analyser/api/optimise",
            json={"region": "SA1", "month": "2024-07", "power_mw": 100, "duration_h": 2},
            timeout=90,
        ))
        sd = data["sample_day"]
        # usable band is 5%..95% of 200 MWh
        assert min(sd["soc"]) >= 0.05 * 200 - 1.0
        assert max(sd["soc"]) <= 0.95 * 200 + 1.0

    @pytest.mark.slow
    def test_sweep_revenue_increases_with_duration(self, http_client):
        data = assert_ok(http_client.post(
            "/bess-analyser/api/sweep",
            json={"region": "SA1", "month": "2024-07", "power_mw": 50, "durations": [1, 2]},
            timeout=120,
        ))
        assert "error" not in data, data
        revs = [p["revenue"] for p in data["points"]]
        # more storage can capture at least as much arbitrage
        assert revs[1] >= revs[0]
        # per-capacity field present for sizing analysis
        assert all("revenue_per_mwh_capacity" in p for p in data["points"])

    @pytest.mark.slow
    def test_optimise_arbitrage_only_has_no_fcas(self, http_client):
        """Without an FCAS opt-in, the response must NOT carry the additive fields
        (the route stays byte-shape-identical to the pure arbitrage one)."""
        data = assert_ok(http_client.post(
            "/bess-analyser/api/optimise",
            json={"region": "SA1", "month": "2024-07", "power_mw": 100, "duration_h": 2},
            timeout=90,
        ))
        assert "error" not in data, data
        assert "total_with_fcas" not in data
        assert "fcas" not in data

    @pytest.mark.slow
    def test_optimise_with_fcas_adds_total(self, http_client):
        """fcas:true on /api/optimise adds the additive FCAS block + grand total."""
        data = assert_ok(http_client.post(
            "/bess-analyser/api/optimise",
            json={"region": "SA1", "month": "2024-07", "power_mw": 100,
                  "duration_h": 2, "fcas": True},
            timeout=90,
        ))
        assert "error" not in data, data
        assert "fcas" in data and "total_with_fcas" in data
        fcas = data["fcas"]
        for k in ("markets", "fcas_total", "hours"):
            assert k in fcas, f"fcas block missing {k}: {list(fcas.keys())}"
        assert set(fcas["markets"]).issubset(
            {"raise_reg", "lower_reg", "raise_6s", "lower_6s"})
        # additive — total_with_fcas == arbitrage total + fcas_total
        assert data["total_with_fcas"] == pytest.approx(
            round(data["total_revenue"] + fcas["fcas_total"], 2), abs=0.01)
        assert "grid_forming" in data  # echoed request flag


@pytest.mark.api
class TestBessConnectionIntelligence:
    """Connection-intelligence layer (Session 2) — vault project store, FCAS,
    milestones, board contract. All gated behind feature.bess-connection.enabled.

    The config endpoint is always live; the flow tests skip cleanly when the
    flag is off, so this suite passes in both deployment states.
    """

    def test_connection_config_endpoint(self, http_client):
        data = assert_ok(http_client.get("/bess-analyser/api/connection/config"))
        assert "enabled" in data
        assert isinstance(data["enabled"], bool)

    def test_projects_list_gating(self, http_client):
        """List route reflects the flag: {disabled} when off, {projects} when on."""
        data = assert_ok(http_client.get("/bess-analyser/api/projects"))
        if _connection_enabled(http_client):
            assert "projects" in data and isinstance(data["projects"], list)
        else:
            assert data.get("disabled") is True

    def test_create_get_delete_lifecycle(self, http_client):
        if not _connection_enabled(http_client):
            pytest.skip("feature.bess-connection.enabled is off")
        pid = f"{TEST_PREFIX}bess-life"
        try:
            created = assert_ok(http_client.post("/bess-analyser/api/projects", json={
                "id": pid, "name": f"{TEST_PREFIX}Lifecycle", "region": "SA1",
                "power_mw": 100, "duration_h": 2, "fcas_markets": ["raise_reg"],
            }))
            assert created.get("ok") is True, created
            # single-project detail (regression: previously 500'd on decode arity)
            got = assert_ok(http_client.get(f"/bess-analyser/api/projects/{pid}"))
            assert "error" not in got, got
            assert got["id"] == pid
            assert got["region"] == "SA1"
            assert got["fcas_markets"] == ["raise_reg"]  # decoded back to a list
            assert got["milestone"] == "enquiry"
            # appears in list_all rows
            rows = assert_ok(http_client.get("/bess-analyser/api/projects"))["projects"]
            assert any(r["id"] == pid for r in rows)
        finally:
            assert_ok(http_client.delete(f"/bess-analyser/api/projects/{pid}"))

    def test_set_field_via_board(self, http_client):
        """The board contract (list_all/set_field) drives inline edits.

        Boards exposes the app's set_field through PATCH
        /api/boards/{board}/items/{item} with a flat {field: value} body, and
        only honours fields that are both declared board columns AND in the
        app's SETTABLE_FIELDS. power_mw satisfies both.
        """
        if not _connection_enabled(http_client):
            pytest.skip("feature.bess-connection.enabled is off")
        pid = f"{TEST_PREFIX}bess-setfield"
        try:
            assert_ok(http_client.post("/bess-analyser/api/projects", json={
                "id": pid, "name": f"{TEST_PREFIX}SetField", "region": "SA1",
                "power_mw": 50, "duration_h": 2,
            }))
            res = assert_ok(http_client.patch(
                f"/boards/api/boards/bess-connection-projects/items/{pid}",
                json={"power_mw": 75},
            ))
            assert res.get("ok") is True, res
            got = assert_ok(http_client.get(f"/bess-analyser/api/projects/{pid}"))
            assert float(got["power_mw"]) == 75.0
        finally:
            assert_ok(http_client.delete(f"/bess-analyser/api/projects/{pid}"))

    def test_milestone_advance_and_log(self, http_client):
        if not _connection_enabled(http_client):
            pytest.skip("feature.bess-connection.enabled is off")
        pid = f"{TEST_PREFIX}bess-ms"
        try:
            assert_ok(http_client.post("/bess-analyser/api/projects", json={
                "id": pid, "name": f"{TEST_PREFIX}Milestone", "region": "SA1",
                "power_mw": 100, "duration_h": 2,
            }))
            adv = assert_ok(http_client.post(
                f"/bess-analyser/api/projects/{pid}/milestone/advance",
                json={"note": "screened"}))
            assert adv.get("ok") is True, adv
            assert adv["milestone"] == "connection_application"  # forward from enquiry
            # transitions is a clean flat list (regression: previously leaked
            # the sidecar envelope's dict keys)
            assert isinstance(adv["transitions"], list)
            assert adv["transitions"][-1]["milestone"] == "connection_application"
            assert adv["transitions"][-1]["note"] == "screened"
            # a backward target is rejected (must be a forward move)
            back = http_client.post(
                f"/bess-analyser/api/projects/{pid}/milestone/advance",
                json={"to": "enquiry"}).json()
            assert back.get("error"), "backward milestone move must be rejected"
            log = assert_ok(http_client.get(
                f"/bess-analyser/api/projects/{pid}/milestone/log"))
            assert isinstance(log["transitions"], list)
            assert log["transitions"][-1]["milestone"] == "connection_application"
        finally:
            assert_ok(http_client.delete(f"/bess-analyser/api/projects/{pid}"))

    @pytest.mark.slow
    def test_project_optimise_with_fcas(self, http_client):
        if not _connection_enabled(http_client):
            pytest.skip("feature.bess-connection.enabled is off")
        pid = f"{TEST_PREFIX}bess-fcas"
        try:
            assert_ok(http_client.post("/bess-analyser/api/projects", json={
                "id": pid, "name": f"{TEST_PREFIX}FCAS", "region": "SA1",
                "power_mw": 100, "duration_h": 2,
                "fcas_markets": ["raise_reg", "lower_reg"],
            }))
            # project-scoped optimise (regression: previously 500'd on decode arity)
            data = assert_ok(http_client.post(
                f"/bess-analyser/api/projects/{pid}/optimise",
                json={"month": "2024-07",
                      "fcas_prices": {"raise_reg": 12.0, "lower_reg": 8.0}},
                timeout=120))
            assert "error" not in data, data
            assert data["total_revenue"] > 0  # arbitrage from the fixed MILP dispatch
            # FCAS is additive on top of the arbitrage dispatch (not co-optimised)
            fcas = data["fcas"]
            assert fcas["fcas_total"] > 0
            assert set(fcas["markets"]) == {"raise_reg", "lower_reg"}
            assert data["total_with_fcas"] == pytest.approx(
                data["total_revenue"] + fcas["fcas_total"], rel=1e-6)
        finally:
            assert_ok(http_client.delete(f"/bess-analyser/api/projects/{pid}"))


@pytest.mark.api
class TestBessBoardContract:
    """Boards view-layer contract (source.type == 'app'). Not flag-gated —
    boards reads list_all directly; the source-status endpoint is always live."""

    def test_board_source_status(self, http_client):
        data = assert_ok(http_client.get(
            "/boards/api/boards/bess-connection-projects/source-status"))
        assert data.get("type") == "app", data
        assert data.get("app") == "bess-analyser", data

    def test_board_lists_created_project(self, http_client):
        """A project created via the app surfaces as a board row through list_all."""
        if not _connection_enabled(http_client):
            pytest.skip("feature.bess-connection.enabled is off")
        pid = f"{TEST_PREFIX}bess-board"
        try:
            assert_ok(http_client.post("/bess-analyser/api/projects", json={
                "id": pid, "name": f"{TEST_PREFIX}Board", "region": "QLD1",
                "power_mw": 100, "duration_h": 2,
            }))
            board = assert_ok(http_client.get(
                "/boards/api/boards/bess-connection-projects/items"))
            items = board.get("items", []) if isinstance(board, dict) else board
            ids = [str(it.get("id") or it.get("file", "")).replace(".md", "")
                   for it in (items or [])]
            assert any(pid in i for i in ids), (
                f"created project not surfaced on board: {ids[:20]}")
        finally:
            assert_ok(http_client.delete(f"/bess-analyser/api/projects/{pid}"))


@pytest.mark.interactive
class TestBessAnalyserUI:
    def test_ui_page_loads(self, page, base_url, page_errors):
        resp = page.goto(base_url + "/bess-analyser/",
                         wait_until="domcontentloaded", timeout=15000)
        assert resp.status == 200
        wait_briefly(page, 1200)
        # An AEMO fetch failure surfaces as a rejected fetch, not an app JS bug —
        # allow that class of message so the load test isn't flaky on a network
        # outage outside the app's control.
        assert_no_js_errors(page_errors, allow_patterns=["fetch", "AEMO", "Failed to load"])
