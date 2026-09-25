"""Unit tests for emptyos.sdk.diff_proposal.DiffProposalStore.

Pure, no daemon required. Exercises the simple propose/apply/reject lifecycle
that the assistant reconcile endpoints use.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from emptyos.sdk.diff_proposal import DiffProposalStore


def test_apply_discards_captured_sandbox_after_success():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        vault = root / "vault"
        vault.mkdir(parents=True, exist_ok=True)
        target = vault / "notes" / "fact.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("before\n", encoding="utf-8")

        store = DiffProposalStore(root / "sandboxes")
        res = store.propose("act-apply01", vault, "notes/fact.md", "after\n")
        assert res["ok"] is True
        sandbox_dir = store.sandbox_root / "act-apply01"
        assert sandbox_dir.exists()

        applied = store.apply("act-apply01")
        assert applied == {"ok": True, "target": str(target)}
        assert target.read_text(encoding="utf-8") == "after\n"
        assert not sandbox_dir.exists()
        assert store.get("act-apply01") is None
