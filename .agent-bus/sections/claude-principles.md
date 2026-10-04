

1. **Everything can be generated** — apps, UIs, configs, pipelines
2. **Everything is reusable** — extract shared work into the platform (`sdk/`)
3. **Everything is connected** — event bus over imports; topology graph IS the architecture
4. **Atomic code, like atomic notes** — apps are atoms (manifest + app.py); value is in connections
5. **Self-testing, self-fixing** — health checks + graceful fallback
6. **The system is expressive** — every app has a UI (custom or auto-generated)
7. **Self-documenting** — `eos app info <id>` generates docs from manifest + code; no separate dev docs
8. **Vault is external** — mounted, swappable, human-readable
9. **Reactive vault population** — `git:saved` → reactor → journal entry; one action ripples to related notes
10. **The system is alive** — Growth Agent + vault emergence + self-audit loop
