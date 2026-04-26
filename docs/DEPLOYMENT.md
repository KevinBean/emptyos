# Deployment Guide

Three deployment shapes, same codebase. Pick the one that matches your need.

| Shape | Who reaches it | Reference |
|---|---|---|
| **Local** | Just you, on your laptop | [GETTING-STARTED.md](GETTING-STARTED.md) |
| **Self-hosted (private)** | You + trusted devices on a VPN | [§ Self-hosted on a VPS](#self-hosted-on-a-vps) |
| **Public demo** | Anyone with the URL | [§ Public demo](#public-demo) |

This guide covers the second and third — the first one is just `eos start`.

---

## Self-hosted on a VPS

### What you'll end up with

- EmptyOS daemon running in Docker on a small VPS
- HTTPS via Caddy (automatic Let's Encrypt certs)
- Auth token gates every request
- Reachable at `https://eos.yourdomain.com`
- Your vault persists across restarts in a Docker volume (or a host bind-mount)

Reference target: a **Hetzner CX22** (€4.51/mo, 2 vCPU, 4 GB RAM) running Ubuntu 24.04. Any small VPS with Docker works — DigitalOcean's $6 droplet, Linode's Nanode, a $5 Vultr instance. CPU-only LLM inference (Ollama with `phi3:mini` or similar) is the floor; if you want decent local AI, size up to 8 GB RAM or BYOK to a cloud provider.

### Step 1 — Provision the VPS

```bash
# On your local machine, create the VPS via your provider's UI or CLI.
# SSH in:
ssh root@your.vps.ip

# Create a non-root user (recommended)
adduser eos
usermod -aG sudo eos
su - eos
```

### Step 2 — Install Docker + Caddy

```bash
# Docker (official one-liner)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

# Caddy (Debian/Ubuntu)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
  sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
  sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

### Step 3 — Point your domain

Add an A record at your DNS provider:

```
eos.yourdomain.com.   A   your.vps.ip
```

Wait for propagation (`dig eos.yourdomain.com` should return your VPS IP).

### Step 4 — Clone EmptyOS and configure

```bash
git clone https://github.com/KevinBean/emptyos.git
cd emptyos

# Copy the example config and edit it
cp emptyos.toml.example emptyos.toml
```

Edit `emptyos.toml` for private mode:

```toml
[notes]
path = "/vault"             # mounted from a Docker volume in step 5
watch = true

[network]
mode = "private"
port = 9000
# auth_token comes from the EOS_NETWORK_AUTH_TOKEN env var — don't put it here

[cloud]
consent = "ask"             # default; safest for a private instance
```

Generate an auth token and save it to `.env`:

```bash
echo "EOS_NETWORK_AUTH_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > .env
chmod 600 .env
```

Save the token value somewhere safe (password manager) — you'll need it on every device that calls the daemon.

### Step 5 — Boot the daemon

`docker-compose.yml` ships with the right shape — a vault volume, the config bind-mounted read-only, the data volume persisted, and the auth token threaded in from `.env`.

```bash
docker compose --env-file .env up -d
docker compose logs -f emptyos     # watch it boot
```

Health check:

```bash
curl -fsS -H "X-Auth-Token: $(grep EOS_NETWORK_AUTH_TOKEN .env | cut -d= -f2)" \
  http://127.0.0.1:9000/api/health
```

### Step 6 — Caddy in front of it

Edit `/etc/caddy/Caddyfile`:

```caddy
eos.yourdomain.com {
    reverse_proxy 127.0.0.1:9000
    encode gzip

    # Optional: rate limit unauthenticated probes
    @no_token not header X-Auth-Token *
    handle @no_token {
        respond "unauthorized" 401 {
            close
        }
    }
}
```

Reload Caddy:

```bash
sudo systemctl reload caddy
sudo systemctl status caddy
```

Caddy will request a Let's Encrypt cert automatically the first time `eos.yourdomain.com` is requested over HTTPS.

### Step 7 — Verify

From your laptop:

```bash
curl -fsS -H "X-Auth-Token: <your-token>" https://eos.yourdomain.com/api/health
```

Open `https://eos.yourdomain.com/?token=<your-token>` in a browser. The daemon stores the token in `localStorage` after the first authenticated load.

### Operating notes

- **Updating**: `git pull && docker compose --env-file .env up -d --build`
- **Backups**: snapshot the `emptyos-data` Docker volume + your vault. The vault is plain markdown — `rsync` it to a backup target.
- **Logs**: `docker compose logs -f emptyos`; system events at `https://eos.yourdomain.com/system`
- **Hardening**: `ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw enable` — block direct access to port 9000

---

## Public demo

The reference public demo is engineered for **$0/month AI cost** and **frictionless visitor onboarding**. The ingredients:

- **Local Ollama (`phi3:mini`)** as the default LLM — slow but free, runs on the VPS CPU
- **BYOK (Bring Your Own Key)** for cloud quality — visitors paste their OpenAI/Anthropic key in Settings; the key is session-scoped in browser storage and never touches your server
- **No server-side cloud key** in the shipped config — your bill stays $0 for AI even if every visitor wanted gpt-4 quality
- **Public auth_token** pre-filled in the landing-page link — deters bot scraping, zero friction for humans
- **Cloudflare in front** (free tier) — DDoS protection + rate limiting without Caddy plugins
- **Daily reset cron** — wipes user-created content every morning so the demo starts clean

Total cost: **~€4.50/month for the VPS, $0 for AI, $0 for Cloudflare.**

Reference setup is fully scripted in `docker-compose.demo.yml` + `demo/emptyos.toml`.

### Step 1 — Seed the demo vault

```bash
python scripts/demo-setup.py --output ./demo/vault --force
```

This creates a sanitized vault with sample tasks, journal entries, projects, and notes. The repo also ships `demo/vault/` with a baseline you can use as-is.

### Step 2 — Generate the auth token

```bash
echo "EOS_NETWORK_AUTH_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" > .env.demo
chmod 600 .env.demo
```

The token is **public** — it's how visitors access the demo, not a real secret. Treat it like a "are you human?" gate. Two ways to share it:

- **Open demo (recommended for HN / Twitter / portfolio):** pre-fill it in your landing-page link. First load sets `localStorage`; visitors never see the token themselves.
  ```
  https://demo.<yourdomain>/?token=<EOS_NETWORK_AUTH_TOKEN>
  ```
- **Soft password (recommended for recruiters / clients):** publish the token in your README and visitors copy-paste once. Light gate, easy to share, easy to revoke (regenerate the token + restart).

### Step 3 — Boot

```bash
docker compose -f docker-compose.demo.yml --env-file .env.demo up -d
docker exec emptyos-demo-ollama ollama pull phi3:mini
```

### Step 4 — Caddy

```caddy
demo.yourdomain.com {
    reverse_proxy 127.0.0.1:9000
    encode gzip
}
```

That's it for the proxy — rate limiting comes from Cloudflare in step 6.

### Step 5 — Reset cadence

The demo vault (`./demo/vault`) is bind-mounted into the container, so visitor-created notes persist across restarts on the host filesystem. To actually wipe them, the daily reset has to discard the working-tree changes (`git checkout`) and then restart the container:

```cron
0 4 * * *  cd /opt/emptyos && git checkout demo/vault/ && python3 scripts/refresh-demo-dates.py && docker compose -f docker-compose.demo.yml --env-file .env.demo restart emptyos-demo >> /var/log/emptyos-demo-reset.log 2>&1
```

The `refresh-demo-dates.py` step shifts every 📅 / ✅ ISO date in `demo/vault/` by `today - SEED_BASELINE` days, preserving the relative spread (overdue / today / week / done buckets) but anchoring everything to today. Without this, the seed dates drift further from "today" each day until the pulse-stats panel looks broken. Skip it only if you don't want date-relative tasks.

What this does:
1. `git checkout demo/vault/` — discards any visitor edits, restoring the seeded content from the latest committed state
2. Container restart — clears the daemon's in-memory state (sessions, autoloaded apps, anything cached)

The container's own `[demo].reset_on_restart = true` flag handles the daemon's `data/` directory (events DB, syslog DB) — those wipe on restart automatically. Vault files need the explicit `git checkout` because bind mounts bypass the daemon's filesystem layer.

If you ever update `demo/vault/` content (add new sample notes via `git pull` from a release), the cron picks them up automatically — `git checkout` always pulls from the current HEAD's state.

### Step 5.5 — Demo data lifecycle

The demo's vault content (`demo/vault/` in this repo) is bind-mounted into the container at `/vault`. Three operations you'll do over time:

#### Get new seed files from a fresh release onto the VPS

When you ship a release that adds notes to `demo/vault/`:

```bash
cd /opt/emptyos
git pull origin main
docker compose -f docker-compose.demo.yml --env-file .env.demo restart emptyos-demo
```

The bind-mount means new files appear immediately; restart clears in-memory state.

#### Manually reset the demo to clean seeded state right now

When visitors have polluted the vault and you want it pristine before a daily cron fires:

```bash
cd /opt/emptyos
git checkout demo/vault/                    # discard all visitor edits
docker compose -f docker-compose.demo.yml --env-file .env.demo restart emptyos-demo
```

`git checkout demo/vault/` reverts the directory to its committed state (i.e. throws away anything visitors created/edited that isn't in git). Then restart clears the daemon's session state.

#### Automate the reset (daily cron)

Edit the crontab:

```bash
crontab -e
```

Add (or update existing demo-restart line to match):

```cron
0 4 * * *  cd /opt/emptyos && git checkout demo/vault/ && docker compose -f docker-compose.demo.yml --env-file .env.demo restart emptyos-demo >> /var/log/emptyos-demo-reset.log 2>&1
```

Verify:

```bash
crontab -l | grep emptyos
```

Now every 04:00 UTC: discard visitor edits → restart → demo serves the seed content unchanged.

#### Add more seed content

1. Edit / add files under `demo/vault/` locally in `D:/emptyos`
2. Commit + push to private + `python scripts/release-public.py vX.Y.Z`
3. On VPS: `git pull origin main` (or wait for the daily cron to do `git checkout`, which pulls the latest committed state)

The personal-data scan (`scripts/check-personal.py`) runs at release time, so any sensitive content accidentally added gets caught before shipping.

#### Why bind-mount + git, not a Docker volume?

Two reasons:
- **Inspectable**: you can read demo content directly on the host (`cat demo/vault/...`) without docker exec
- **Versioned**: the seed lives in git; updates ship via normal release flow; rollback is `git checkout` to a tag
- The trade-off: visitor edits persist on the host until `git checkout` discards them. The daily cron handles this automatically.



Caddy's built-in rate limiter requires a custom build. Cloudflare's free tier gives you the same protection plus DDoS shielding without touching Caddy.

1. Add `demo.<yourdomain>` to a Cloudflare zone (the apex domain doesn't need to be on Cloudflare — only the demo subdomain).
2. DNS record: `demo` → your VPS IP, with the **orange cloud icon ON** (proxied).
3. In Cloudflare → Security → WAF → Rate limiting rules:
   - Match: `(http.request.uri.path contains "/")`
   - Rate: 60 requests per 1 minute per source IP
   - Action: Challenge (Managed Challenge — friction-free for humans, blocks bots)
4. In Cloudflare → SSL/TLS → set mode to **Full (strict)**. Your Caddy + Let's Encrypt cert handles the origin side.

This catches scrapers and abusive traffic before it reaches your VPS. Visitors hitting the demo through a normal browser see no friction.

### Step 6.5 — Redeploying after upstream changes

When the upstream repo gets a new release and you want to update the demo, the script `scripts/redeploy-demo.sh` does the safe sequence in one command:

```bash
cd /opt/emptyos
bash scripts/redeploy-demo.sh
```

What it does:
1. `git fetch + git reset --hard origin/main` — force-syncs local to public, handles any history divergence (e.g. if public was force-pushed during a snapshot transition)
2. `docker compose down`
3. `docker compose build --no-cache` — full rebuild, so dep changes in `pyproject.toml` actually take effect (Docker's layer cache otherwise reuses stale `pip install` results)
4. `docker compose up -d`
5. Polls `/api/health` for up to 60s
6. Smoke-checks that critical Python deps (`fastapi`, `edge_tts`, `multipart`, etc.) are installed
7. Greps the startup log for known-good / known-bad lines

Safe to re-run. Doesn't touch the data volume — user-created content survives across rebuilds and is wiped only by the daily reset cron.

If you'd rather run the steps manually:

```bash
cd /opt/emptyos
git fetch origin && git reset --hard origin/main
docker compose -f docker-compose.demo.yml --env-file .env.demo down
docker compose -f docker-compose.demo.yml --env-file .env.demo build --no-cache
docker compose -f docker-compose.demo.yml --env-file .env.demo up -d
```

The `--no-cache` is the load-bearing bit — without it, Docker may reuse the cached pip-install layer and silently keep an old set of dependencies.

### Step 7 — Cost ceiling (defense in depth)

`demo/emptyos.toml` ships with `[billing.budgets]` set to $5/month for OpenAI + Anthropic. Even though you're not shipping a server-side cloud key, this guarantees that if you (or a fork) ever does, the spend is capped. `apps/billing/` disables the provider when the budget trips.

If you want to fund a server-side cloud fallback (so visitors get quality without BYOK), set the provider's API key in `.env.demo`:

```bash
echo "OPENAI_API_KEY=sk-..." >> .env.demo
```

…and add `openai` to the providers list in `demo/emptyos.toml`:

```toml
[capabilities.think]
providers = ["ollama", "openai"]   # tries local first, falls back to cloud
```

The $5 monthly budget is your safety net — once tripped, OpenAI drops out and visitors fall back to Ollama again. Adjust the cap to whatever you're willing to pay.

### Demo-specific cautions

- **Don't reuse this config on a personal instance.** `cloud.consent = "always"` and `billing.budgets` defaults are demo-calibrated. On your real machine, set `cloud.consent = "ask"` and either remove the budgets or raise them.
- **Don't put real vault content in `demo/vault/`.** Anything mounted under `/vault` is visible to every visitor — treat it as public.
- **Don't ship a server-side cloud key by default.** Visitors will use it freely. BYOK is the safe path; explicit funded fallback is opt-in (Step 7).
- **Rotate the auth_token periodically.** Even though it's public, rotation gives you a way to invalidate stale bookmarks if abuse patterns emerge.

---

## Sizing

| Workload | Min RAM | Notes |
|---|---|---|
| EmptyOS daemon alone | 512 MB | API + apps + vault watcher |
| + small Ollama model (`phi3:mini`, `qwen2.5:1.5b`) | 4 GB | CPU inference, slow but works |
| + medium Ollama model (`llama3.1:8b`, `qwen2.5:7b`) | 8 GB | Tolerable on CPU; usable on a small GPU |
| + ComfyUI image generation | 16 GB + GPU | Don't try this on a CPU VPS |

For demos, BYOK to OpenAI/Anthropic is cheaper than running a big local model — visitors paste their own key, you pay for nothing.

---

## Troubleshooting

**Daemon won't start in public mode.** `EOS_NETWORK_AUTH_TOKEN` is empty or missing. Check `.env` and `docker compose config | grep EOS_NETWORK_AUTH_TOKEN`.

**HTTPS works but every request returns 401.** You're not sending the auth token. Browsers store it in `localStorage` after the first authenticated load — visit `https://your.domain/?token=<your-token>` once.

**Caddy fails to get a cert.** Port 80 must be reachable from the public internet for the HTTP-01 challenge. Check `ufw status` and your VPS firewall rules.

**Ollama is slow.** CPU inference on a small VPS is slow. Either upgrade to a GPU instance, switch to a smaller model (`phi3:mini` is the floor), or BYOK a cloud provider.

**Vault changes don't appear.** The watcher only sees files written by the host. If you're editing the vault from another container or a remote sync, make sure the file events propagate — bind mounts work, named volumes work, NFS often doesn't.
