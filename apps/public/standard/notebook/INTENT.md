# Intent — Notebook

> Living design doc for `apps/public/standard/notebook/`. Edit as the app evolves.
> Birth certificate: `30_Resources/EmptyOS/grill/new-app-notebook-2026-09-28.md`.
> Plan: `10_Projects/emptyos/log/_plans/notes-workspace.md`.
> Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why
Notes in EmptyOS only opened as a modal overlay on top of some other app, so the
vault had no home of its own and daily reading still happened in an external
viewer. Notebook is the note-first workspace: browse the vault, open any note at
a bookmarkable URL, and see what links in and out.

## Relationships
**Calls into** (`self.call_app(...)`):
- `link` (optional) — `backlinks(title=<path>)`; it owns the cached vault-wide link index, so notebook never rebuilds one.

**Reads:**
- `vault_index` service — the note path list behind the tree and link resolution.
- `/api/vault/read` (platform) — the page fetches note bodies directly.

**Emits:**
- (none) — read-only until the editor lands (plan T2).

**Listens for** (`@on_event`):
- (none) — every request reads the live index, so there is no cache to invalidate.

## Decisions
- Link resolution is the SDK's `resolve_link_target` over `build_link_lookup` (`emptyos/sdk/utils.py`), the same two functions `link`'s index uses — extracted when notebook became the second consumer. An ambiguous stem returns every candidate; the page asks rather than guessing. The outgoing panel drops self-links (as `link` does) and attachment embeds (`![[photo.jpg]]`).
- Backlinks answer `pending` after 6 s and the page polls; a finished lookup is kept for 120 s so the next poll collects it instead of starting another rebuild. `link`'s index build can take minutes and must keep running rather than be cancelled by a request timeout.
- The tree lists notes, not disk: a folder with no markdown at any depth does not appear. `dir` never reaches the filesystem, so its check exists for honest errors, not safety — and a colon stays legal in a folder name (Linux/macOS vaults).
- An absent vault index is an error, never an empty vault.
- Every note-to-note move stays in the page: wikilinks, bare `x.md` mentions and relative `.md` links route through the hash; `#anchor` links scroll. The 📅 timeline button mounts on the header (`#nb-head[data-entity-path]`), not the note body.

## Open questions
- Editing, rename-with-link-rewrite, attachments and the viewer-seam switch are plan tasks T2–T5.

## Future
- Tabs / split panes.
- vault-graph node click → open in notebook.
