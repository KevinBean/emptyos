

| URL | What |
|---|---|
| `http://localhost:9000/` | Home — app launcher, stats, events |
| `http://localhost:9000/topology` | Live dependency graph |
| `http://localhost:9000/system` | Capability Inspector — providers, status, recovery hints |
| `http://localhost:9000/docs` | FastAPI Swagger — all API routes |
| `http://localhost:9000/{app}/` | App UI (custom or auto-generated) |
| `ws://localhost:9000/ws` | WebSocket — live events |

```bash
eos                     # System status
eos start               # Boot daemon (port 9000)
eos health              # Full health check
eos app list            # All apps
eos app info <id>       # Self-documenting app details

# App commands (auto-routed to running daemon, or local kernel)
eos capture "idea"
eos task list
eos journal add "good day" good
eos search "cable rating"
```

CLI detects running daemon → proxies via HTTP (shared kernel). No daemon → falls back to local kernel.
