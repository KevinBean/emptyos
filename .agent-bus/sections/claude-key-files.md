

- `docs/README.md` — documentation index; `docs/DOC-SYSTEM.md` — how the docs are structured
- `docs/DESIGN.md` — architecture, philosophy, consciousness model
- `docs/APPS.md`, `docs/TIERS.md`, `docs/SKILLS.md` — generated catalogs (`scripts/generate_{apps,tiers,skills}_doc.py`)
- `docs/APP-DEVELOPMENT.md`, `docs/FRONTEND-DESIGN-LANGUAGE.md`, `docs/GETTING-STARTED.md`
- `docs/DEFERRED-WORK.md` — deferred features with build triggers; grep it before building something substantive
- `docs/AGENT-FRAMEWORK.md` — a new autonomous agent is *config, not code*; register every loop in `emptyos/sdk/loops.py`
- `AGENTS.md` — non-Claude-Code AI self-config; `apps/public/standard/forge/FORGE.md` — Forge growth charter
- `emptyos.toml` — machine config (gitignored); `restart.bat` — kill python, check external services, boot EmptyOS
- `emptyos/kernel/__init__.py` — kernel boot; `emptyos/sdk/base_app.py` — BaseApp; `emptyos/web/server.py` — FastAPI server + auto-UI + topology
- `emptyos/runtime/vault_index.py` — in-memory vault index; `emptyos/runtime/vault_map.py` — app path discovery + auto-heal
- `emptyos/sdk/vault_library.py` — vault-backed collections; `emptyos/sdk/utils.py` — `parse_llm_json`, `streak_from_dates`, … (re-exports frontmatter helpers — don't redefine them); `emptyos/sdk/srs.py` — FSRS-4.5
- `emptyos/sdk/loops.py` — feedback-loop registry (`eos loops list|show|stages`)
- `emptyos/capabilities/providers/claude_cli.py`, `openai_compat.py` — think providers
- **Top-level stdlib-only modules** — **the kernel never imports `emptyos.sdk` at module level** (it pulls in `base_app`), so helpers both need live here: `nethost.py` (canonicalise hosts before loopback checks), `frontmatter.py` (the one frontmatter parser), `basepath.py` (`resolve_under_base`), `fieldspec.py` (calculator-declaration checker), `composite_score.py` (weighted scoring; three silent-wrong-answer edges), `headless.py` (child processes never open a console window), `speechlang.py` (zh/ja/en for TTS routing). Why each exists → `.claude/rules/top-level-modules.md`.

### Rules loaded on demand

`.claude/rules/` holds one file per topic. Files with `paths:` frontmatter load only when you touch matching files (`.claude/rules/path-scoped-rules.md`). **Read these by hand when the task is about them but you haven't touched a matching path:** `proposed-action` (propose/preview/confirm), `autopilot-grants`, `verb-registry`, `authorship-boundary`, `proactive-comms`, `sandbox-driven-testing` + `sandbox-usage` (leasing `:9002+`), `vault-operator`, `media-gotchas` (any MV/podcast/ComfyUI work — vault paths don't trigger it), `web-tool-operation` (driving Flow/ChatGPT/claude.ai), `three-natures-lens`, `self-audit-loops`, `agent-bus` (any `self.think()` needing architecture context), `demo-mode` (`[app] private = true`), `gate-driven-fix-loop`, `parallel-shard-runs`, `dev-cli-dispatch`.