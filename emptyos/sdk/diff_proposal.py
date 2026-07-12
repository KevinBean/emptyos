"""diff_proposal — stage→preview→apply vault-file edits as SandboxedWrite diffs.

A thin **return-based** propose/preview/apply/reject lifecycle over the
`SandboxedWrite` primitive (`emptyos/sdk/sandbox.py`), so a consumer with simple
single-edit endpoints doesn't re-roll the capture + load + error-handling
boilerplate. First consumer: the assistant reconcile ripple
(`apps/public/standard/assistant/reconcile.py`).

NOT used by the rooms review-gate, deliberately: rooms threads a *raise-based,
multi-change* apply (iterate `proposed_changes`, `sw.apply()` raising
`StaleSandbox`, discard-after, status written back into the pending-action JSON)
that doesn't fit this store's flat `{ok, error}` apply. Both correctly share the
`SandboxedWrite` *primitive*; only the simple lifecycle is shared here. A second
*simple* consumer can adopt this store directly.

The diff IS the gate — these proposals are never auto-applied
(`.claude/rules/proposed-action.md`).
"""

from __future__ import annotations

from pathlib import Path

from emptyos.sdk.sandbox import SandboxedWrite, StaleSandbox, load_sandbox


class DiffProposalStore:
    """Per-app store of pending vault-file diffs under one sandbox root."""

    def __init__(self, sandbox_root: "Path | str"):
        self.sandbox_root = Path(sandbox_root)
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

    def capture(self, action_id: str, vault_root, rel_path: str, content: str) -> SandboxedWrite:
        """Snapshot the target + stage the proposed content; return the live
        SandboxedWrite (callers that need ``diff_lines()`` / to stash the sw).
        Raises ``ValueError`` on a path that escapes the vault."""
        sw = SandboxedWrite(action_id, vault_root, rel_path, content, self.sandbox_root)
        sw.capture()
        return sw

    def propose(self, action_id: str, vault_root, rel_path: str, content: str) -> dict:
        """Capture + return ``{ok, action_id, diff}`` (or ``{ok: False, error}``)."""
        try:
            sw = self.capture(action_id, vault_root, rel_path, content)
        except ValueError as e:
            return {"ok": False, "error": str(e)[:200]}
        except Exception as e:  # noqa: BLE001 — surface capture failure, never raise
            return {"ok": False, "error": f"capture failed: {e!s:.200}"}
        return {"ok": True, "action_id": action_id, "diff": sw.diff_lines()}

    def apply(self, action_id: str) -> dict:
        """Replay a captured diff onto the vault (staleness-checked). Returns
        ``{ok, target}`` or ``{ok: False, error}`` (``"not found"`` / ``"stale"``)."""
        sw = load_sandbox(action_id, self.sandbox_root)
        if not sw:
            return {"ok": False, "error": "not found"}
        try:
            target = sw.apply()
        except StaleSandbox:
            return {"ok": False, "error": "stale"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        sw.discard()
        return {"ok": True, "target": str(target)}

    def reject(self, action_id: str) -> dict:
        """Discard a captured diff. Idempotent — missing action_id is a no-op."""
        sw = load_sandbox(action_id, self.sandbox_root)
        if sw:
            sw.discard()
        return {"ok": True}

    def get(self, action_id: str) -> "SandboxedWrite | None":
        """Load a captured proposal (for consumers that drive apply themselves)."""
        return load_sandbox(action_id, self.sandbox_root)
