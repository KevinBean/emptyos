# Intent — Requirements

> Living design doc for `apps/public/standard/requirements/`. Edit as the app evolves.
> Birth certificate: this session's approved plan (mellea-borrow → requirement-management pivot, 2026-07-22).
> Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why

EmptyOS already owns the expensive 80% of the software-engineering
requirements-management process — traceability (KB `verified_against` /
`implemented_in` + the citation index + `kb_claim_audit`), verification
(conformance = acceptance criterion + check), and change rails (DEFERRED-WORK
triggers, proposed-action, loop-traceability). The keystone gap was that **no
first-class requirement object existed** — nothing to trace *from*, so the
traceability chain started one link too late. This app is that keystone: a
project-scoped, stateful, lifecycle-bearing requirement that traces *into* the
KB rather than living in it. Requirements are deliberately **not** KB notes — KB
is global reusable knowledge; a requirement belongs to one project and moves
through a lifecycle (proposed → approved → verified → superseded).

## Relationships

**Calls into** (`self.call_app(...)`):
- `kb` (optional) — `resolve_reference` maps a free-text `references:` citation
  to a KB `clause` slug, for the detail view's clickable trace links and the
  impact query. Fail-soft: absent kb degrades to "unresolved", never errors.
- `<any calc app>` (dynamic) — `run_conformance` runs a linked conformance case
  live to verify a requirement (no persisted conformance-result store exists).

**Emits:**
- `requirements:created` — a requirement was captured; reactor/breadcrumb hooks.
- `requirements:updated` — a field changed (status/priority/title/verified_by).
- `requirements:verified` — a linked conformance case passed and flipped status.

**Listens for** (`@on_event`):
- (none)

**Contributes:**
- `[[contributes.boards.preset]]` `board_presets` — a read-only Requirements
  board (table + kanban-by-status), aggregated generically by the boards app.
- `[provides.timeline] entity_source = requirement_path` — the 📅 4D drawer on
  the detail view.

## Open questions

- Should a requirement without a formal project route to `inbox` (current
  default) or force project selection? Current: default `inbox`, lazy dir.
- Global row id is `{project}~{req_id}` (tilde-joined, path-safe). Revisit if a
  project id can legitimately contain `~`.
- **Boards preset timing — FIXED at the platform (2026-07-22).** Originally the
  `requirements-all` board didn't appear: boards gathered app-contributed presets
  only once in its own lazy `setup()`, and because apps web-lazy-load, a
  contributor loaded *after* boards was missed. Fixed generally in
  `apps/public/standard/boards/app.py::_sync_presets` — boards now force-loads any
  app whose *manifest* declares `[[contributes.boards.preset]]` before gathering,
  so every system-database board appears regardless of load order (bounded: ~4
  contributor apps; fail-soft). Verified on a sandbox: the board appears even when
  `/boards` is hit before `/requirements` ever loads. This helps every current and
  future contributor app, not just this one.

## Future

- **Judge-check verification** (mellea IVR): a `verified_by: judge:<requirement>`
  mode that validates a soft/AI-generated deliverable via LLM-as-judge with
  bounded repair — this app is the awaited *second consumer* of the deferred
  `think_with_requirements` helper (`docs/DEFERRED-WORK.md`).
- Requirement baselines / versioning (freeze a set at a milestone).
- Change-impact beyond citation matching (propagate a clause edit to dependent
  requirements + their verification state).
- ReqIF/DOORS import-export (only if a real interchange need appears).
