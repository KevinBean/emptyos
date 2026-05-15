# Proposed-Action Rule — propose, preview, confirm

When an app/agent wants to mutate state the user cares about, it should
**propose** the change first, **preview** it inline, and only **apply** on
explicit user confirmation. The same paradigm covers file edits, outbound
messages, and irreversible commits — only the preview shape differs.

**Reference implementation:** `emptyos/sdk/sandbox.py::SandboxedWrite` +
the room review-gate (`apps/rooms/app.py` `_gate_server_actions` /
`apply_pending` / `reject_pending`; `apps/rooms/pages/index.html:2976`
`renderPendingCard` + `:3024` `_pendingDiffHtml`). The rooms gate is *the
plumbing*; this rule is *the paradigm*. They compose. See also
`.claude/rules/room-review-gate.md` for the [DO:] token grammar that
drives the rooms-specific flow.

## Why this exists

EmptyOS's north star is "with you, not for you" — features that augment
the user's judgment beat features that replace it. Every irreversible
mutation of persistent or shared or external state is a place autopilot
can creep in. The proposed-action paradigm separates *deciding to do X*
from *doing X*, so the human sits in between by default.

Bridge's "auto-apply when confident" framing went the other way (let the
model decide). We explicitly reject that — confidence is what got
"Written to `…`" hallucinations into chat logs without anything being
written. The proposal step is the load-bearing part; "apply" is just a
replay of what the user already approved.

## The three preview shapes

A proposed action falls into exactly one of these shapes. If a candidate
seems to fit two, you're combining two actions — split them and gate
each.

### Diff-shaped

Content has a meaningful **before / after**. The preview is a unified
diff (add / del / ctx / hunk lines). Stale check is byte-comparison of
`before` against current on-disk state.

- **Shipped example:** `[DO:rooms.write_note({path, content})]` →
  `SandboxedWrite`. New files render as all-adds; edits render add+del;
  no-op writes render empty.
- **Candidates:** vault frontmatter updates (`vault_update(path, props)`
  → JSON-diff the before/after frontmatter), multi-line text edits
  inside a note section (`vault_append_section`), bulk find-and-replace.

### Render-shaped

Content is *consumed* in some target form (markdown → Telegram message,
JSON → API payload, prompt → image, draft → social post). The preview
is the **final form as the user will see it** — a rendered card, a
message bubble, an image thumbnail — not the source.

- **Candidates:** outbound Telegram message before `notifications.send`,
  social post before publish, generated image before download to vault,
  email draft before send. Staleness usually doesn't apply (outbound
  messages don't go stale) — gate against double-apply instead.

### Impact-shaped

Content is a **batch or aggregate** effect — many vault notes, a git
commit, a pruning sweep, a board bulk-edit. The preview is a summary of
*what will change*: file list, item count, git diff stats, "removing 47
notes from tag X." Stale check varies (target git ref's HEAD, affected
record IDs, etc.).

- **Strongest candidate today:** `apps/publish/app.py:270` `deploy()`
  currently `git push -u origin gh-pages --force` (line 299) with no
  review of the commit diff. An impact-shaped gate would render the
  `git diff --stat` + commit message before push.
- **Candidates:** bulk archive / delete in boards, board bulk-edit
  apply-all, batch tag operations, fix-agent merge (already gated by
  py_compile — could surface the worktree diff too).

## Lifecycle contract

Every proposed action transitions through these states, mirroring the
pending-action JSON shape in `data/apps/rooms/pending/<id>.json`:

| State | Meaning | Side effects |
|---|---|---|
| `propose` | Capture payload into per-action storage | None on live state |
| `preview` | Render the diff/render/impact in a card | None |
| `apply` | Replay onto live state | Emit `<app>:proposed_applied` |
| `reject` | Discard captured payload | Emit `<app>:proposed_rejected` |
| `stale` (terminal at apply-time) | World moved since capture | Refuse, ask for regen |

Persistence: each action gets its own file under
`data/apps/<app>/proposals/<id>/` (or `pending/<id>.json` for the rooms
gate today). One file per proposal so the queue is greppable, the action
ID is stable across daemon restarts, and orphaned captures can be
swept by hand. Never write to the vault during `propose` — that defeats
the gate.

## What goes in the captured payload

Enough state for `apply` to run **without re-querying the source**:

- Target identifier (vault rel-path, git ref, message channel, image id)
- The proposed content (full new content for diff-shaped, rendered form
  for render-shaped, batch effect descriptor for impact-shaped)
- For staleness: the captured "before" state (file bytes, git HEAD,
  record IDs, etc.) the apply path will compare against
- Source actor (who proposed it: agent id, CLI id, app id) — already in
  the rooms pending JSON via `source_actor`

