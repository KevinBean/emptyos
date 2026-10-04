# Chatbot service — VPS go-live checklist

Step-by-step to bring `chat.<yourdomain>` live. Assumes the VPS already has the demo daemon running (so Docker + Caddy + `/opt/emptyos` clone exist).

## Prerequisites

- [ ] Latest code shipped to public repo via `python scripts/release-public.py vX.Y.Z`
- [ ] DNS A record: `chat.<yourdomain>` → VPS IP, propagated (`dig chat.<yourdomain>`)
- [ ] OpenAI API key in hand (or whichever provider you'll use)
- [ ] At least one published site with `corpus.json` reachable (run `eos publish build` on a site that has `chatbot.enabled = true`)

## One-time setup on the VPS

```bash
ssh kevin@your-vps

# Pull the latest code (chatbot service comes with it)
cd /opt/emptyos
git pull

# Configure the chatbot service
cd services/chatbot
cp .env.example .env
nano .env
# Set:
#   OPENAI_API_KEY=sk-...
#   CHATBOT_ADMIN_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
# (Save the admin token — you'll paste it into emptyos.toml [apps.publish.chatbot] later)

cp sites.toml.example sites.toml
nano sites.toml
# At minimum, edit [sites.eos]:
#   allowed_origins = ["https://eos.binbian.net"]   ← your real published-site URL
#   corpus_url      = "https://eos.binbian.net/corpus.json"
# Add more [sites.<id>] blocks for additional published sites.

# Caddy vhost
sudo cp caddy.snippet /etc/caddy/sites/chatbot.caddy
# Or, if your Caddyfile is monolithic:
#   sudo cat caddy.snippet >> /etc/caddy/Caddyfile
sudo systemctl reload caddy

# Deploy
bash /opt/emptyos/scripts/deploy-service.sh chatbot
```

The deploy script reports `✓ healthy` when the container is up and `/health` returns 200. First-time `--build` takes 1–2 min.

## Verify

```bash
# From the VPS:
curl -fsS http://127.0.0.1:9100/health
# {"status":"ok","sites":["eos", ...]}

# From your laptop:
curl -fsS https://chat.<yourdomain>/health
# Same response — confirms Caddy + TLS works

# From your published site's browser console:
fetch('https://chat.<yourdomain>/chat', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    site_id: 'eos',
    messages: [{role: 'user', content: 'hi'}],
    session_id: crypto.randomUUID(),
  })
}).then(r => r.json()).then(console.log)
# Expect: {reply: "...", sources: [...], source: "model" or "faq", ...}
```

## Wire EmptyOS publish app to the service

In your local `emptyos.toml` (and on the VPS daemon if you want Q&A admin from there):

```toml
[apps.publish.chatbot]
endpoint    = "https://chat.<yourdomain>"
admin_token = "<the CHATBOT_ADMIN_TOKEN you generated above>"
```

Restart EmptyOS. Open the publish app → click 💬 Chatbot Q&A on a site that has `chatbot.enabled = true`. You should see pending visitor questions, with Approve / Reject / Promote actions.

## Enable on a published site

1. EmptyOS publish app → site settings → set `chatbot.enabled = true`, `endpoint = https://chat.<yourdomain>`, optionally fill `starter_questions`.
2. Rebuild + redeploy the static site (`eos publish build && eos publish deploy`).
3. The published page now ships `chatbot-widget.js` + the meta tags. Visitors see the floating bubble.
4. Hit `POST https://chat.<yourdomain>/admin/refresh/<site_id>` (with `X-Admin-Token`) to bust the corpus cache so the service picks up the new content immediately. Otherwise wait `corpus_ttl_seconds` (default 1h).

## Ongoing redeploys

```bash
# Just the chatbot (after pushing new code via release-public.py):
ssh kevin@vps "cd /opt/emptyos && git pull && bash scripts/deploy-service.sh chatbot"

# Whole fleet:
ssh kevin@vps "cd /opt/emptyos && bash scripts/redeploy-all.sh"
```

## Rollback

```bash
# Quick: stop the container without removing data
docker compose -f /opt/emptyos/services/chatbot/docker-compose.yml stop

# Real rollback: check out the previous git SHA and redeploy
ssh kevin@vps
cd /opt/emptyos
git log --oneline -10                  # find the last good SHA
git checkout <sha>
bash scripts/deploy-service.sh chatbot

# Return to head when fixed:
git checkout main
git pull
bash scripts/deploy-service.sh chatbot
```

The SQLite ledger persists across rollbacks (it's in a Docker volume, not the image). Curated Q&A entries survive.

## Troubleshooting

**Service starts but `/health` returns 5xx.** Check `docker compose logs chatbot`. Most likely: `sites.toml` malformed or `OPENAI_API_KEY` missing in `.env`.

**Browser console: 401 from `/chat`.** Origin mismatch. The site's actual origin (`https://eos.binbian.net`) must literally appear in `sites.toml` `allowed_origins`. Trailing slashes count; protocol counts.

**Browser console: 404 from `/chat`.** Wrong `site_id` in the request. Must match a `[sites.<id>]` block.

**Widget shows but never streams.** Open Network tab; check the SSE response. If Caddy is buffering, ensure the `caddy.snippet` matched (it sets `flush_interval -1` for `/chat/stream`).

**Q&A admin shows "chat service unreachable" in EmptyOS UI.** The publish app's `chatbot.endpoint` config doesn't match the actual service URL, or the `admin_token` doesn't match. Both must be set in `emptyos.toml` `[apps.publish.chatbot]`.

## What's NOT shipped on first deploy

- Cloudflare Turnstile (Slice 2.5+ — only if abuse appears)
- RAG / embeddings (Slice 2.5+ — only when first site corpus exceeds prompt-stuff threshold)
- Multi-provider routing (provider abstraction in place, only OpenAI implemented)
- Tag-pinned deploys (currently follows `main`; revisit after first release if needed)
