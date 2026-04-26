# Vault Operator — External Vault Connection

EmptyOS mounts an external markdown vault (Obsidian/Logseq) as its "hard drive".
The vault path is configured in `emptyos.toml` under `[notes] path = "..."`.

## Vault Connection State

A file at `.claude/vault-connection.json` tracks the current connection:

```json
{
  "connected": true,
  "vault_path": "/path/to/your/vault",
  "connected_at": "2026-04-07T10:00:00",
  "vault_claude_md": "/path/to/your/vault/CLAUDE.md"
}
```

When **connected**, you can:
- Read/write vault files using absolute paths from the connection file
- Call EmptyOS APIs at `localhost:9000` if the daemon is running
- Use vault_config paths from `_vault-map.toml` for app-specific data locations

When **disconnected**, only operate on the EmptyOS codebase itself.

## How to Read Vault Path

```python
# From emptyos.toml
import tomllib
with open("emptyos.toml", "rb") as f:
    config = tomllib.load(f)
vault_path = config.get("notes", {}).get("path", "")
```

Or read `.claude/vault-connection.json` for the cached connection state.

## Vault Structure (PARA method)

```
{vault}/
├── 00_Inbox/          ← captures, unsorted
├── 10_Projects/       ← active projects with deadlines
├── 20_Areas/          ← ongoing responsibilities (Career, Health, Finances)
├── 30_Resources/      ← reference material (People, Books, Learning)
├── 40_Archive/        ← completed/inactive projects
├── 50_Journal/        ← daily notes: {year}/{YYYY-MM-DD}.md
└── 30_Resources/EmptyOS/
    ├── _vault-map.toml  ← app data path mappings
    └── {app}/           ← per-app vault storage
```

## Rules When Operating on Vault

1. **Never delete vault files** without explicit user confirmation
2. **Respect frontmatter** — don't strip or reformat YAML frontmatter in notes
3. **Use forward slashes** in paths (Windows compat: `D:/Vault/note.md`)
4. **Prefer APIs** over direct file access when EmptyOS daemon is running — APIs apply vault_config, validation, and emit events
5. **Read vault CLAUDE.md** if it exists at `{vault}/CLAUDE.md` — it may contain personal preferences, vault conventions, or project context
6. **Vault map** at `{vault}/30_Resources/EmptyOS/_vault-map.toml` tells you where each app's data lives
