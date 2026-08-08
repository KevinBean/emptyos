# Path-traversal audit — caller-supplied id → filename (2026-08-08)

Follow-up to the 2026-07-19 sweep that produced `require_path_segment`. That
sweep fixed eight consumers; this one found the shape still live in the review
gate, and enumerates what remains.

**The shape.** `dir / f"{id}.json"` where `id` comes from `request.path_params`.
Starlette's `{param}` excludes `/` but **not** `\`, and on Windows a backslash is
a path separator — so a URL-encoded `..\` escapes the directory.

**Reproduced, not argued** (sandbox daemon, 2026-08-08):
`POST /rooms/api/pending/..%5CCANARY/edit` → loaded a file one directory above
the queue **and wrote to it**, stamping `edited: true` on a record that was never
a pending action. Forward-slash probes correctly 404 at the router.

## Fixed

| Site | Reach | Consequence |
|---|---|---|
| `rooms/pending.py` `_pending_path` | route + Telegram callback | read/**write** a neighbouring `.json` |
| `voice-assistant/pending.py` `_pending_path` | route | same |
| `promote/campaigns.py` `_campaign_path` | route incl. **DELETE** | `unlink()` an arbitrary `.json` |
| `rag-eval` `_reg_*` | internal | (new code, guarded at birth) |
| `promote/app.py` `_proposal_path` | route | **apply_proposal executes** — can fire an outbound webhook |
| `promote/distribution.py` `_tracker_path` | route | read/write tracker |
| `pattern-harvester` `api_proposal_read` | route | arbitrary `.md` read |
| `radio/app.py` persona DELETE | route (auth'd) | `unlink()` an arbitrary `.toml` — the sibling PUT *was* guarded |

First four via `emptyos/sdk/record_store.py::JsonRecordStore`; last four via an
inline `require_path_segment` / `safe_path_segment` (their id grammars differ, and
migrating the store risked orphaning live records — see below).

## Deliberately NOT changed

- **`promote/analytics.py` proposals** — legacy ids carry a `+`
  (`linkedin-…682155+0000`) that `safe_path_segment` refuses outright. Migrating
  would return `None` for every legacy proposal; a pinned round-trip test caught
  it. Widening a guard 15 apps depend on to fit one app's vestigial format is the
  wrong trade. It gained the atomic write only.
- **`apps/personal/**`** — out of scope for an automated pass (gitignored, Kevin's
  own). `fiction-engine`, `haitao`, `nest`, `plan-scenarios`, `recipes`,
  `3d-studio` each have sites; single-user reach, low priority.

## Also fixed — `vault_library.find_file` (the one SDK lever)

The VaultIndex lookup was always safe (it matches on `Path(...).name`, which
cannot hold a separator). Both **fallback** branches escaped, and the second was
the surprise: `dir / "../SECRET.md"` resolves outside and `.exists()` is True,
and `dir.rglob("../SECRET.md")` **returns the outside file** rather than raising.
Now both check containment on the *resolved* path via `_contained()`. One edit
covers ~20 `.detail(f"{id}.md")` call sites across `operate`, `replay`, `kb` and
`haitao` — no caller passes a nested path, and legitimate one-level-down lookups
still resolve. Mutation-verified: removing containment reddens all three
traversal cases and leaves the legitimate-lookup case green.

## Open — needs judgment, not a blanket patch
- ~50 further raw grep hits, mostly false positives: download `filename=` fields,
  internally-generated timestamps, registry-issued `run_id`s. **Do not treat the
  raw count as a vulnerability count.**

## Why a scanner is the real fix

The July sweep missed these four because a human enumerated the call sites. Per
`.claude/rules/audits.md` the check has to rerun to be worth anything, but the
naive grep is ~80% false positives, so it must be narrowed to *"a name that
reaches a path AND originates from `path_params`/request body AND has no guard in
its enclosing function"* and calibrated against known-good apps before it is
allowed to gate. Until then this document is the inventory.
