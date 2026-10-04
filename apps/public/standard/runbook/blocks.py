"""Runbook — per-block-type executors.

Plain module functions taking the app as ``self`` (called by ``engine.py``, not
bound to the class). Imports the leaf utility layer (``shared``, ``calc``) only;
never imports ``engine`` or ``.app`` (acyclic).

Contract — ``execute_block(self, block, context)`` returns one of:
  * ``{"value": <v>, "type": <t>}``  — a computed result (non-side-effect)
  * ``{"action": {"app","method","args"}, "type": "text"}`` — a proposed
    side-effect for the engine to route through the review gate / grant check

Output ``type`` ∈ text | number | json | table | artifact-ref. Side-effecting
types (write_draft, notify, call_app w/ mutates) return an ``action`` so the
engine — not the block — decides gate (interactive) vs grant (scheduled).
"""

from __future__ import annotations

from . import calc as _calc
from . import shared as _shared


def _rendered_args(header_args: dict, context: dict) -> dict:
    """Render string arg values through the template; pass non-strings through."""
    out = {}
    for k, v in (header_args or {}).items():
        out[k] = _shared.render_template(v, context) if isinstance(v, str) else v
    return out


async def execute_block(self, block, context: dict) -> dict:
    fn = _DISPATCH.get(block.type)
    if fn is None:
        raise ValueError(f"unknown block type {block.type!r}")
    return await fn(self, block, context)


async def _vault_query(self, block, context: dict) -> dict:
    h = block.header
    tags = h.get("tags") or None
    if isinstance(tags, str):
        tags = [tags]
    if tags:
        tags = [_shared.render_template(t, context) if isinstance(t, str) else t for t in tags]
    folder = h.get("folder")
    if isinstance(folder, str):
        folder = _shared.render_template(folder, context) or None
    props = {}
    for k, v in (h.get("where") or {}).items():
        props[k] = _shared.render_template(v, context) if isinstance(v, str) else v
    rows = self.vault_query(tags=tags, folder=folder, **props)
    limit = h.get("limit")
    if isinstance(limit, int) and limit > 0:
        rows = rows[:limit]
    return {"value": rows, "type": "table"}


async def _calculate(self, block, context: dict) -> dict:
    names = {local: context.get(src) for local, src in block.inputs.items()}
    val = _calc.safe_eval(block.body, names)
    t = "number" if isinstance(val, (int, float)) and not isinstance(val, bool) else "json"
    return {"value": val, "type": t}


async def _think(self, block, context: dict) -> dict:
    prompt = _shared.render_template(block.body, context)
    domain = str(block.header.get("domain", "text"))
    out = await self.think(prompt, domain=domain, system=_shared.THINK_BLOCK_SYSTEM)
    return {"value": out, "type": "text"}


async def _call_app(self, block, context: dict) -> dict:
    app = str(block.header.get("app", "")).strip()
    method = str(block.header.get("method", "")).strip()
    if not app or not method:
        raise ValueError("call_app block requires 'app' and 'method'")
    args = _rendered_args(block.header.get("args") or {}, context)
    if block.side_effect:  # mutates=true → propose, don't execute
        return {"action": {"app": app, "method": method, "args": args}, "type": "text"}
    res = await self.call_app(app, method, **args)
    return {"value": res, "type": "json"}


async def _chart(self, block, context: dict) -> dict:
    prompt = _shared.render_template(block.body, context)
    shape = str(block.header.get("shape", "chart"))
    try:
        res = await self.call_app("viz", "generate", prompt=prompt, shape=shape)
    except Exception as e:  # noqa: BLE001 — viz is optional
        raise ValueError(f"viz unavailable: {e}") from e
    if not (isinstance(res, dict) and res.get("ok", True) and res.get("id")):
        raise ValueError(f"viz generation failed: {res}")
    return {"value": {"id": res.get("id"), "html_path": res.get("html_path")},
            "type": "artifact-ref"}


async def _write_draft(self, block, context: dict) -> dict:
    path = _shared.render_template(str(block.header.get("path", "")), context)
    if not path:
        raise ValueError("write_draft block requires 'path'")
    body = _shared.render_template(block.body, context)
    section = block.header.get("section")
    content = body
    if section:
        content = f"## {section}\n\n{body}\n"
    # Proposed as a rooms.write_note — the canonical gated, diff-captured write.
    return {"action": {"app": "rooms", "method": "write_note",
                       "args": {"path": path, "content": content}}, "type": "text"}


async def _notify(self, block, context: dict) -> dict:
    text = _shared.render_template(block.body, context)
    priority = str(block.header.get("priority", "info") or "info").strip()
    args: dict = {"text": text, "priority": priority}
    # Scheduled runs deliver while the user may be absent/asleep — mark the
    # action so send_notification routes it through the proactive gate
    # (quiet hours / caps / per-kind mute). Interactive runs omit the key:
    # the user Apply-clicks the pending card in real time, so raw delivery
    # is their explicit, present-tense consent.
    if context.get("mode") == "scheduled":
        args["scheduled"] = True
    return {
        "action": {
            "app": "runbook",
            "method": "send_notification",
            "args": args,
        },
        "type": "text",
    }


_DISPATCH = {
    "vault_query": _vault_query,
    "calculate": _calculate,
    "think": _think,
    "call_app": _call_app,
    "chart": _chart,
    "write_draft": _write_draft,
    "notify": _notify,
}
