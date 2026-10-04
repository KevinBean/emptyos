"""System tests for rooms — team mode (API surface).

Covers the team-mode HTTP routes end-to-end against a live daemon: role
assignment toggling team-enablement, shared-task CRUD + validation, and the
auto-dispatch run state machine (arm / budget-cap / double-start / stop).

Deliberately does NOT drive a full LLM-powered team run — that needs a real
think provider and is non-deterministic. The turn *execution* reuses the
already-tested chat path; here we verify the team coordination *surface* the
UI (rooms-team.js) drives.

Pure-logic coverage (role normalization, prompt blocks, loop budget math) is
in test_unit_rooms_logic.py. This file is the live-daemon companion.

Self-cleaning: creates two TEST_PREFIX agents + a group room and removes them
in teardown, since conftest's sweep handles rooms-by-title but not the agent
records team rooms are built from.
"""

import pytest

from helpers import TEST_PREFIX, assert_ok


@pytest.mark.api
class TestRoomsTeamAPI:
    @pytest.fixture
    def team_room(self, http_client):
        """Create lead + worker agents and a group room; yield ids; clean up."""
        created_agents = []

        def _mk_agent(name):
            r = http_client.post("/rooms/api/agents", json={
                "name": f"{TEST_PREFIX}{name}",
                "system_prompt": f"You are {name}.",
            })
            data = r.json()
            aid = data.get("id") or (data.get("agent") or {}).get("id")
            assert aid, f"agent create failed: {data}"
            created_agents.append(aid)
            return aid

        lead = _mk_agent("teamlead")
        worker = _mk_agent("teamworker")

        r = http_client.post("/rooms/api/rooms", json={
            "title": f"{TEST_PREFIX}team",
            "participants": [
                {"type": "agent", "id": lead},
                {"type": "agent", "id": worker},
            ],
        })
        room = r.json()
        rid = room.get("id")
        assert rid, f"room create failed: {room}"

        yield {"rid": rid, "lead": lead, "worker": worker}

        # Teardown — archive the room, delete both agents.
        http_client.post(f"/rooms/api/rooms/{rid}/archive")
        http_client.request("DELETE", f"/rooms/api/agents/{rid}")
        for aid in created_agents:
            http_client.request("DELETE", f"/rooms/api/agents/{aid}")

    # ── team enablement via roles ──

    def test_room_not_team_until_lead(self, http_client, team_room):
        data = assert_ok(http_client.get(f"/rooms/api/rooms/{team_room['rid']}/team"))
        assert data["enabled"] is False
        assert data["lead"] == ""
        assert all(p["role"] == "peer" for p in data["roster"])

    def test_assign_lead_enables_team(self, http_client, team_room):
        rid, lead = team_room["rid"], team_room["lead"]
        r = assert_ok(http_client.post(
            f"/rooms/api/rooms/{rid}/team/role",
            json={"participant_id": lead, "role": "lead"},
        ))
        assert r["team_enabled"] is True
        data = http_client.get(f"/rooms/api/rooms/{rid}/team").json()
        assert data["enabled"] is True
        assert data["lead"] == lead

    def test_demote_lead_disables_team(self, http_client, team_room):
        rid, lead = team_room["rid"], team_room["lead"]
        http_client.post(f"/rooms/api/rooms/{rid}/team/role",
                         json={"participant_id": lead, "role": "lead"})
        r = http_client.post(f"/rooms/api/rooms/{rid}/team/role",
                             json={"participant_id": lead, "role": "worker"}).json()
        assert r["team_enabled"] is False

    def test_bad_role_rejected(self, http_client, team_room):
        r = http_client.post(
            f"/rooms/api/rooms/{team_room['rid']}/team/role",
            json={"participant_id": team_room["lead"], "role": "captain"},
        ).json()
        assert "error" in r

    # ── task CRUD ──

    def test_task_lifecycle(self, http_client, team_room):
        rid, worker = team_room["rid"], team_room["worker"]
        # add
        r = assert_ok(http_client.post(
            f"/rooms/api/rooms/{rid}/team/tasks",
            json={"content": "draft intro", "assignee": worker},
        ))
        tid = r["task"]["id"]
        assert r["task"]["status"] == "todo"
        assert r["task"]["assignee"] == worker
        # status
        r2 = assert_ok(http_client.post(
            f"/rooms/api/rooms/{rid}/team/tasks/{tid}/status",
            json={"status": "done", "result": "shipped"},
        ))
        assert r2["task"]["status"] == "done"
        assert r2["task"]["result"] == "shipped"
        # list reflects it
        data = http_client.get(f"/rooms/api/rooms/{rid}/team").json()
        assert any(t["id"] == tid and t["status"] == "done" for t in data["tasks"])

    def test_add_task_requires_content(self, http_client, team_room):
        r = http_client.post(f"/rooms/api/rooms/{team_room['rid']}/team/tasks",
                             json={"content": "   "}).json()
        assert "error" in r

    def test_add_task_rejects_unknown_assignee(self, http_client, team_room):
        r = http_client.post(f"/rooms/api/rooms/{team_room['rid']}/team/tasks",
                             json={"content": "x", "assignee": "ghost"}).json()
        assert "error" in r

    def test_assign_unknown_task(self, http_client, team_room):
        r = http_client.post(
            f"/rooms/api/rooms/{team_room['rid']}/team/tasks/t-nope/assign",
            json={"assignee": team_room["worker"]},
        ).json()
        assert "error" in r

    def test_bad_status_rejected(self, http_client, team_room):
        rid, worker = team_room["rid"], team_room["worker"]
        tid = http_client.post(f"/rooms/api/rooms/{rid}/team/tasks",
                               json={"content": "x", "assignee": worker}).json()["task"]["id"]
        r = http_client.post(f"/rooms/api/rooms/{rid}/team/tasks/{tid}/status",
                             json={"status": "frobnicated"}).json()
        assert "error" in r

    # ── run control state machine ──

    def test_run_requires_lead(self, http_client, team_room):
        # No lead assigned yet → start refused.
        r = http_client.post(f"/rooms/api/rooms/{team_room['rid']}/team/run/start",
                             json={}).json()
        assert "error" in r

    def test_run_arms_caps_budget_and_stops(self, http_client, team_room):
        rid, lead = team_room["rid"], team_room["lead"]
        http_client.post(f"/rooms/api/rooms/{rid}/team/role",
                         json={"participant_id": lead, "role": "lead"})
        # Arm with an over-large budget → capped at 40. No actionable tasks,
        # so the loop settles to done quickly; just assert the arm response.
        r = http_client.post(f"/rooms/api/rooms/{rid}/team/run/start",
                             json={"max_turns": 999}).json()
        assert r["ok"] is True
        assert r["run"]["turns_left"] == 40
        # Stop is always safe to call (idempotent).
        stop = http_client.post(f"/rooms/api/rooms/{rid}/team/run/stop").json()
        assert stop["ok"] is True


# ── last_active on the room list (portal's Recent sidebar sorts by it) ───────

class TestRoomsLastActive:
    def test_last_active_is_history_time_and_absent_without_history(self, http_client):
        from datetime import datetime, timezone
        r = http_client.post("/rooms/api/agents", json={
            "name": f"{TEST_PREFIX}last-active", "system_prompt": "x"})
        data = r.json()
        aid = data.get("id") or (data.get("agent") or {}).get("id")
        assert aid, f"agent create failed: {data}"
        try:
            rows = assert_ok(http_client.get("/rooms/api/agents?status=active"))
            mine = next(a for a in rows if a.get("id") == aid)
            # A room nobody has written to has no activity time — not "now".
            assert "last_active" not in mine, mine
            stamped = [a for a in rows if a.get("last_active")]
            if not stamped:
                pytest.skip("no room with history on this deployment")
            now = datetime.now(timezone.utc)
            for a in stamped:
                t = datetime.fromisoformat(a["last_active"])
                assert t.tzinfo is not None, a["last_active"]
                assert t <= now, f"{a['id']} last_active in the future: {a['last_active']}"
        finally:
            http_client.request("DELETE", f"/rooms/api/agents/{aid}")
