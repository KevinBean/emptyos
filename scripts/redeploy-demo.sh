#!/bin/bash
# Redeploy the EmptyOS public demo VPS to the latest public-repo state.
#
# Usage (run on the demo VPS, in the repo dir, e.g. /opt/emptyos):
#   bash scripts/redeploy-demo.sh
#
# What it does (in order):
#   1. Force-sync the local repo to origin/main
#      (handles the case where public history was force-pushed during a
#      snapshot transition and `git pull` would otherwise refuse / silently
#      stay on stale state)
#   2. Stop the running containers
#   3. Build images with --no-cache (so dep changes in pyproject.toml take
#      effect — Docker's layer cache otherwise reuses stale pip installs)
#   4. Start containers detached
#   5. Wait for the daemon to report healthy
#   6. Smoke-check: import critical deps, ping /api/health, look for
#      "no provider" / "Connected" lines in the logs
#
# This script is safe to re-run. It does NOT delete the demo data volume;
# user-created content from the previous deploy survives the rebuild and
# is wiped only by the daily reset cron (or by manually `down -v`).
#
# First-time deploy (NOT this script): see docs/DEPLOYMENT.md § Public demo.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/emptyos}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.demo.yml}"
ENV_FILE="${ENV_FILE:-.env.demo}"
SERVICE="${SERVICE:-emptyos-demo}"

cd "$REPO_DIR"

step() { echo; echo "  -> $*"; }
ok()   { echo "    OK: $*"; }
fail() { echo "    FAIL: $*" >&2; exit 1; }

# -- 1. Sync repo --
step "Force-sync repo to origin/main (current: $(git log --oneline -1))"
git fetch origin
git reset --hard origin/main
ok "now at $(git log --oneline -1)"

# -- 2. Stop --
step "Stop running containers"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down
ok "stopped"

# -- 3. Build (no cache so dep changes land) --
step "Rebuild images (--no-cache; ~3-5 min)"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build --no-cache
ok "built"

# -- 4. Start --
step "Start containers"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
ok "started"

# -- 5. Wait for daemon health --
step "Wait for daemon health (up to 60s)"
TOKEN=$(grep EOS_NETWORK_AUTH_TOKEN "$ENV_FILE" | cut -d= -f2)
for i in $(seq 1 30); do
    if curl -fsS -H "X-Auth-Token: $TOKEN" -m 3 http://127.0.0.1:9000/api/health >/dev/null 2>&1; then
        ok "daemon healthy after ${i}s"
        break
    fi
    sleep 2
    if [ "$i" -eq 30 ]; then
        echo "    daemon didn't respond in 60s -- check logs:"
        docker logs "$SERVICE" 2>&1 | tail -30
        fail "daemon not healthy"
    fi
done

# -- 6. Smoke-check critical deps + provider state --
step "Verify critical Python deps are present"
docker exec "$SERVICE" python -c "
import sys
ok = True
for mod in ('fastapi', 'uvicorn', 'multipart', 'edge_tts', 'aiohttp', 'apscheduler'):
    try:
        __import__(mod)
        print(f'    OK: {mod}')
    except ImportError as e:
        print(f'    MISSING: {mod} ({e})', file=sys.stderr)
        ok = False
sys.exit(0 if ok else 1)
" || fail "one or more critical packages missing -- pip install didn't run cleanly"

step "Look for known-good / known-bad lines in startup logs"
LOGS=$(docker logs "$SERVICE" 2>&1 | tail -100)
echo "$LOGS" | grep -iE "ollama.*connected|web dashboard at|no provider|missing|error|warning" | head -20 || echo "    (no notable lines)"

step "Done"
echo "    Demo is live. Refresh the browser at your demo URL."
echo "    Daily reset cron should be in: crontab -l"
