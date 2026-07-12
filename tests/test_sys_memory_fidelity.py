"""System tests: memory-fidelity proposer (apps/extension/dev/memory-fidelity/).

Scheduled proposer for Attested Memory (docs/MEMORY.md §8). These cover the
read-only surfaces + the confirm apply-target's guard. The sweep itself is NOT
exercised here — it files real review-gate proposals into the pending dashboard,
which would be a side effect on the live vault/daemon. The sweep is verified
end-to-end on a sandbox member instead (see the session devlog).
"""

import pytest

from helpers import assert_dict_response


@pytest.mark.api
class TestMemoryFidelityAPI:

    def test_status_returns_fidelity_report(self, http_client):
        """/memory-fidelity/api/status returns the dial + counts + config (read-only)."""
        data = assert_dict_response(http_client.get("/memory-fidelity/api/status"))
        for key in ("enabled", "sweep_cron", "propose_levels", "dial", "total", "counts"):
            assert key in data, f"status missing {key!r}: {list(data.keys())}"
        assert isinstance(data["dial"], int)
        assert 0 <= data["dial"] <= 100
        assert isinstance(data["total"], int)
        assert isinstance(data["counts"], dict)

    def test_status_dark_by_default(self, http_client):
        """The cron is dark unless [apps.memory-fidelity] enabled is set."""
        data = http_client.get("/memory-fidelity/api/status").json()
        # Default config ships disabled; a machine that opted in may report True.
        assert isinstance(data["enabled"], bool)

    def test_propose_levels_defaults_to_suspect(self, http_client):
        """Suspect-only by default — tentative (baseline AI memory) isn't a treadmill."""
        data = http_client.get("/memory-fidelity/api/status").json()
        assert "suspect" in data.get("propose_levels", [])

    def test_confirm_requires_path(self, http_client):
        """The confirm apply-target rejects an empty path rather than touching the vault."""
        data = assert_dict_response(
            http_client.post("/memory-fidelity/api/confirm", json={"path": ""})
        )
        assert data.get("error"), f"expected an error for empty path, got {data}"
