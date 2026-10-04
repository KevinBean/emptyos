

Vault is the source of truth for app data. **VaultIndex** (`emptyos/runtime/vault_index.py`) indexes all vault markdown in memory on startup (~800ms for 3000+ files), updates incrementally via `vault:changed`.

A note has three layers: **frontmatter** (structured, indexed, queryable), **`##` sections** (semi-structured, names indexed — e.g. `## Timeline` with `- 2026-04-02 — interview` bullets), and **prose** (unstructured, LLM-summarizable).

| Method | Layer | Purpose |
|---|---|---|
| `vault_query(tags, **props)` | 1 | Find notes by tags + frontmatter properties |
| `vault_get_properties(path)` / `vault_update(path, props)` | 1 | Read / mutate frontmatter |
| `vault_sections(path)` | 2 | List `##` section names |
| `vault_read_section` / `vault_append_section` / `vault_set_section` | 2+3 | Read / append / replace a `##` section (preserves frontmatter) |
| `vault_read_body(path)` | 3 | Everything after frontmatter |
| `vault_create_note(path, fm, body)` | all | Create new note |

**Soft schema.** Tags in frontmatter identify note types (`job-application`, `person`, `daily`, `song`); folder never determines type. No hard schema — notes stay hand-editable. App-managed note types may use `VaultModel` (`emptyos/sdk/vault_model.py`): coerces YAML strings at the write boundary, round-trips unknown fields, maps legacy keys, validates on write, fails soft on read (`docs/SOFT-SCHEMA.md`). Two access patterns coexist — **VaultIndex** (target) and **vault_config + file I/O** (legacy); migrate an app when touched, and only when its notes have queryable frontmatter — never silently return empty data.

Markdown profile: `docs/EOS-MARKDOWN-PROFILE.md`. Vault operations and connection state: `.claude/rules/vault-operator.md`.
