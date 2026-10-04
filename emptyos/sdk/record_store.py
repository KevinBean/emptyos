"""A directory of id-keyed JSON records — one guarded implementation.

Sixteen apps keep a queue or registry as ``<dir>/<id>.json``: rooms and
voice-assistant pending actions, promote proposals and campaigns, rag-eval
regression proposals, grill sessions, cad drafts. Each re-implemented the same
four operations, and the copies did not agree on the two that matter.

**Path safety.** ``_pending_path`` in rooms and voice-assistant built
``dir / f"{action_id}.json"`` from a caller-supplied route parameter with no
guard. Starlette's ``{param}`` excludes ``/`` but not ``\\``, and on Windows a
backslash is a separator — so a URL-encoded ``..\\`` in the id escaped the
queue directory. Measured on a sandbox daemon 2026-08-08, not inferred:
``POST /rooms/api/pending/..%5CCANARY/edit`` loaded a file one directory up
**and wrote to it**, stamping ``edited: true`` on a record that was never a
pending action. The 2026-07-19 traversal audit that produced
``require_path_segment`` swept eight consumers and missed these.

**Crash safety.** Both pending stores wrote with a plain ``write_text``. That
undermines the promise ``pending_claim`` is built on — the claim is persisted
inside the lock precisely so it survives a crash — because a torn write leaves
JSON that ``json.loads`` rejects, the tolerant loader turns into ``None``, and
the action disappears from the queue instead of surfacing as the honest
``approving`` state that ``resolve_unknown`` exists to adjudicate.

So this is a platform fix wearing a deduplication's clothes
(``feedback_platform_fix_for_n_app_bugs``): the same defect in three or more
apps belongs in one place. ``pending_claim``'s own docstring named this
refactor and deferred it — "normalising those is a bigger refactor than this
fix"; this is that normalisation.

Read and write are deliberately asymmetric:

- ``path`` and ``save`` **raise** on an unsafe id. They are the write
  choke-point, and a caller that has smuggled a separator into an id should
  fail loudly rather than write somewhere surprising.
- ``load``, ``exists`` and ``delete`` **soft-miss** to ``None``/``False``. A
  lookup for a malformed id is a lookup that found nothing, which is what
  every existing caller's ``dict | None`` contract already says.

What this does NOT do: serialize read-modify-write. Atomic replacement stops a
reader seeing half a file; two writers still race. Approval queues must keep
holding their lock and persisting the claim inside it — see
``emptyos/sdk/pending_claim.py`` and ``.claude/rules/atomic-persistence.md``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from emptyos.runtime.atomic_io import atomic_write_text

from .utils import require_path_segment, safe_path_segment


class JsonRecordStore:
    """``<directory>/<id>.json`` records with guarded paths and atomic writes.

    ``label`` names the id in refusal messages ("action id", "proposal id").
    ``pattern`` narrows :meth:`records` when a directory holds more than one
    kind of file (promote's campaigns glob ``camp-*.json``).
    """

    def __init__(self, directory: Path | str, *, label: str = "id", pattern: str = "*.json") -> None:
        self.directory = Path(directory)
        self.label = label
        self.pattern = pattern

    # ── paths ───────────────────────────────────────────────────────

    def path(self, record_id: str) -> Path:
        """Resolve an id to its file. Raises ``ValueError`` on an unsafe id."""
        return self.directory / f"{require_path_segment(record_id, self.label)}.json"

    def _safe_path(self, record_id: str) -> Path | None:
        safe = safe_path_segment(record_id)
        return (self.directory / f"{safe}.json") if safe else None

    # ── write ───────────────────────────────────────────────────────

    def save(self, record: dict, *, id_key: str = "id") -> Path:
        """Write a record atomically, keyed by ``record[id_key]``."""
        path = self.path(str(record.get(id_key, "")))
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(record, indent=2, ensure_ascii=False))
        return path

    def delete(self, record_id: str) -> bool:
        """Remove a record. False when the id is unsafe or nothing was there."""
        path = self._safe_path(record_id)
        if path is None or not path.exists():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True

    # ── read (tolerant — a bad id or a torn file reads as absent) ────

    def load(self, record_id: str) -> dict | None:
        path = self._safe_path(record_id)
        if path is None or not path.exists():
            return None
        return _read(path)

    def exists(self, record_id: str) -> bool:
        path = self._safe_path(record_id)
        return bool(path and path.exists())

    def records(self) -> Iterator[dict]:
        """Every parseable record. Unreadable files are skipped, not raised —
        one corrupt entry must not blank a review queue."""
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob(self.pattern)):
            record = _read(path)
            if record is not None:
                yield record

    def list(self) -> list[dict]:
        return list(self.records())


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
