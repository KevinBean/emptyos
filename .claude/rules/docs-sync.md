# Session Wrapup Rule

At the end of meaningful development sessions, suggest `/eos-session-wrapup` to sync docs and record a dev log.

Trigger conditions:
- Created a new app directory under `apps/` or `apps/personal/`
- Deleted or retired an app (moved to `_retired/`)
- Added or removed a plugin under `plugins/`
- Changed an app's manifest.toml `[app]` section (id, name, description)
- Any session that changed multiple files or added features
- Changed release.toml tier definitions (app/plugin/skill lists)
- Changed public docs (README.md, docs/GETTING-STARTED.md, docs/APP-DEVELOPMENT.md)
- Added or significantly changed test files under `tests/` (update CLAUDE.md § Testing if the suite structure changes)