Per CLAUDE.md rule 13, captured payloads live under `data/` (per-machine,
gitignored), never the vault. The vault is for user-authored content;
proposals are kernel telemetry.

## Staleness check is mandatory

Every shape needs a "did the world move underneath" gate before `apply`.
Without it, a user who clicks Apply five minutes later silently
overwrites whatever changed in between.

- **Diff-shaped:** byte-compare captured `before` to current on-disk
  content. This is what `SandboxedWrite.apply` does today via
  `StaleSandbox`.
- **Impact-shaped:** verify the target ref's HEAD matches what was
  captured (for git pushes), or the affected record set hasn't grown
  /shrunk (for batch deletes).
- **Render-shaped:** usually unnecessary — an outbound message can't go
  stale — but the apply path should still refuse double-applies (set
  `status="applied"` before the actual send, so a retry is a no-op).

Failed-stale must surface clearly. The user sees "the file changed
since I showed you the diff; ask the agent to regenerate." Never
silently retry, never silently overwrite.

## When NOT to use this pattern

The gate adds latency and friction. Use it only where the user benefits
from inspection. Skip it for:

- **Read-only operations.** Nothing to apply, nothing to gate.
- **Form-driven creates.** The form *is* the preview. Adding a gate
  duplicates the inspection the user already did when filling it.
  Applies to projects.create, people.add, kb.add via UI forms — but
  *not* to agent-initiated creates of the same verbs (those should
  gate).
- **High-frequency operations.** Reactor breadcrumbs, telemetry logs,
  metrics writes. Review fatigue kills the gate's value.
- **Reversible idempotent operations.** Re-rendering a podcast,
  rebuilding a static site preview, regenerating a thumbnail. The
  output is replaceable; gate-friction is pure cost.
- **In-process workflow steps.** Inside an agent's plan, each step
  doesn't get its own gate — the *outcome* of the plan does. Gating
  every step kills the flow.

## SDK extraction trigger (per CLAUDE.md rule 9)

| Consumer count | Shape mix | What to extract |
|---|---|---|
| 1 (today) | diff-shaped only (rooms.write_note) | Keep `SandboxedWrite` in `emptyos/sdk/sandbox.py`, no superclass |
| 2 | Same shape | Rename if needed; still no superclass |
| 2 | Different shapes (e.g. + publish.deploy impact-shape) | Extract `ProposedAction` Protocol with `.capture()`, `.preview()`, `.apply()`, `.discard()`, `.is_stale()`; keep concrete classes in their owning apps |
| 3+ | Mixed | Move Protocol into `emptyos/sdk/proposed_action.py`, document each shape's renderer contract |

The graduation gate is *different-shape*, not *consumer-count*. Two
diff-shaped consumers can share `SandboxedWrite` directly. The
superclass is only worth it when the capture/replay logic genuinely
varies (file content vs. git commit vs. HTTP payload).

## UI surface contract

When a new shape lands, reuse:

- `.pending-card` CSS at `apps/rooms/pages/index.html:150` — already
  shared by `apps/company/`'s workshop pending cards
- Apply / Reject button row in `renderPendingCard` (`:2976`) —
  same `.eos-btn-sm` classes, same Apply / Reject semantics
- The path header pattern (`EOS.noteActions(path)` for vault paths)

Add a new renderer next to `_pendingDiffHtml` (`:3024`) in
`apps/rooms/pages/index.html` when the first consumer of a new shape
lands. Promote the renderer to `emptyos/web/static/eos-components.js`
when a second consumer of the same shape appears (per the shipped
extraction rule for EOS_UI helpers).

## CLI / agent system-prompt requirement

Carries over from `.claude/rules/room-review-gate.md`: any agent or CLI
that proposes state changes must be told in its system prompt:

1. What verb to emit (`[DO:app.verb(...)]` or equivalent for the host
   surface)
2. What the captured payload contains (which args are required)
3. That the user reviews each proposal before any state changes — and
   that describing-the-action-without-emitting-the-token results in the
   user seeing nothing happen

The `_build_cli_system` function in `apps/rooms/app.py` is the reference.
Mirror its shape when a new proposal verb lands.

## Cross-references

- `.claude/rules/room-review-gate.md` — the shipped reference
  implementation: token grammar, `_gate_server_actions`,
  `apply_pending` / `reject_pending` lifecycle
- `emptyos/sdk/sandbox.py` — the only concrete capture class today
  (`SandboxedWrite` + `StaleSandbox` + `load_sandbox`)
- CLAUDE.md rule #9 — extract to `sdk/` on second consumer
- CLAUDE.md north star — "with you, not for you" — the philosophical
  anchor this paradigm enforces at the action layer
