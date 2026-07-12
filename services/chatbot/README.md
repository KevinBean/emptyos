# EmptyOS Chatbot Service

Standalone FastAPI service that answers questions about sites built by the EmptyOS `publish` app.

**Lane 1 service — no vault.** Independent of the EmptyOS daemon: sites keep working when this is down; this keeps working when the daemon is down. Deployed at `chat.<yourdomain>` behind Caddy. See `docs/DEPLOYMENT.md` § "Lane 1 — Service" for the conventions this directory follows.

## What it does

1. Accepts `POST /chat` from a whitelisted site origin.
2. Fetches that site's `corpus.json` (built by `apps/publish/builder.py`).
3. Tries an FAQ pre-bake (free, instant).
4. Falls through to OpenAI — concise reply, capped tokens, capped daily $.
5. Records every call in a SQLite ledger.

## Anti-abuse

| Layer | Mechanism |
|---|---|
| Origin lock | Reject if `Origin` header not in site's allowlist (401) |
| Token caps | Reject input > `max_input_chars`; cap output at `max_output_tokens` |
| Per-IP rate limit | SQLite ledger, 20/hour + 60/day per IP (429) |
| Per-site $ cap | Reject when site hits `daily_cap_usd` (429, retry after midnight UTC) |
| Global $ cap | Reject when sum across sites > `global_cap_usd` (last-line) |
| Topic gate | System prompt instructs model to refuse off-topic |
| Logging | Every call → SQLite `requests` table |

## Quick start (local)

```bash
cd services/chatbot
pip install -e .

cp sites.toml.example sites.toml  # edit per-site config
export OPENAI_API_KEY=sk-...
export CHATBOT_ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

uvicorn main:app --host 127.0.0.1 --port 8000
```

Test:

```bash
# Health
curl -s http://127.0.0.1:8000/health

# Chat (must include Origin matching sites.toml allowed_origins)
curl -s -X POST http://127.0.0.1:8000/chat \
  -H "Origin: https://eos.binbian.net" \
  -H "Content-Type: application/json" \
  -d '{"site_id":"eos","messages":[{"role":"user","content":"What is EmptyOS?"}],"session_id":"test"}'
```

Expected negative responses:
- Missing/wrong `Origin` header → 401
- Message > 4000 chars → 400
- 21 requests in <1h from same IP → 429 on 21st
- Cumulative $ > daily cap → 429 with `Retry-After` header

## Quick start (Docker, via standard deploy script)

The Lane-1 standard says: every service deploys with `scripts/deploy-service.sh <name>`. For the chatbot:

```bash
# One-time setup on a host
cd services/chatbot
cp .env.example .env && nano .env          # set OPENAI_API_KEY + CHATBOT_ADMIN_TOKEN
cp sites.toml.example sites.toml && nano sites.toml
sudo cp caddy.snippet /etc/caddy/sites/chatbot.caddy
sudo systemctl reload caddy

# Deploy (idempotent — safe to re-run)
bash ../../scripts/deploy-service.sh chatbot
```

The deploy script reads `service.toml`, runs `docker compose --env-file .env up -d --build`, polls `/health` until 200, and reports status. Service binds to `127.0.0.1:9100` on the host.

For a one-off ad-hoc run without the script:
```bash
docker compose --env-file .env up -d --build
docker compose logs -f
```

## Refresh corpus cache

When you rebuild a site and want the new content live immediately (otherwise wait for `corpus_ttl_seconds`):

```bash
curl -X POST https://chat.yourdomain.com/admin/refresh/eos \
  -H "X-Admin-Token: <CHATBOT_ADMIN_TOKEN>"
```

## Files

| File | Role |
|---|---|
| `service.toml` | Lane-1 manifest — name, vhost, port, healthcheck (read by `deploy-service.sh`) |
| `caddy.snippet` | Drop-in Caddy vhost block; copy into `/etc/caddy/sites/chatbot.caddy` |
| `.env.example` | Documented env vars; copy to `.env` and fill in |
| `sites.toml.example` | Per-site config template; copy to `sites.toml` and fill in |
| `docker-compose.yml` | Compose stack (no `<name>` infix — Lane-1 standard) |
| `Dockerfile` | Image build |
| `main.py` | FastAPI app + routes + middleware-shaped gates |
| `config.py` | Loads `sites.toml` |
| `corpus.py` | Per-site corpus fetch, in-memory TTL cache, FAQ matcher |
| `ledger.py` | SQLite — rate limits + $ tracking + Q&A log |
| `providers/openai_provider.py` | OpenAI chat-completions impl |
| `providers/base.py` | Provider ABC for swapping backends later |

## Adding a new site

1. Build the site with EmptyOS publish app — `corpus.json` lands at the site root.
2. Add a `[sites.<id>]` block to `sites.toml`.
3. Restart the service (or `docker compose restart chatbot`).
4. Hit `/admin/refresh/<id>` to skip the cache wait.

## Adding a new LLM provider

1. New file `providers/<name>_provider.py` implementing `Provider` from `providers/base.py`.
2. Register in `providers/__init__.py:get_provider`.
3. Add pricing dict if charging per-token.
4. Set `defaults.provider = "<name>"` in `sites.toml` (or override per-site — TODO when 2nd provider lands).
