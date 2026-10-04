# Intent — Library

> Living design doc for `apps/public/standard/library/`. Edit as the app evolves.
> Birth certificate: approved plan `we-want-a-zotero-resilient-galaxy.md`
> (approved plan, 2026-08-22). Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why

A Zotero-style reference manager, built into EmptyOS rather than bolted on:
collect papers/articles, auto-fill citation metadata from a DOI/URL, store
PDFs in the vault, search inside them, annotate/highlight, export a
bibliography (BibTeX/CSL-JSON), and bridge into the existing engineering-
standards KB when a source deserves a permanent KB entry. KB's `reference`/
`clause` citation grammar is standards-numbering-only (IEC/IEEE style) and
stays untouched — Library owns a separate author/year/DOI citation grammar,
with one explicit bridge action ("Send to KB"), not a merge of the two
corpora.

## Relationships

**Calls into** (`self.call_app(...)`):
- `kb` — `propose_kb_note(...)` when the user sends a paper to the KB as a
  `reference` note (review-gated, applied by the user from the pending
  dashboard — never a silent write).

**Emits:**
- `library:paper_added` — a paper note was created (manual entry, DOI lookup,
  or PDF import).
- `library:paper_updated` — frontmatter fields changed (status, rating, tags).
- `library:paper_deleted` — a paper (and its PDF attachment) was removed.
- `library:highlight_added` — a highlight/annotation was appended to a
  paper's `## Highlights` section.
- `library:sent_to_kb` — a "Send to KB" digest was proposed.

**Listens for** (`@on_event`):
- (none)

## Open questions

- `store_category = "productivity"` is a judgment call (closest fit in the
  store category enum) — revisit if a better-fitting category exists.
- Whether "Send to KB" should write a backlink from the created KB note back
  onto the library paper once applied (deferred — see § Future).

## Future

- Browser "save to library" capture (right-click verb via
  `tools/chrome-extension/`, mirroring the shipped `eval-job-sel` pattern) —
  not selected for v1.
- Drag-select PDF text highlighting (v1 is page + typed quote only).
- Semantic full-text search over `data/apps/library/fulltext/` via
  `emptyos/sdk/embeddings.py` (v1 is keyword/substring).
- SRS review over highlights, reusing `apps/personal/media/highlights.py`'s
  SM-2 machinery.
- RIS export (same shape as the BibTeX/CSL-JSON exporters).
- Automatic two-way KB↔library backlink once "Send to KB" is applied.
