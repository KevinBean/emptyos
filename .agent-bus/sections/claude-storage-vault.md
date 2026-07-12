

### Two domains

| Domain | What | Location |
|---|---|---|
| **Vault (user knowledge)** | Anything a human wrote, edits, or needs after reset — journal, contacts, jobs, expenses, items | External `notes.path` in emptyos.toml, markdown + frontmatter |
| **data/ (machine telemetry)** | Event history, syslog, billing counters, chat sessions, activity logs | `data/` (SQLite/JSON) |

**Rule:** Human-authored or recovery-critical → vault. High-frequency operational bookkeeping → `data/`.

### VaultLibrary standard

All vault-backed collections use `VaultLibrary` (SDK). Each item is a `.md` note with frontmatter + type tag (e.g. `tags: [song]`). Query by tag via `vault_query()`, not folder. Folder is default creation location only. See `emptyos/sdk/vault_library.py`.

### KB note kinds

Every KB note carries `tag: kb` and one of nine `kind`s — one corpus, role typing via kind (Notes + Blocks + Documents were unified 2026-05-15; `pattern` added 2026-05-26 for viz/code reusable scaffolding):

| Kind | Shape |
|---|---|
| `concept` | Explanatory standalone |
| `formula` | Implementable spec — `verified_against:` anchors to a case, `implemented_in:` to a code path |
| `reference` | Whole external source (IEC 60287, TB 880, Anders textbook…) — landing page that aggregates its clauses via the `standard_id:` field |
| `clause` | Verbatim text of one section/clause from a reference — frontmatter `standard`, `edition`, `clause` |
| `case` | Worked example with published numbers |
| `lesson` | Practical / hard-won knowledge |
| `pattern` | Reusable scaffolding for new artifacts — engineering anatomy + fenced code blocks (Three.js / CadQuery / etc.) consumed by viz `examples=[...]` few-shot injection or by humans copying directly |
| `doc` | Composition outline via `paragraphs_json` — each paragraph carries `noteRefs: [slug | slug#section]` resolved server-side at view time |
| `moc` | Map of content / navigation hub |

Free-text `references:` strings in any KB note auto-resolve to clickable `clause` notes via the citation parser in `apps/kb/app.py`. Reverse-lookup endpoints: `/kb/api/references` (citation index), `/kb/api/implementations/<path>` (which formulas implement a code path), `/kb/api/notes/<slug>/section/<name>` (slice a single section). `BaseApp.kb_explain(slug)` lets any app pull a KB note's body for tooltip / "?" surfaces. Clauses live flat under `30_Resources/EmptyOS/kb/sources/<slug>.md`; docs under `kb/docs/<slug>.md`.

### Project standard

Every `10_Projects/` entry is a **directory**, never a flat `.md`. Defined as `PROJECT_STRUCTURE` in `apps/projects/app.py`.

```
10_Projects/{project-id}/
├── {project-id}.md      # Main note (frontmatter + tasks + notes)
├── docs/                # Specs, meeting notes, research (created on first use)
├── assets/              # Images, PDFs, attachments (created on first use)
└── log/                 # Activity logs, changelogs (created on first use)
```

All creation paths enforce this. Subfolders are created **lazily on first write** (never eagerly at project creation), so empty scaffold husks don't accumulate — the vault-structure scanner (`scripts/check_vault_structure.py`) prunes empty dirs. `POST /api/projects/{id}/upgrade` converts flat files. `GET /api/structure` reports compliance.
