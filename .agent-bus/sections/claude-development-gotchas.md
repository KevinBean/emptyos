

The architecturally load-bearing ones. Generic quirks: `.claude/rules/dev-gotchas.md`.

- **Vault read-modify-write races**: any `read → mutate → write` yields the loop between awaits, and the reactor subscribes ~30 events, so a POST and a handler can clobber each other. Serialize a **vault note** with `async with self.note_lock(path)` (kernel-wide, keyed by vault-relative path — excludes across apps); `self.write_lock(key)` is per-app-instance, for everything else. Journal's `_daily_lock()` is the reference; `vault_set_body` and quick-action/expense/learn/people take `note_lock`, and `VaultIndex` logs a `race candidate` warning when a write lands under another task's lock. Keep emits **outside** the lock so handlers can recurse through `call_app` without deadlocking.
- **Vault frontmatter tags must be block-style**: `tags:\n  - a\n  - b`, not `tags: [a, b]`. One parser, `emptyos/frontmatter.py`: `key:` and `key: ""` read `""`, `key: []` reads `[]` — `str([])` is the truthy `"[]"`, which is how an unfilled field used to pass `if not value`.
- **Normalize loose field shapes at the write boundary**: when callers pass a string where `dict | None` is typed, coerce once in the write function with a small `_coerce_<field>()` helper, not at every read site.
- **The daemon process is user-owned**: never run `restart.bat`/`stop.bat`, `python -m emptyos start`, `taskkill` python, or delete `data/*.db*` against `:9000` / `:9001`. Probe via `curl`, read `data/daemon.err.log`, surface diagnoses; verify Python changes on a leased sandbox member. `.claude/rules/daemon-handling.md`.
