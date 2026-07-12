

Vault is the source of truth for app data. **VaultIndex** (`emptyos/runtime/vault_index.py`) indexes all vault markdown in memory on startup (~800ms for 3000+ files), updates incrementally via `vault:changed`.

### Three Layers in a Vault Note

```
--- frontmatter ---          ← Layer 1: Structured (indexed, queryable)
company: Acme Corp
tags: [job-application]
---

## Timeline                  ← Layer 2: Semi-structured (section names indexed)

- 2026-04-02 — interview

## Notes                     ← Layer 3: Unstructured (human prose, LLM-summarizable)

Panel was friendly...
```

### BaseApp Vault API

| Method | Layer | Purpose |
|---|---|---|
| `vault_query(tags, **props)` | 1 | Find notes by tags + frontmatter properties |
| `vault_get_properties(path)` | 1 | Read all frontmatter fields |
| `vault_update(path, props)` | 1 | Mutate frontmatter fields |
| `vault_sections(path)` | 2 | List `##` section names |
| `vault_read_section(path, name)` | 2+3 | Read content of a `##` section |
| `vault_append_section(path, name, text)` | 2 | Append to a `##` section |
| `vault_set_section(path, name, text)` | 2 | Replace/insert a `##` section (set-semantics, preserves frontmatter) |
| `vault_read_body(path)` | 3 | Read everything after frontmatter |
| `vault_create_note(path, fm, body)` | all | Create new note |

### Convention (soft schema)

Tags in frontmatter identify note types (`job-application`, `person`, `daily`, `song`); apps query by tag, and folder never determines type. Vault notes remain the source of truth and stay hand-editable — there is **no hard schema**. For *app-managed* note types, apps may use **soft typed contracts** via `VaultModel` (`emptyos/sdk/vault_model.py`): a Pydantic view that coerces YAML-string frontmatter at the write boundary (`mode="before"`), round-trips unknown fields (`extra="allow"`), maps legacy keys (`_legacy_aliases`), validates on write, and fails soft on read. The contract **is** the `VaultModel` subclass, not a prose doc. Apps that haven't adopted it still read with `.get()` defaults — both coexist; human-authored notes are never forced into a schema. See `docs/SOFT-SCHEMA.md`.

Two access patterns coexist: **VaultIndex** (target — `vault_query`, `vault_update`) and **vault_config + file I/O** (legacy — `vault_config()` → `Path.glob()` → parse). Apps migrate when touched. Safe migration rule: only migrate an app when its notes have queryable frontmatter. Otherwise add tags first via a vault script — never silently return empty data.

For vault operations and connection state, see `.claude/rules/vault-operator.md`.
