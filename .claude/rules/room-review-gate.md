# Room Review Gate Rule — pending [DO:] actions awaiting Apply / Reject

The review gate is the "with you, not for you" mechanism for room CLI
participants. Claude Code CLI runs read-only in a room (no Edit/Write/Bash
allowed) and is instructed via system prompt to emit
`[DO:app.method({"arg":"value"})]` tokens for any state-changing action.
Those tokens land as **pending action cards** the user reviews. Apply
dispatches via `call_app`; Reject leaves them logged but un-executed.

**Reference implementation:** `apps/rooms/app.py` —
`_gate_server_actions`, `apply_pending`, `reject_pending`, `list_pending`.
UI: `renderPendingCard`, `applyAction`, `rejectAction` in
`apps/rooms/pages/index.html`. See also `docs/ROOMS-V3.md` Phase 5.

## Why this exists

Three principles in tension:

1. **Agents shouldn't autopilot the user's filesystem.** A CLI with
   `--allowedTools=Edit,Write,Bash` will modify files the moment it
   thinks it should. That's the wrong default for a chat surface where
   the user wants to think before committing.
2. **claude-cli streams events; tool_use is mid-stream.** We can't pause
   the subprocess to ask "approve this Edit?" — by the time we'd ask,
   it's already executed. So real tool-level review-gating means running
   the CLI without write tools at all.
3. **The agent still needs a way to propose actions.** Pure read-only
   CLIs are useful but they can't *do* anything. We need a structured
   way for the agent to say "I want to do X" without doing it.

The `[DO:]` token grammar threads this needle: it's text the LLM emits
as part of its reply, parsed post-stream, surfaced as cards. The user
clicks Apply for the actions they want; the rest stay logged. No
autopilot, no rebuilding the streaming machinery.

## The grammar

Same pattern agents use for [DO:] today (Phase 5 generalised it for CLI):

```
[DO:<app>.<method>({"<arg>":"<value>", ...})]
```

- `<app>` and `<method>` are dot-separated identifiers (lowercase
  recommended).
- The args object is JSON. Multi-line is fine — the parser uses balanced
  braces, not single-line regex.
- Multiple tokens in one reply produce multiple pending actions in
  document order.

Example reply from a CLI:
```
Sure — I'll add a task and ping you tomorrow.

[DO:task.add({"text":"Refactor billing app"})]
[DO:rooms.add_reminder({"due_ts":"2026-05-11T09:00:00","note":"check refactor status"})]
```

After parsing: 2 pending actions, cleaned reply text:
```
Sure — I'll add a task and ping you tomorrow.
```

## Backend contract

### Input

```python
async def _gate_server_actions(
    self, response: str, *, room_id: str, source_actor: dict,
) -> tuple[str, list[dict]]:
```

- `response` — the full LLM/CLI reply text including `[DO:]` tokens.
- `room_id` — for filing the pending entries.
- `source_actor` — `{"type": "cli"|"agent", "id": "..."}`. Persisted on
  each pending entry so the UI can show "Claude Code CLI proposes:".

### Output

A 2-tuple:
- Cleaned text with all `[DO:]` tokens stripped.
- List of saved pending action dicts. Empty when no tokens matched.

Each saved pending entry has the shape:
```jsonc
{
  "id": "act-<10-hex>",
  "room_id": "...",
  "ts": "ISO timestamp",
  "source_actor": {"type": "cli", "id": "claude-cli"},
  "app": "task",
  "method": "add",
  "args": {"text": "..."},
  "status": "pending"   // pending → applied | rejected | failed
}
```

Stored at `data/apps/rooms/pending/<id>.json` — one file per action.

### No allowlist

Unlike agent `[DO:]` execution, the gate accepts ANY app+method on
emit. **The user is the gate.** Validation happens at apply time:
unknown app/method just fails through to `{status: "failed", error: ...}`
and surfaces as a red card. This trade-off is intentional — pre-validating
would require the CLI to know every app's method signature, which is a
moving target.

## Lifecycle

```
[DO:] emitted in reply
   ↓
_gate_server_actions parses + saves as {status: "pending"}
   ↓
Card renders inline (yield from generator) + saved on the message
   ↓
User clicks Apply or Reject
   ↓
apply_pending → call_app(app, method, **args)
   ├── success → status: "applied", result captured
   ├── unknown app/method → status: "failed", error captured
   └── exception → status: "failed", error captured
reject_pending → status: "rejected"
   ↓
Card re-renders with new state; emit rooms:action_applied / _rejected
```

Once resolved, an action stays in the pending dir but with non-pending
status. The global pending dashboard's `?status=all` filter shows the
audit trail.

## When NOT to use the review gate

The gate is for **CLI participants**. Agents (regular `[provides.assistant]`-
style agents using `self.think`) keep auto-execute + undo via the
existing `_execute_server_actions` path:

- Agents emit `[DO:]` against their `server_actions` allowlist
- Each call executes immediately
- Reversible calls record an `inverse`; the Undo button calls it

Two paths because the threats are different. An agent's
`task.add` is constrained — it can't do arbitrary filesystem damage
because the allowlist gates which methods are exposed. A CLI's tool_use
is unconstrained — Edit/Write/Bash means anything goes. So agents are
allow-by-list, CLIs are review-by-default.

If a future feature wants "review even agent [DO:]" (e.g. for
high-stakes verbs like `delete`), pass `gate_mode="gate"` through the
agent path's `_execute_server_actions` — wire it up only when a
specific verb actually warrants the friction.

## CLI system-prompt requirement

Every CLI participant's system prompt **must** include the gate framing.
Currently in `_build_cli_system`:

> You have read-only tools (Read, Grep, Glob, WebFetch) for investigation.
> For any action that would MODIFY state — adding a task, editing a note,
> sending a message — emit a `[DO:app.method({"arg":"value"})]` token
> inline in your reply. The user reviews each [DO:] as a card and clicks
> Apply or Reject. Never describe an action you would take and skip the
> token; the user only sees what you emit.

The "never describe an action without emitting the token" line is
load-bearing. Without it, the CLI says things like "I'll add a task to
follow up" without emitting the `[DO:]` token, and the user sees the
intent without ever getting an Apply button.

## Surfaces

- **Inline cards** — saved in the message's `pending: [<id>, ...]` list,
  re-rendered on history reload via `_pendingMap` lookup.
- **Activity drawer · Pending tab** — per-room view, splits into
  "Awaiting review" and "History (resolved)".
- **Global pending dashboard** — sidebar `⏳ <n>` button auto-shows when
  any room has pending across the system. Modal groups by room.
- **Inbound dashboard** — fired counts surface on `/rooms/` cold open.

All four surfaces use the same `renderPendingCard(action)` so resolving
in any one updates the rest in lockstep (the apply/reject handlers find
every DOM instance by id and replace).

## Tests

`tests/test_sys_rooms_logic.py::TestGateServerActions` covers token
parsing, single + multi token runs, file persistence. End-to-end
apply/reject is exercised through the manifested `rooms:action_applied`
event landing in the reactor's journal breadcrumb.
