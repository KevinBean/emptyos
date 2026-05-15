"""SandboxedWrite — capture a proposed vault file write into an isolated dir,
expose a unified diff for review, and replay the write only on explicit apply.

Used by the rooms review-gate's `[DO:rooms.write_note(...)]` verb: when a CLI
participant proposes a vault edit, the content lands here first; the pending
action card surfaces the diff; the user clicks Apply (or Reject) before any
real vault file changes.

The pattern is intentionally simpler than `apps/fix-agent/`'s git worktree —
vault writes are arbitrary file content, not commits, and a per-action temp
dir is cheaper than a branch + worktree per edit.

Lives in `emptyos/sdk/` as an exception to CLAUDE.md rule #9 ("extract to
sdk/ when a second app needs it"). Rooms is currently the sole consumer,
but moving the module into `apps/rooms/` breaks `tests/test_sys_rooms_logic.py`
which loads `app.py` via `spec_from_file_location` without parent-package
context (relative imports fail). The cost of fighting Python's import
machinery outweighs the cost of a slightly-early SDK placement; the module
is well-bounded, type-clean, and the surface ports cleanly if a second
consumer ever lands.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import unified_diff
from pathlib import Path


class StaleSandbox(Exception):
    """Raised on apply() when the on-disk vault file has changed since the
    proposed content was captured. The CLI must regenerate from current
    state — silently overwriting would lose whatever moved underneath."""


@dataclass
class DiffLine:
    """One row in the rendered diff. `kind` ∈ {"ctx","add","del","hunk"}."""
    kind: str
    text: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text}


class SandboxedWrite:
    """Stage one proposed vault file write under
    ``<sandbox_root>/<action_id>/`` until the user applies or rejects it.

    Layout on disk::

        <sandbox_root>/<action_id>/
            meta.json       # {rel_path, captured_at, before_existed}
            before          # vault content at capture time (absent if new file)
            after           # proposed content

    The on-disk ``before`` bytes are the source of truth for the staleness
    check at apply time — if the vault file no longer matches, we refuse
    rather than clobbering whatever changed underneath.
    """

    def __init__(
        self,
        action_id: str,
        vault_root: Path,
        rel_path: str,
        content: str,
        sandbox_root: Path,
    ):
        self.action_id = action_id
        self.vault_root = Path(vault_root).resolve()
        self.rel_path = rel_path
        self.content = content
        self.sandbox_root = Path(sandbox_root)
        self.dir = self.sandbox_root / action_id

        # Path traversal guard. Reject any rel_path that escapes vault_root
        # or is absolute. Resolve first, then assert the prefix relation.
        candidate = (self.vault_root / rel_path).resolve()
        try:
            candidate.relative_to(self.vault_root)
        except ValueError as e:
            raise ValueError(
                f"rel_path {rel_path!r} resolves outside vault_root"
            ) from e
        self.target = candidate

    # --- Capture --------------------------------------------------------

    def capture(self) -> None:
        """Snapshot the current vault file (if any) + persist proposed content.
        Idempotent on the same SandboxedWrite instance; overwrites on a second
        call with the same action_id."""
        self.dir.mkdir(parents=True, exist_ok=True)
        before_existed = self.target.exists()
        if before_existed:
            shutil.copy2(self.target, self.dir / "before")
        else:
            # Leave no `before` file; meta.before_existed=False is the marker.
            stale = self.dir / "before"
            if stale.exists():
                stale.unlink()
        (self.dir / "after").write_text(self.content, encoding="utf-8")
        meta = {
            "action_id": self.action_id,
            "rel_path": self.rel_path,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "before_existed": before_existed,
            "vault_root": str(self.vault_root),
            "target": str(self.target),
        }
        (self.dir / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

    # --- Inspect --------------------------------------------------------

    def _before_text(self) -> str:
        p = self.dir / "before"
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8")

    def _after_text(self) -> str:
        return (self.dir / "after").read_text(encoding="utf-8")

    def _before_existed(self) -> bool:
        meta_p = self.dir / "meta.json"
        if not meta_p.exists():
            return (self.dir / "before").exists()
        try:
            return bool(json.loads(meta_p.read_text(encoding="utf-8")).get(
                "before_existed", False
            ))
        except Exception:
            return (self.dir / "before").exists()

    def diff(self) -> str:
        """Unified diff string, suitable for raw display or saving to a log."""
        before = self._before_text().splitlines(keepends=True)
        after = self._after_text().splitlines(keepends=True)
        label_a = "/dev/null" if not self._before_existed() else f"a/{self.rel_path}"
        label_b = f"b/{self.rel_path}"
        return "".join(unified_diff(before, after, fromfile=label_a, tofile=label_b))

    def diff_lines(self) -> list[dict]:
        """Structured diff for frontend rendering.

        Each entry is `{"kind": "ctx"|"add"|"del"|"hunk", "text": "..."}`.
        Header lines (`--- a/...`, `+++ b/...`) are dropped — the card
        already shows the path. Hunk markers are kept so long diffs can be
        visually segmented.
        """
        raw = self.diff()
        out: list[dict] = []
        for line in raw.splitlines():
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("@@"):
                kind = "hunk"
            elif line.startswith("+"):
                kind = "add"
            elif line.startswith("-"):
                kind = "del"
            else:
                kind = "ctx"
            out.append(DiffLine(kind=kind, text=line).as_dict())
        return out

    # --- Resolve --------------------------------------------------------

    def apply(self) -> Path:
        """Replay the proposed write onto the real vault file. Raises
        ``StaleSandbox`` if the on-disk content no longer matches what was
        captured — the CLI must regenerate against current state.
        Returns the resolved target path on success."""
        captured_before = self._before_text()
        captured_existed = self._before_existed()
        on_disk = (
            self.target.read_text(encoding="utf-8") if self.target.exists() else ""
        )
        on_disk_existed = self.target.exists()
        if on_disk_existed != captured_existed or on_disk != captured_before:
            raise StaleSandbox(
                f"vault file {self.rel_path!r} changed since sandbox was captured"
            )
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text(self._after_text(), encoding="utf-8")
        # Sandbox dir kept for audit until the action card is finalized;
        # the caller should discard() once the pending entry transitions to
        # "applied" + the user has seen the result.
        return self.target

    def discard(self) -> None:
        """Remove the sandbox dir. Safe to call on a missing dir."""
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)


def load_sandbox(action_id: str, sandbox_root: Path) -> SandboxedWrite | None:
    """Reconstruct a SandboxedWrite from a captured directory. Returns None
    if the directory or meta.json is missing — apply()/discard() callers
    must handle that and not assume the sandbox still exists."""
    d = Path(sandbox_root) / action_id
    meta_p = d / "meta.json"
    if not meta_p.exists():
        return None
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except Exception:
        return None
    rel_path = meta.get("rel_path")
    vault_root = meta.get("vault_root")
    if not rel_path or not vault_root:
        return None
    after_p = d / "after"
    content = after_p.read_text(encoding="utf-8") if after_p.exists() else ""
    sw = SandboxedWrite(
        action_id=action_id,
        vault_root=Path(vault_root),
        rel_path=rel_path,
        content=content,
        sandbox_root=Path(sandbox_root),
    )
    return sw
