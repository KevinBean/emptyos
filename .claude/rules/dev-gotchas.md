# Development Gotchas — Non-Architectural Surprises

Keep CLAUDE.md focused on EmptyOS-architectural rules. Generic Python/web/integration quirks live here.

## Frontend / Web

- **Windows paths** — normalize with forward slashes (`EOS.normPath()`, `.replace("\\", "/")`)
- **HTML pages hot-reload** — read from disk per request; Python changes still need server restart
- **`encodeURIComponent` encodes `/`** — use a custom encoder for Obsidian URIs
- **TTS audio** — must be copied to a servable dir + served via `/api/audio/{filename}`

## Python / Runtime

- **`config.path` is the config FILE**, not the project root — use `config.path.parent` for project dir
- **App relative imports** (`from . import module`) require `app_loader` to register parent packages in `sys.modules`
- **Billing stats / assistant sessions** are cached in memory; billing flushes every 10 calls

## Integrations

- **Claude CLI** — needs `--dangerously-skip-permissions` + `cwd=vault_path` for vault access
- **FLUX image models** — cfg=1–4, euler sampler, simple scheduler (NOT SD1.5 params)
- **Staff agents** — global Claude lock → sequential execution; output to `system-log` (system feed), NOT `capture` (user inbox)
- **Saved staff agents** in `data/apps/staff/agents.json` override `DEFAULT_STAFF` (defined in `apps/personal/staff/agents.py`) — edit both when changing agent configs

## Architectural — keep these top-of-mind (also in CLAUDE.md)

- **Vault read-modify-write races** — see CLAUDE.md § Development Gotchas
- **Vault frontmatter tags must be block-style** — see CLAUDE.md § Development Gotchas
- **Normalize loose field shapes at the write boundary** — see CLAUDE.md § Development Gotchas
