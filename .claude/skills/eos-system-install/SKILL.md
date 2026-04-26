# EmptyOS System Install

Set up EmptyOS from scratch on a new machine, or verify/repair an existing installation. Covers dependencies, config, vault connection, external services, boot-on-login, and first run.

## When to Use

- Fresh git clone — user says "install", "set up", "get started"
- New machine migration — "set up on this machine"
- Broken installation — "eos won't start", "missing dependencies", "config broken"
- Adding a new external service — "add ComfyUI", "connect Ollama"
- Verifying everything works — "is my install ok?"

## Process

### Step 1: Prerequisites Check

Verify the machine has what EmptyOS needs.

```bash
# Python 3.11+
python --version

# pip available
pip --version

# Git (for version control)
git --version

# curl (for health checks)
curl --version 2>/dev/null | head -1
```

Report what's installed and what's missing. Python 3.11+ is the only hard requirement.

### Step 2: Install Python Dependencies

```bash
cd D:\emptyos

# Core dependencies
pip install -e .

# Optional: semantic search (FAISS)
pip install -e ".[semantic]"

# Optional: development/testing
pip install -e ".[dev]"

# Optional: test suite
pip install playwright pytest-playwright httpx
playwright install chromium
```

Verify installation:
```bash
python -c "import emptyos; print('emptyos package OK')"
python -c "import fastapi, uvicorn, typer, rich, watchfiles, aiohttp, apscheduler; print('all deps OK')"
```

**PATH check (Windows, per-user pip installs).** `pip install -e .` often installs `eos.exe` to
`%APPDATA%\Python\Python{ver}\Scripts`, which is not on PATH by default. `eos init` (step 3) detects this
and prints the exact PowerShell command to fix it. Until then, `python -m emptyos` works everywhere.

### Step 3: Configuration

Check if `emptyos.toml` exists. If not, there are two paths:

**Interactive setup** (recommended for first-time):
```bash
python -m emptyos init
```
This walks through OS name, vault path, LLM providers, external services, personal defaults, and boot-on-login setup.

At the end it also:
- Registers the config path at `~/.config/emptyos/config-path.txt` so `eos` works from any directory
- Checks whether `eos` is on PATH and prints the exact command to add it if not

**Manual setup** (for experienced users):
```bash
cp emptyos.example.toml emptyos.toml
# Edit emptyos.toml with your settings
```

Key config sections to verify:
- `[notes] path` — path to markdown vault (if any)
- `[llm]` or `[capabilities.think]` — at least one LLM provider for AI features
- `[plugins.*]` — external service connections (Ollama, ComfyUI, etc.)
- `[scheduler] timezone` — for cron jobs

### Step 4: Vault Connection

If a vault path is configured in `emptyos.toml`:

```bash
# Verify vault exists
python -c "import tomllib; c=tomllib.load(open('emptyos.toml','rb')); print(c.get('notes',{}).get('path','(not set)'))"
```

Check vault structure:
- Does `{vault}/30_Resources/EmptyOS/` exist? If not, first boot will create it.
- Does `{vault}/30_Resources/EmptyOS/_vault-map.toml` exist? If not, first boot auto-generates it.
- Does `{vault}/CLAUDE.md` exist? If yes, it contains vault-specific instructions.

Write Claude Code vault connection state:
```python
import json
from datetime import datetime
from pathlib import Path
state = {
    "connected": True,
    "vault_path": vault_path,
    "connected_at": datetime.now().isoformat(),
    "vault_claude_md": f"{vault_path}/CLAUDE.md" if Path(f"{vault_path}/CLAUDE.md").exists() else None,
}
Path(".claude/vault-connection.json").write_text(json.dumps(state, indent=2))
```

### Step 5: External Services

EmptyOS works without external services (human fallback for all capabilities), but these enhance it:

