# EmptyOS External Vault Connector

Connect/disconnect Claude Code to the external markdown vault. When connected, you gain full read/write access to the user's knowledge base, notes, and personal data.

## When to Use

- User says "connect vault", "open vault", "mount vault"
- User says "disconnect vault", "close vault", "unmount vault"
- User says "vault status", "is vault connected"
- Any time the user wants to search, read, or write vault notes

## Commands

### `/eos-vault` or `/eos-vault status`
Show connection status.

### `/eos-vault connect`
Connect to the vault.

### `/eos-vault disconnect`
Disconnect from the vault.

### `/eos-vault search <query>`
Search vault files (requires connection).

### `/eos-vault read <path>`
Read a vault file by relative path.

## Implementation

### Connect

1. Read `emptyos.toml` to get `[notes] path`
2. Verify the path exists and is a directory
3. Check for `{vault}/CLAUDE.md` — if it exists, read and display it
4. Check for `{vault}/30_Resources/EmptyOS/_vault-map.toml` — report app data locations
5. Write connection state to `.claude/vault-connection.json`
6. Report: vault path, file count, folder structure, CLAUDE.md presence

```bash
# Read vault path
python -c "import tomllib; print(tomllib.load(open('emptyos.toml','rb')).get('notes',{}).get('path',''))"
```

```python
# Write connection state
import json
from datetime import datetime
state = {
    "connected": True,
    "vault_path": vault_path,
    "connected_at": datetime.now().isoformat(),
    "vault_claude_md": f"{vault_path}/CLAUDE.md" if Path(f"{vault_path}/CLAUDE.md").exists() else None,
}
Path(".claude/vault-connection.json").write_text(json.dumps(state, indent=2))
```

### Disconnect

1. Write `{"connected": false}` to `.claude/vault-connection.json`
2. Report disconnection

### Status

1. Read `.claude/vault-connection.json`
2. If connected, verify path still exists
3. Report: connected/disconnected, path, file count, daemon status

### Check Daemon

When connected, also check if EmptyOS daemon is running:

```bash
curl -s http://localhost:9000/api/health 2>/dev/null || echo "not running"
```

If running, prefer API access over direct file reads for operations that benefit from app logic.

### Search

When connected, search vault files:

```bash
# If daemon running — use search API (has semantic search)
curl -s "http://localhost:9000/search/api/search?q=QUERY&top=10"

# If no daemon — direct grep
grep -ril "QUERY" "{vault_path}" --include="*.md" | head -15
```

### Read

When connected, read a vault file:

```python
# Relative path -> absolute
full_path = Path(vault_path) / relative_path
content = full_path.read_text(encoding="utf-8")
```

## Vault CLAUDE.md

If the vault has its own `CLAUDE.md`, it may contain:
- Personal preferences and writing style
- Vault folder conventions beyond PARA
- Project-specific context
- Tag taxonomy
- Templates and note types

Read it on connect and respect its instructions alongside the EmptyOS CLAUDE.md.

## After Connection

Once connected, the user can naturally interact with their vault:
- "What's in my journal today?" → read daily note
- "Add a task: call dentist" → call task API or write to vault
- "Search for cable rating notes" → search API
- "How much did I spend this month?" → expense API
- "Show my projects" → projects API

Route to EmptyOS APIs when the daemon is running, fall back to direct file access when it's not.
