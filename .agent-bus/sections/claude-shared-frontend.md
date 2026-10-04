

Visual + interaction DNA: `docs/FRONTEND-DESIGN-LANGUAGE.md` (read before touching a page). Shared bundles, geo stack and shortcuts: `.claude/rules/shared-frontend.md` (loads on `apps/**/pages/**`). Audits: `/eos-design-system-audit`.

**Never hand-roll a primitive:**
- **`EOS_UI.statusBadge(label, status, map?)`** for every status/priority chip — never `'eos-badge-status-' + statusVariant(...)` (double-prefixes).
- **`EOS_UI.jsArg(value)`** for any value in a hand-written inline `onclick=` — **never bare `JSON.stringify`**, whose double quotes close the attribute and leave a dead handler (gated by `scripts/check_onclick_args.py`). `EOS_UI.entityCard({onClick})` is the opposite sink and wants a bare `JSON.stringify` — never copy a handler between them.
- **Status is never conveyed by colour alone** — pair it with a word (`.claude/rules/list-card-density.md`).
- Vault paths are always clickable via `EOS.noteActions(path)`.
