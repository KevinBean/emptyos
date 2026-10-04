"""Context Packing — reversible original storage.

Each compressed original is written to::

    <store_root>/refs/<YYYY-MM-DD>/ctx_<id>.json

so EmptyOS (not an external provider) can recover the exact text behind a
``ctx_*`` summary until its TTL expires. Refs are LOCAL-ONLY — for retrieval,
auditing, and follow-up tool calls, never provider-side hidden memory.

TTL is lazy-reap-on-read (the autopilot-grant pattern, ``sdk/autopilot.py``):
expired refs are deleted when a read misses or sweeps past them. No boot or
scheduled job in v1.

This module touches the filesystem; callers on an async path must invoke the
packer (which uses this) via ``asyncio.to_thread`` — see the Async Boundary
section of ``docs/CONTEXT-PACKING.md``.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .blocks import OriginalRef

REFS_DIR = "refs"
DEFAULT_TTL_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_ref_id() -> str:
    """Mint a short, collision-resistant ref id, e.g. ``ctx_3f9 a1b2c3``."""
    return "ctx_" + secrets.token_hex(5)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _is_expired(record: dict) -> bool:
    """Birth timestamp + TTL, via the shared helper (see is_past_ttl).

    Behaviour preserved, including the `>=` boundary and failing open on a bad
    timestamp or TTL. What changes: `created_at` values that are date-only or
    naive now parse instead of quietly meaning "never expires".
    """
    from emptyos.sdk.utils import is_past_ttl

    ttl = record.get("ttl_hours", DEFAULT_TTL_HOURS)
    try:
        ttl_seconds = float(ttl) * 3600.0
    except (TypeError, ValueError):
        return False
    return is_past_ttl(str(record.get("created_at") or ""), ttl_seconds, _now())


def save_original(
    store_root: Path,
    *,
    source: str,
    kind: str,
    text: str,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> OriginalRef:
    """Persist an original and return its :class:`OriginalRef`.

    Raises only on a genuine filesystem failure; the packer wraps this in its
    fail-open contract so a write error degrades to verbatim output.
    """
    ref_id = new_ref_id()
    day = _now().strftime("%Y-%m-%d")
    day_dir = Path(store_root) / REFS_DIR / day
    day_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "id": ref_id,
        "created_at": _now().isoformat(),
        "source": source,
        "kind": kind,
        "sha256": _sha256(text),
        "original_text": text,
        "ttl_hours": ttl_hours,
        "privacy": "local-only",
    }
    (day_dir / f"{ref_id}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    return OriginalRef(
        id=ref_id, source=source, kind=kind, sha256=record["sha256"], ttl_hours=ttl_hours
    )


def load_original(store_root: Path, ref_id: str) -> str | None:
    """Return the exact original text for ``ref_id``, or None if missing/expired.

    Expired records are deleted on read (lazy reap). Returns None — never
    raises — for an unknown id, a malformed file, or an expired ref.
    """
    if not ref_id or not ref_id.startswith("ctx_"):
        return None
    refs_root = Path(store_root) / REFS_DIR
    if not refs_root.exists():
        return None
    for path in refs_root.glob(f"*/{ref_id}.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if _is_expired(record):
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return record.get("original_text")
    return None
