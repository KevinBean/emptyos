"""RunRegistry — per-run scratchpad + state for harness-shaped apps.

Three apps converged on the same shape: each "run" is a stable id, a
directory of intermediate artifacts, and a small JSON state file. dogfood-agent,
staff, and model-bench all hand-rolled it slightly differently. This is the
shared minimum.

Out of scope: phase orchestration, validation gates, sub-agent fan-out,
resume-from-phase. None of those are shared across the three apps yet — pull
them in only if a 4th harness app needs them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def _mint_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


@dataclass(frozen=True)
class RunHandle:
    """One run. Knows its id, its directory, and how to read/write its state."""

    run_id: str
    dir: Path
    _state_filename: str = "run.json"

    def write_state(self, data: dict, *, name: str | None = None) -> None:
        target = self.dir / (name or self._state_filename)
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def read_state(self, *, name: str | None = None) -> dict | None:
        target = self.dir / (name or self._state_filename)
        if not target.exists():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return None

    def artifact(self, name: str) -> Path:
        """Path inside the run dir. Caller does the I/O — bytes, JSONL, markdown,
        whatever. The registry doesn't care what's in there."""
        return self.dir / name

    def write_artifact(self, name: str, content: str | bytes) -> Path:
        p = self.artifact(name)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
        return p

    def read_artifact(self, name: str) -> str | None:
        p = self.artifact(name)
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8", errors="replace")


class RunRegistry:
    """Directory of runs under a single base path.

    Layout:
        {base}/
        ├── 20260510T143022-abc123/
        │   ├── run.json
        │   └── <whatever artifacts the app writes>
        ├── 20260510T143155-def456/
        │   └── ...

    Usage:
        runs = RunRegistry(self.data_dir / "runs")
        handle = runs.new()
        handle.write_state({"scenario": "foo", "status": "running"})
        handle.write_artifact("stream.jsonl", b"...")
        ...
        for handle, state in runs.recent_states(n=50):
            if state.get("status") == "error": ...
    """

    def __init__(self, base: Path, *, state_filename: str = "run.json"):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self._state_filename = state_filename

    def new(self, run_id: str | None = None) -> RunHandle:
        """Mint a new run. Caller may pass an explicit run_id (e.g. for tests
        or scenario+timestamp hashes); otherwise a UTC-timestamp + uuid suffix."""
        rid = run_id or _mint_run_id()
        d = self.base / rid
        d.mkdir(parents=True, exist_ok=True)
        return RunHandle(run_id=rid, dir=d, _state_filename=self._state_filename)

    def get(self, run_id: str) -> RunHandle | None:
        d = self.base / run_id
        if not d.is_dir():
            return None
        return RunHandle(run_id=run_id, dir=d, _state_filename=self._state_filename)

    def __contains__(self, run_id: str) -> bool:
        return (self.base / run_id).is_dir()

    def recent(self, n: int | None = 50) -> Iterator[RunHandle]:
        """Most recent runs first (lex sort = chrono since run_ids are timestamped).
        Pass n=None for unbounded."""
        paths = sorted(
            (p for p in self.base.glob(f"*/{self._state_filename}")),
            reverse=True,
        )
        if n is not None:
            paths = paths[:n]
        for p in paths:
            yield RunHandle(run_id=p.parent.name, dir=p.parent, _state_filename=self._state_filename)

    def recent_states(
        self, n: int | None = 50, *, status: str | None = None
    ) -> Iterator[tuple[RunHandle, dict]]:
        """Recent runs with their state preloaded. Skips unreadable state files
        silently (a half-written run.json shouldn't crash callers). Optional
        `status` filter matches state['status']."""
        for handle in self.recent(n):
            state = handle.read_state()
            if state is None:
                continue
            if status is not None and state.get("status") != status:
                continue
            yield handle, state
