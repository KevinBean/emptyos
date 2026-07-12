# Explore (retired)

This app was merged into `apps/kb/` in May 2026. See the kb-explore merge plan
notes for context — the short version:

- The **vault network graph** (formerly `/explore/pages/graph.html`) now lives
  inline as a peer view in kb: `/kb/?view=network`. Click a node → inline
  expand card; click an outgoing chip → graph re-centers.
- The **AI flipbook generator** (formerly `/explore/`) moved into kb as a
  second peer view: `/kb/?view=flipbook`. Backend ported into
  `apps/kb/flipbook_gen.py` + `apps/kb/flipbook_io.py`; frontend into
  `apps/kb/pages/flipbook.{html,js,css}`. Routes namespaced under
  `/kb/api/flipbook/*`.
- Saved flipbook notes migrated from `30_Resources/Explore/` to
  `30_Resources/EmptyOS/kb/flipbook/` via `scripts/migrate_explore_to_kb.py`.
  The new notes carry `tags: [kb, flipbook]` so they surface in kb's queries,
  network view, and hub panel.

This folder is preserved (not deleted) so the git history of the merge is
easy to follow and so any half-migrated personal scripts that hard-coded
`apps/explore/...` paths fail loudly instead of silently. If you've confirmed
nothing references this app anymore, deletion is safe.
