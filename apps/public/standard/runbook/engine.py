"""Runbook — execution engine (bound onto RunbookApp).

Orchestrates a runbook's typed-block pipeline: build context → run block →
record status/provenance in the sidecar → emit. Owns staleness (an
upstream-output-changed-since check over the inputs DAG), the interactive
review-gate vs scheduled-grant routing for side-effecting blocks, the 4D
timeline handler, and the hub panel.

Bound to RunbookApp in app.py. Imports the leaf utility layer
(shared/calc/runs/blocks) + the autopilot SDK; never imports `.app`.

# ─── Bind to RunbookApp class as ─────────────────────────────────────
#   _run_store           = _engine._run_store
#   _runbook_rel         = _engine._runbook_rel
#   _load_blocks         = _engine._load_blocks
#   _producer_map        = _engine._producer_map
#   _build_context       = _engine._build_context
#   run_block            = _engine.run_block
#   run_all              = _engine.run_all
#   run_from             = _engine.run_from
#   block_statuses       = _engine.block_statuses
#   send_notification    = _engine.send_notification
#   runbook_timeline     = _engine.runbook_timeline
#   panel_runbook_status = _engine.panel_runbook_status
# Adding a method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import autopilot as _autopilot

from . import blocks as _blocks
from . import shared as _shared
from .runs import RunStore

if TYPE_CHECKING:
    from .app import RunbookApp  # noqa: F401 — for type hints only


def _run_store(self) -> RunStore:
    return RunStore(Path(self.data_dir))


def _runbook_rel(self, runbook_id: str) -> str:
    return f"{_shared.RUNBOOK_DIR}/{runbook_id}.md"


def _load_blocks(self, runbook_id: str) -> list:
    body = self.vault_read_body(_runbook_rel(self, runbook_id)) or ""
    return _shared.parse_blocks(body)


def _producer_map(self, blocks: list) -> dict:
    """output name -> block id that produces it."""
    return {b.output: b.id for b in blocks if b.output}


def _now_context() -> dict:
    d = datetime.now().date().isoformat()
    return {"date": d, "month": d[:7]}


def _build_context(self, runbook_id: str, blocks: list, upto_index: int) -> dict:
    """Context for the block at upto_index: every prior block's output, pulled
    from the in-run accumulator if present else the sidecar cache."""
    store = _run_store(self)
    outputs: dict = {}
    for b in blocks[:upto_index]:
        if not b.output:
            continue
        cached = store.cached_output(runbook_id, b.id)
        if cached is not None:
            outputs[b.output] = cached
    return {**outputs, "outputs": outputs, "now": _now_context()}


async def run_block(self, runbook_id: str, block, context: dict, *, mode: str = "interactive") -> dict:
    """Execute one block, record state, return the new block-state dict.

    Side-effecting blocks return an action descriptor from the executor; this
    function routes it: interactive → rooms review gate (pending); scheduled →
    autopilot grant required, else no-op with status 'needs-grant'."""
    store = _run_store(self)
    if block.type == "invalid":
        st = {"status": "error", "error": block.parse_error, "type": "text"}
        store.set_block(runbook_id, block.id, **st)
        return st

    # Block depending on an unavailable upstream output. Distinguish a genuine
    # failure ('blocked') from an upstream merely awaiting a review/grant
    # ('awaiting-upstream'), so a pending pipeline doesn't read as broken.
    deferred = context.get("deferred") or set()
    for local, src in block.inputs.items():
        if src not in context.get("outputs", {}):
            if src in deferred:
                st = {"status": "awaiting-upstream", "type": "text",
                      "error": f"upstream '{src}' awaits review/grant"}
            else:
                st = {"status": "blocked", "type": "text",
                      "error": f"upstream '{src}' not available"}
            store.set_block(runbook_id, block.id, **st)
            return st

    try:
        result = await _blocks.execute_block(self, block, context)
    except Exception as e:  # noqa: BLE001 — record per-block failure
        st = {"status": "error", "error": f"{type(e).__name__}: {e}", "type": "text"}
        store.set_block(runbook_id, block.id, **st)
        return st

    if "action" in result:
        act = result["action"]
        verb = f"{act['app']}.{act['method']}"
        if mode == "scheduled":
            root = self.autopilot_root()
            # Actionable terminal status: tell the operator exactly how to
            # authorize this verb instead of leaving it silently stuck. Two
            # distinct gates — the policy eligibility floor, then the grant.
            grant_cmd = (f"eos autopilot grant {runbook_id} {verb} "
                         f"--actor-type runbook --scope runbook:{runbook_id}")
            if not _autopilot.is_eligible(root, verb):
                st = {"status": "needs-grant", "verb": verb, "type": "text",
                      "preview": str(act.get("args"))[:200],
                      "hint": (f"'{verb}' is not autopilot-eligible. An operator must add it "
                               f"to policy.json eligible_verbs, then: {grant_cmd}")}
                store.set_block(runbook_id, block.id, **st)
                return st
            grant = _autopilot.match(
                root, actor_type="runbook", actor_id=runbook_id,
                verb=verb, scope_candidates=[f"runbook:{runbook_id}", "global"],
            )
            if grant is None:
                st = {"status": "needs-grant", "verb": verb, "type": "text",
                      "preview": str(act.get("args"))[:200],
                      "hint": f"no grant for '{verb}'. Authorize with: {grant_cmd}"}
                store.set_block(runbook_id, block.id, **st)
                return st
            try:
                res = await self.call_app(act["app"], act["method"], **act["args"])
                st = {"status": "ok", "type": "text", "output_preview": str(res)[:200]}
                audit_ok, audit_err = True, None
            except Exception as e:  # noqa: BLE001
                st = {"status": "error", "error": f"{type(e).__name__}: {e}", "type": "text"}
                audit_ok, audit_err = False, f"{type(e).__name__}: {e}"
            # Mandatory grant audit (autopilot-grants.md): every auto-applied
            # action logs with its grant_id. Never let audit failure break it.
            try:
                _autopilot.append_audit(
                    root,
                    actor={"type": "runbook", "id": runbook_id},
                    app=act["app"], method=act["method"], args=act.get("args") or {},
                    grant_id=grant.get("id"), ok=audit_ok, error=audit_err,
                )
            except Exception:  # noqa: BLE001
                pass
            store.set_block(runbook_id, block.id, **st)
            return st
        # interactive → route through the review gate
        try:
            pend = await self.call_app(
                "rooms", "save_pending_action", app=act["app"], method=act["method"],
                args=act["args"], source_actor={"type": "app", "id": "runbook"},
            )
            st = {"status": "pending", "type": "text", "pending_id": pend.get("id"),
                  "verb": verb}
        except Exception as e:  # noqa: BLE001 — never silently write without the gate
            st = {"status": "error", "type": "text",
                  "error": f"review gate unavailable (rooms): {type(e).__name__}: {e}"}
        store.set_block(runbook_id, block.id, **st)
        return st

    # Plain computed result.
    value = result.get("value")
    store.cache_output(runbook_id, block.id, value)
    if block.output:
        context["outputs"][block.output] = value
        context[block.output] = value
    preview = value if isinstance(value, str) else None
    st = {"status": "ok", "type": result.get("type", "json"),
          "output_preview": (preview[:300] if preview else None),
          "value_kind": result.get("type", "json")}
    store.set_block(runbook_id, block.id, **st)
    return st


async def run_all(self, runbook_id: str, *, mode: str = "interactive") -> dict:
    """Run every block in document order. Stops at the first error unless that
    block sets continue_on_error=true; downstream of a stop go 'blocked'."""
    blocks = _load_blocks(self, runbook_id)
    async with self.entity_lock(f"runbook:{runbook_id}"):
        context = {"outputs": {}, "now": _now_context(), "deferred": set(),
                   "mode": mode}
        results = {}
        stopped = False
        store = _run_store(self)
        for i, b in enumerate(blocks):
            if stopped:
                results[b.id] = {"status": "blocked", "type": "text",
                                 "error": "upstream block failed"}
                store.set_block(runbook_id, b.id, **results[b.id])
                continue
            st = await run_block(self, runbook_id, b, context, mode=mode)
            results[b.id] = st
            # A block awaiting review/grant produces no output; record its
            # output name so dependents read 'awaiting-upstream', not 'blocked'.
            if (st.get("status") in ("pending", "needs-grant", "awaiting-upstream")
                    and getattr(b, "output", None)):
                context["deferred"].add(b.output)
            if st.get("status") == "error" and not b.header.get("continue_on_error"):
                stopped = True
        record = {"mode": mode, "ok": not stopped,
                  "blocks": {k: v.get("status") for k, v in results.items()}}
        store.append_run(runbook_id, record)
    self.spawn_background(self.emit("runbook:run_finished",
                                  {"id": runbook_id, "ok": record["ok"]}))
    return {"id": runbook_id, "results": results, "ok": record["ok"]}


async def run_from(self, runbook_id: str, block_id: str, *, mode: str = "interactive") -> dict:
    """Run a single block and everything after it in document order, rehydrating
    upstream outputs from the sidecar cache."""
    blocks = _load_blocks(self, runbook_id)
    idx = next((i for i, b in enumerate(blocks) if b.id == block_id), None)
    if idx is None:
        return {"error": f"block '{block_id}' not found"}
    async with self.entity_lock(f"runbook:{runbook_id}"):
        context = _build_context(self, runbook_id, blocks, idx)
        context.setdefault("deferred", set())
        context["mode"] = mode
        results = {}
        stopped = False
        store = _run_store(self)
        for b in blocks[idx:]:
            if stopped:
                results[b.id] = {"status": "blocked", "type": "text"}
                store.set_block(runbook_id, b.id, **results[b.id])
                continue
            st = await run_block(self, runbook_id, b, context, mode=mode)
            results[b.id] = st
            if (st.get("status") in ("pending", "needs-grant", "awaiting-upstream")
                    and getattr(b, "output", None)):
                context["deferred"].add(b.output)
            if st.get("status") == "error" and not b.header.get("continue_on_error"):
                stopped = True
    self.spawn_background(self.emit("runbook:block_ran",
                                  {"id": runbook_id, "block": block_id}))
    return {"id": runbook_id, "from": block_id, "results": results}


async def send_notification(
    self, *, text: str, priority: str = "info", scheduled: bool = False,
) -> dict:
    """Callable app verb used by runbook notify blocks.

    ``scheduled=True`` (stamped by the notify block executor on scheduled
    runs) routes delivery through the proactive gate: the run fired from a
    cron under a grant while the user may be absent/asleep — exactly the
    machine-initiated moment quiet hours / caps / per-kind mute exist for
    (same judgment as the reminders migration). A block with
    ``priority="critical"`` maps to critical urgency, which pierces quiet
    hours — the per-block escape hatch for genuinely urgent runbooks. A
    "disabled" verdict (master toggle off, today's default) falls back to
    the raw path so nothing goes silently dark before the gate is opted in.

    Interactive runs never set the flag: the user Apply-clicked the pending
    card seconds ago, and suppressing a delivery they just approved would
    fight their intent.
    """
    message = str(text or "").strip()
    if not message:
        return {"error": "text required"}
    pr = (priority or "info").strip() or "info"
    if scheduled:
        result = await self.proactive_notify(
            "runbook", message, priority=pr,
            urgency="critical" if pr == "critical" else "normal",
        )
        if result.get("delivered"):
            return {"ok": True, "priority": pr, "via": "proactive"}
        if result.get("reason") != "disabled":
            # A real suppression (quiet-hours / cap / mute) — honor it.
            return {"ok": True, "priority": pr, "via": "proactive",
                    "suppressed": result.get("reason")}
        # Gate dark → fall through to the raw path.
    notifier = self.service("notifications")
    if notifier is None:
        return {"error": "notifications service unavailable"}
    await notifier.send(message, priority=pr, source="runbook")
    return {"ok": True, "priority": pr}


def block_statuses(self, runbook_id: str, blocks: list) -> list[dict]:
    """Per-block view for the UI: id/type/output/side_effect + last status +
    staleness (an input's producer ran more recently than this block did)."""
    store = _run_store(self)
    state = store.load(runbook_id).get("blocks", {})
    producers = _producer_map(self, blocks)
    out = []
    for b in blocks:
        bs = state.get(b.id, {})
        status = bs.get("status", "never")
        ran_at = bs.get("ran_at")
        if status == "ok" and ran_at:
            for src in b.inputs.values():
                up_id = producers.get(src)
                up_ran = state.get(up_id, {}).get("ran_at") if up_id else None
                if up_ran and up_ran > ran_at:
                    status = "stale"
                    break
        out.append({
            "id": b.id, "type": b.type, "output": b.output,
            "side_effect": b.side_effect, "inputs": b.inputs,
            "body": b.body, "header": {k: v for k, v in b.header.items()
                                       if k not in ("id", "type", "output", "inputs")},
            "parse_error": b.parse_error,
            "status": status, "ran_at": ran_at,
            "output_preview": bs.get("output_preview"),
            "error": bs.get("error"), "verb": bs.get("verb"),
        })
    return out


async def runbook_timeline(self, entity_path: str) -> dict:
    """4D timeline custom handler: past runs (sidecar) + next scheduled run +
    current block statuses. entity_path is the runbook's vault rel-path."""
    runbook_id = Path(entity_path).stem
    store = _run_store(self)
    data = store.load(runbook_id)
    past = [
        {"when": r.get("ts"), "label": f"run ({'ok' if r.get('ok') else 'failed'}, {r.get('mode')})"}
        for r in reversed(data.get("runs", []))
    ][:20]
    future = []
    nxt = self.get_cron_job_next_fire(f"runbook:{runbook_id}")
    if nxt:
        future.append({"when": nxt, "label": "next scheduled run"})
    now = [{"label": f"{bid}: {st.get('status', 'never')}"}
           for bid, st in data.get("blocks", {}).items()]
    return {"past": past, "future": future, "now": now}


async def panel_runbook_status(self):
    """Hub panel — runbooks with a terminal chart/write_draft block + last run."""
    try:
        notes = self.vault_query(tags=[_shared.RUNBOOK_TAG])
    except Exception:  # noqa: BLE001
        return None
    if not notes:
        return None
    store = _run_store(self)
    rows = []
    for n in notes:
        rid = Path(n.get("path", "")).stem
        if not rid:
            continue
        data = store.load(rid)
        last = data.get("runs", [])
        sub = "never run"
        if last:
            r = last[-1]
            sub = f"{'ok' if r.get('ok') else 'failed'} · {str(r.get('ts', ''))[:10]}"
        rows.append({"title": n.get("frontmatter", {}).get("title") or rid,
                     "subtitle": sub, "href": f"/runbook/#{rid}"})
    return rows or None
