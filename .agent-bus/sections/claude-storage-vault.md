

### Two domains

| Domain | What | Location |
|---|---|---|
| **Vault (user knowledge)** | Anything a human wrote, edits, or needs after reset — journal, contacts, jobs, expenses, items | External `notes.path` in emptyos.toml, markdown + frontmatter |
| **data/ (machine telemetry)** | Event history, syslog, billing counters, chat sessions, activity logs | `data/` (SQLite/JSON) |

**Rule:** Human-authored or recovery-critical → vault. High-frequency operational bookkeeping → `data/`.

### VaultLibrary standard

All vault-backed collections use `VaultLibrary` (SDK). Each item is a `.md` note with frontmatter + type tag (e.g. `tags: [song]`). Query by tag via `vault_query()`, not folder. Folder is default creation location only. See `emptyos/sdk/vault_library.py`.

### KB note kinds

Every KB note carries `tag: kb` and one of ten `kind`s. The vocabulary is closed and enforced in two places — `KINDS` in `apps/public/standard/kb/shared.py` and `VALID_KINDS` in `scripts/audit_kb_app_alignment.py` — pinned against each other by `tests/test_unit_kb_alignment_symbols.py`; add a kind to both or neither.

| Kind | Shape |
|---|---|
| `concept` | Explanatory standalone |
| `formula` | Implementable spec — `verified_against:` anchors to a case, `implemented_in:` to a code path |
| `reference` | Whole external source — landing page aggregating its clauses via `standard_id:` |
| `clause` | Verbatim text of one section of a reference — frontmatter `standard`, `edition`, `clause` |
| `case` | Worked example with published numbers |
| `lesson` | What people get WRONG (`## The lesson`, `## The misconception`) |
| `guide` | A procedure for carrying out a task — how to DO it; substantive body, composes/navigates nothing |
| `pattern` | Reusable scaffolding (engineering anatomy + fenced code) for viz few-shot injection or copying |
| `doc` | Composition outline via `paragraphs_json` — `noteRefs: [slug \| slug#section]` resolved at view time |
| `moc` | Map of content / navigation hub |

Free-text `references:` strings auto-resolve to `clause` notes (citation parser in `apps/public/standard/kb/app.py`). Reverse lookups: `/kb/api/references`, `/kb/api/implementations/<path>`, `/kb/api/notes/<slug>/section/<name>`. `BaseApp.kb_explain(slug)` pulls a KB body for tooltips. Clauses live flat under `30_Resources/EmptyOS/kb/sources/<slug>.md`; docs under `kb/docs/<slug>.md`.

### Project standard

Every `10_Projects/` entry is a **directory**, never a flat `.md` — `{project-id}/{project-id}.md` plus `docs/`, `assets/`, `log/` created **lazily on first write** (the vault-structure scanner prunes empty dirs). Defined as `PROJECT_STRUCTURE` in `apps/public/standard/projects/app.py`. `POST /api/projects/{id}/upgrade` converts flat files; `GET /api/structure` reports compliance.
