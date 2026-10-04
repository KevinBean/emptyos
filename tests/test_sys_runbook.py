"""Runbook — daemon-side endpoint tests (requires :9000).

Drives the real app: create a runbook, run a no-side-effect pipeline, confirm a
write_draft block is NOT silently executed (routed to the gate), schedule it,
and that from-session drafts without writing until confirmed.
"""

import pytest

from helpers import TEST_PREFIX, assert_ok

_RID = TEST_PREFIX + "rb-calc"
_RID_SE = TEST_PREFIX + "rb-se"
# Runbook ids are slugified to lowercase on create; every {rid} route
# normalizes the same way, so requests may use _RID but responses carry
# the canonical form.
_RID_CANON = _RID.lower()

# A no-side-effect runbook: one calculate block over a literal (no LLM, no I/O).
_CALC_MD = '```eos-block\nid = "n"\ntype = "calculate"\noutput = "n"\n---\n2 + 3 * 4\n```'

# A runbook whose only block is a side-effecting write_draft.
_SE_MD = ('```eos-block\nid = "w"\ntype = "write_draft"\noutput = "w"\n'
          'path = "30_Resources/EmptyOS/runbook/outputs/%s/out.md"\n---\nhello\n```' % _RID_SE)


@pytest.mark.api
class TestRunbookCrudRun:
    def test_create_get_run(self, http_client):
        # create (idempotent-ish: ignore "already exists" from a prior run)
        http_client.post("/runbook/api/runbooks",
                         json={"id": _RID, "title": _RID, "markdown": _CALC_MD})
        got = assert_ok(http_client.get(f"/runbook/api/runbooks/{_RID}"))
        assert got["id"] == _RID_CANON
        assert any(b["type"] == "calculate" for b in got["blocks"])
        assert got.get("_vault_path")  # for the 4D timeline button

        run = assert_ok(http_client.post(f"/runbook/api/runbooks/{_RID}/run"))
        assert run["ok"] is True
        after = assert_ok(http_client.get(f"/runbook/api/runbooks/{_RID}"))
        nblock = next(b for b in after["blocks"] if b["id"] == "n")
        assert nblock["status"] == "ok"

    def test_list_includes_created(self, http_client):
        http_client.post("/runbook/api/runbooks",
                         json={"id": _RID, "title": _RID, "markdown": _CALC_MD})
        data = assert_ok(http_client.get("/runbook/api/runbooks"))
        assert any(r["id"] == _RID_CANON for r in data["runbooks"])


@pytest.mark.api
class TestSideEffectGated:
    def test_write_draft_not_silently_executed(self, http_client):
        http_client.post("/runbook/api/runbooks",
                         json={"id": _RID_SE, "title": _RID_SE, "markdown": _SE_MD})
        assert_ok(http_client.post(f"/runbook/api/runbooks/{_RID_SE}/run"))
        got = assert_ok(http_client.get(f"/runbook/api/runbooks/{_RID_SE}"))
        wblock = next(b for b in got["blocks"] if b["id"] == "w")
        # Interactive run must route through the gate (pending) or fail if the
        # gate is unavailable — never "ok" (which would mean a silent write).
        assert wblock["status"] in ("pending", "error", "needs-grant")
        assert wblock["status"] != "ok"


@pytest.mark.api
class TestSchedule:
    def test_schedule_registers_next_run(self, http_client):
        http_client.post("/runbook/api/runbooks",
                         json={"id": _RID, "title": _RID, "markdown": _CALC_MD})
        res = assert_ok(http_client.post(
            f"/runbook/api/runbooks/{_RID}/schedule", json={"cron": "0 8 * * 1"}))
        assert res.get("ok")
        assert res.get("next_run")  # APScheduler computed a next fire time
        # clear it so the test daemon doesn't keep a scheduled job around
        assert_ok(http_client.post(
            f"/runbook/api/runbooks/{_RID}/schedule", json={"cron": ""}))


@pytest.mark.api
@pytest.mark.llm
class TestFromSession:
    def test_draft_writes_nothing_until_confirm(self, http_client):
        rid = TEST_PREFIX + "rb-fromsess"
        transcript = ("user: count my expense notes this month and total them\n"
                      "assistant: I queried tag:expense and summed the amount field.")
        res = assert_ok(http_client.post(
            "/runbook/api/from-session", json={"id": rid, "transcript": transcript}))
        assert res.get("ok") and res.get("token") and "markdown" in res
        # Nothing written yet — the runbook must not exist before confirm.
        not_yet = http_client.get(f"/runbook/api/runbooks/{rid}").json()
        assert "error" in not_yet