| Service | What | Check Command | Config Key |
|---|---|---|---|
| **Ollama** | Local LLM | `curl -s http://localhost:11434/api/tags` | `[plugins.ollama] host` |
| **ComfyUI** | GPU image/video | `curl -s http://localhost:8188/system_stats` | `[plugins.comfyui] host, launcher` |
| **Voice API** | TTS + STT | `curl -s http://localhost:8601/health` | `[plugins.voice-api] host` |
| **Applio** | Voice conversion | `curl -s http://localhost:6969/health` | `[plugins.applio] host, launcher` |
| **Blender** | 3D cable routing | `curl -s http://localhost:8400/health` | `[plugins.blender] host` |

For each service the user wants:
1. Verify the service binary/runtime exists on this machine
2. Add config to `emptyos.toml` under `[plugins.<name>]`
3. Set `launcher` path for auto-start capability
4. Test connectivity

### Step 6: Data Directories

Ensure runtime directories exist:

```bash
# These should exist (created by eos init or first boot)
ls data/
ls data/logs/
ls data/state/
ls data/cache/
ls data/apps/
```

If missing:
```bash
mkdir -p data/logs data/state data/cache data/apps
```

### Step 7: First Boot Test

```bash
# Quick status check (no daemon)
python -m emptyos

# Full health check (starts kernel, checks everything, exits)
python -m emptyos health

# Start daemon
python -m emptyos start
```

Verify in browser: `http://localhost:9000/`

Expected output from health check:
- Vault: OK (with file count)
- Capabilities: 7 listed, think should show configured providers
- Connectors: configured services show OK or unreachable
- Apps: count matches expected (63+ for full install)

### Step 8: Boot on Login (Windows)

For Windows machines, EmptyOS can start automatically on login:

```bash
# Generate boot script
python -m emptyos boot --generate

# Install to Windows Task Scheduler
python -m emptyos boot --install
```

Or use `restart.bat` for manual start (also checks/starts Ollama and ComfyUI).

### Step 9: Test Suite (Optional)

If developing or verifying the install thoroughly:

```bash
# EmptyOS must be running on localhost:9000
python -m emptyos start &

# Run smoke tests (every app page loads)
pytest tests/ -v -k tier1

# Run API tests (every endpoint returns 200)
pytest tests/ -v -k tier2

# Full suite
pytest tests/ -v
```

### Step 10: Verify Installation

Final checklist — run after all steps:

```bash
# System status
python -m emptyos

# Full health
python -m emptyos health

# Start and check web
python -m emptyos start
# Then open: http://localhost:9000/
# Check: http://localhost:9000/topology (all nodes visible)
# Check: http://localhost:9000/docs (Swagger API docs)
```

Report:
- All capabilities and their provider status
- All plugins connected/unreachable
- App count and endpoint count
- Vault connection status
- Any errors or warnings

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: emptyos` | Not installed | `pip install -e .` |
| Port 9000 in use | Previous instance | `restart.bat` or kill process on port 9000 |
| Vault not found | Wrong path in config | Check `[notes] path` in `emptyos.toml` |
| Ollama unreachable | Not running | `ollama serve` or check host in config |
| ComfyUI unreachable | Not running | Check launcher path, start manually first |
| `emptyos.toml` missing | Not initialized | `python -m emptyos init` or `cp emptyos.example.toml emptyos.toml` |
| SQLite lock errors | Dirty shutdown | Delete `data/*.db-wal` and `data/*.db-shm` files |
| Apps not loading | Import errors | Check `python -m emptyos` output for loader errors |

## Key Files

| File | Purpose |
|---|---|
| `emptyos.example.toml` | Config template (copy to `emptyos.toml`) |
| `emptyos.toml` | Machine config (gitignored) |
| `pyproject.toml` | Python dependencies |
| `emptyos/cli/commands/init.py` | Interactive setup wizard |
| `emptyos/cli/commands/boot.py` | Boot script generation + Task Scheduler install |
| `restart.bat` | Manual restart script (kills, checks services, starts) |
| `boot.vbs` | Auto-generated login boot script (Windows) |
| `service.bat` | Windows Task Scheduler launcher |
