"""Capture destinations — the one table for "a captured thought → task | journal | kb".

Three surfaces route free-text capture into the same three destinations, and
each used to encode the app/method/arg-shaping itself:

* ``apps/public/core/quick-action`` — triage endpoints, **immediate** writes
  (``call_app`` now, undo after).
* ``apps/public/standard/braindump`` — typed-action extraction, **review-gated**
  proposals (nothing touches state before Apply).
* ``apps/public/standard/portal`` — hero-composer chips (frontend; hits the two
  apps above rather than reimplementing the table).

Extracted at the second backend consumer per CLAUDE.md rule 9. What is shared is
*only* the destination table and the two executors over it. The **posture** is
not shared and must not be: quick-action applies directly because its payload is
one line the user just typed; braindump proposes because an LLM inferred the
payload. Both read the same table so a destination's arg shape can never drift
between them.

Deliberately NOT in the table: quick-action's ``_TAG_ROUTE`` / ``_TAG_PROJECT``
tag→app/project routing. Those have a single consumer, so they stay in the app
(rule 9 is a floor, not a target).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

__all__ = [
    "CaptureDestination",
    "CAPTURE_DESTINATIONS",
    "capture_destination",
    "shape_capture_args",
    "apply_capture_direct",
    "propose_capture",
]

# How a destination reaches the review gate. `action` → BaseApp.propose_action;
# `kb_note` → BaseApp.propose_kb_note (a different helper with its own shape).
ProposeKind = Literal["action", "kb_note"]


@dataclass(frozen=True)
class CaptureDestination:
    """One capture destination: where it lands and how its args are shaped.

    ``shape`` maps a loose capture item to the target method's kwargs, or returns
    ``None`` when the item is empty/unroutable (the caller skips it silently —
    an empty task is not an error, it is nothing).
    """

    key: str
    verb: str                      # "<app>.<method>" — the label surfaces show
    app: str
    method: str
    shape: Callable[[dict], dict | None]
    propose_kind: ProposeKind = "action"
    label_field: str = "text"      # which shaped arg is the human summary


def _shape_task(item: dict) -> dict | None:
    text = (item.get("text") or "").strip()
    if not text:
        return None
    return {
        "text": text,
        "due": (item.get("due") or "").strip(),
        "project": (item.get("project") or "inbox").strip() or "inbox",
    }


def _shape_journal(item: dict) -> dict | None:
    # Collapse newlines: journal's single-line guard rejects natural speech that
    # an LLM (or a pasted dump) split across lines.
    text = " ".join((item.get("text") or "").split())
    if not text:
        return None
    return {"text": text, "mood": (item.get("mood") or "okay").lower()}


def _shape_kb(item: dict) -> dict | None:
    title = (item.get("title") or "").strip()
    if not title:
        return None
    return {
        "kind": (item.get("kind") or "lesson").lower(),
        "title": title,
        "body": (item.get("body") or "").strip(),
        "topic": (item.get("topic") or "").strip(),
    }


CAPTURE_DESTINATIONS: dict[str, CaptureDestination] = {
    "task": CaptureDestination(
        key="task", verb="task.add", app="task", method="add", shape=_shape_task,
    ),
    "journal": CaptureDestination(
        # Public verb, not journal's private `_add_entry` — the private call was
        # a quick-action-only shortcut and bypassed the newline normalization.
        key="journal", verb="journal.voice_add_entry", app="journal",
        method="voice_add_entry", shape=_shape_journal,
    ),
    "kb": CaptureDestination(
        key="kb", verb="kb.create_note", app="kb", method="create_note",
        shape=_shape_kb, propose_kind="kb_note", label_field="title",
    ),
}


def capture_destination(key: str) -> CaptureDestination | None:
    """Look up a destination by its type key (`task` / `journal` / `kb`)."""
    return CAPTURE_DESTINATIONS.get((key or "").strip().lower())


def shape_capture_args(key: str, item: dict) -> dict | None:
    """Shape a loose capture item into the destination's kwargs, or None to skip."""
    dest = capture_destination(key)
    if dest is None or not isinstance(item, dict):
        return None
    return dest.shape(item)


async def apply_capture_direct(app: Any, key: str, item: dict) -> dict:
    """Write the capture to its destination **now**, via ``call_app``.

    The immediate-write posture: the user typed this payload themselves, so the
    safety net is audit + undo after, not approval before (CLAUDE.md north star).
    Returns ``{"ok": True, "verb": ..., "result": ...}`` or ``{"error": ...}``.
    """
    dest = capture_destination(key)
    if dest is None:
        return {"error": f"unknown capture destination '{key}'"}
    args = dest.shape(item) if isinstance(item, dict) else None
    if args is None:
        return {"error": "nothing to capture"}
    # kb's direct path takes the same shaped kwargs as its propose path.
    try:
        result = await app.call_app(dest.app, dest.method, **args)
    except Exception as e:
        return {"error": f"{dest.verb} failed: {type(e).__name__}: {e}"}
    if isinstance(result, dict) and result.get("error"):
        return {"error": f"{dest.app} declined: {result['error']}"}
    return {"ok": True, "verb": dest.verb, "result": result}


async def propose_capture(app: Any, key: str, item: dict, *, source: str = "") -> dict | None:
    """File the capture into the review gate; **nothing touches state** until Apply.

    Returns the UI row ``{action_id, type, verb, summary}``, or ``None`` when the
    item is empty or the propose call failed (one bad item never sinks the batch).
    """
    dest = capture_destination(key)
    if dest is None:
        return None
    args = dest.shape(item) if isinstance(item, dict) else None
    if args is None:
        return None
    try:
        if dest.propose_kind == "kb_note":
            saved = await app.propose_kb_note(source=source, **args)
        else:
            saved = await app.propose_action(app=dest.app, method=dest.method, args=args)
    except Exception:
        # A failed propose drops that one item; the rest still surface.
        return None
    return {
        "action_id": (saved or {}).get("id", ""),
        "type": dest.key,
        "verb": dest.verb,
        "summary": str(args.get(dest.label_field, ""))[:120],
    }
