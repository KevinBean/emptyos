"""Agent mailbox — durable, addressed agent→agent work handoff.

The gap this fills (see the audit in this session's plan): every existing
mechanism misses "durably deposit an addressed work-item into a specific
agent's inbox, delivered the next time that agent runs":

  • the event bus is fire-and-forget — an event with no live subscriber is
    lost (its DB row is an audit log, not a delivery queue);
  • ``emit_assignment`` is a people-app workload *index*, not delivery;
  • ``fix_queue`` is a durable file queue but welded to fix-agent's parse
    contract and single-consumer;
  • rooms team tasks are stranded when a run's budget expires.

``AgentMailbox`` is the missing piece: one JSON file per message under
``<data_root>/mailbox/<recipient>/<msg_id>.json``. The **recipient decides when
to drain** — a staff agent at shift start, a rooms team run at start — so the
mailbox is just durable, per-recipient storage with enqueue / list_pending /
mark_delivered. It generalizes the fix_queue *shape* (dir + file-per-item +
lifecycle) minus the fix-agent content contract, plus a recipient axis.

Pure: stdlib only, no kernel. **Fail-soft** — a mailbox read/write must never
break the sender. Agent telemetry (operational bookkeeping) → ``data/``, never
the vault (CLAUDE.md § Storage & Vault).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(s: str) -> str:
    """Path-safe recipient/id segment (mirrors the sanitization other stores use)."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s)) or "_"


def mailbox_message(
    *,
    recipient: str,
    sender: str = "",
    kind: str = "",
    subject: str = "",
    payload: "dict | None" = None,
    ts: "str | None" = None,
) -> dict:
    """Build the canonical message record. One shape, shared by the writer and
    any consumer, so the on-disk layout never drifts."""
    stamp = ts or _now_iso()
    return {
        "id": "msg-" + uuid.uuid4().hex[:10],
        "ts": stamp,
        "recipient": str(recipient),
        "sender": str(sender or ""),
        "kind": str(kind or ""),
        "subject": str(subject or "")[:300],
        "payload": payload if isinstance(payload, dict) else {},
        "status": "pending",
        "delivered_ts": None,
    }


class AgentMailbox:
    """Per-recipient durable inbox at ``<data_root>/mailbox/<recipient>/``.

    One JSON file per message. Reads tolerate malformed files (a truncated write
    is skipped, not fatal). ``mark_delivered`` flips a message's status in place
    (kept for audit); ``list_pending`` filters on it.
    """

    def __init__(self, data_root: str | Path):
        self.root = Path(data_root) / "mailbox"

    def _dir(self, recipient: str) -> Path:
        return self.root / _safe_id(recipient)

    def enqueue(
        self,
        recipient: str,
        *,
        sender: str = "",
        kind: str = "",
        subject: str = "",
        payload: "dict | None" = None,
    ) -> "dict | None":
        """Deposit a work-item into ``recipient``'s inbox. Returns the saved
        record, or None fail-soft."""
        try:
            rec = mailbox_message(
                recipient=recipient, sender=sender, kind=kind,
                subject=subject, payload=payload,
            )
            d = self._dir(recipient)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{rec['id']}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return rec
        except Exception:
            return None

    def _read_all(self, recipient: str) -> list[dict]:
        out: list[dict] = []
        d = self._dir(recipient)
        if not d.exists():
            return out
        try:
            for f in d.glob("msg-*.json"):
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
        except Exception:
            return out
        out.sort(key=lambda r: r.get("ts", ""))
        return out

    def list_pending(self, recipient: str) -> list[dict]:
        """Undelivered messages for ``recipient``, oldest first."""
        return [r for r in self._read_all(recipient) if r.get("status") != "delivered"]

    def list_all(self, recipient: str) -> list[dict]:
        """Every message for ``recipient`` (incl. delivered), oldest first."""
        return self._read_all(recipient)

    def mark_delivered(self, recipient: str, msg_id: str) -> bool:
        """Flip a message to delivered (kept on disk for audit). Returns True if
        a pending message was updated."""
        try:
            p = self._dir(recipient) / f"{_safe_id(msg_id)}.json"
            if not p.exists():
                return False
            rec = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(rec, dict) or rec.get("status") == "delivered":
                return False
            rec["status"] = "delivered"
            rec["delivered_ts"] = _now_iso()
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    def count_pending(self, recipient: str) -> int:
        """Cheap pending count for badges/panels."""
        return len(self.list_pending(recipient))

    def recipients(self) -> list[str]:
        """Every recipient that has (or had) a mailbox — for a dashboard."""
        try:
            if not self.root.exists():
                return []
            return sorted(p.name for p in self.root.iterdir() if p.is_dir())
        except Exception:
            return []
