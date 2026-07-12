#!/usr/bin/env bash
# deploy-service.sh — deploy or redeploy a Lane-1 service.
#
# Usage:  bash scripts/deploy-service.sh <service-name>
# Reads:  services/<name>/service.toml
#         services/<name>/.env  (created from .env.example)
#         services/<name>/docker-compose.yml
#
# Behavior:
#   1. Validates that .env exists (refuses to deploy without it)
#   2. docker compose --env-file .env up -d --build
#   3. Polls healthcheck endpoint (from service.toml) until 200 or timeout
#   4. Reports container status; exit 0 on success, non-zero on failure
#
# This script is the canonical deploy mechanism for Lane 1. See
# docs/DEPLOYMENT.md § "Lane 1 — Service" for the full standard.

set -euo pipefail

NAME="${1:-}"
if [ -z "$NAME" ]; then
  echo "Usage: $0 <service-name>" >&2
  echo "Available services:" >&2
  ls -d "$(dirname "$0")/../services/"*/ 2>/dev/null | xargs -n1 basename 2>/dev/null || true
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SVC_DIR="$REPO_ROOT/services/$NAME"

if [ ! -d "$SVC_DIR" ]; then
  echo "✗ services/$NAME/ does not exist" >&2
  exit 1
fi

cd "$SVC_DIR"

# ── Validate required files ──────────────────────────────────────
for f in service.toml docker-compose.yml; do
  if [ ! -f "$f" ]; then
    echo "✗ services/$NAME/$f missing — see docs/DEPLOYMENT.md § Lane 1" >&2
    exit 1
  fi
done

if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    echo "✗ services/$NAME/.env missing. Copy from .env.example and fill in:" >&2
    echo "    cd services/$NAME && cp .env.example .env && nano .env" >&2
  else
    echo "✗ services/$NAME/.env missing and no .env.example to copy from" >&2
  fi
  exit 1
fi

# ── Parse service.toml (lightly — Python, no extra deps) ─────────
read_toml() {
  python3 - "$1" "$2" <<'PYEOF'
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib
key_path, default = sys.argv[1].split("."), sys.argv[2] if len(sys.argv) > 2 else ""
with open("service.toml", "rb") as f:
    data = tomllib.load(f)
node = data
for k in key_path:
    if isinstance(node, dict) and k in node:
        node = node[k]
    else:
        node = default
        break
print(node if node is not None else default)
PYEOF
}

VHOST=$(read_toml "service.vhost" "")
PORT=$(read_toml "service.port" "")
HEALTH_PATH=$(read_toml "service.healthcheck" "/health")
HEALTH_TIMEOUT=$(read_toml "service.healthcheck_timeout" "30")
BUILD_FLAG=$(read_toml "deploy.build" "true")

echo "═══ Deploying service: $NAME ═══"
[ -n "$VHOST" ] && echo "  vhost:        $VHOST"
[ -n "$PORT" ] && [ "$PORT" != "0" ] && echo "  host port:    127.0.0.1:$PORT"
[ -n "$HEALTH_PATH" ] && echo "  healthcheck:  $HEALTH_PATH"
echo

# ── Docker compose up ────────────────────────────────────────────
COMPOSE_ARGS=(--env-file .env)
UP_ARGS=(-d)
if [ "$BUILD_FLAG" = "True" ] || [ "$BUILD_FLAG" = "true" ]; then
  UP_ARGS+=(--build)
fi

echo "→ docker compose ${COMPOSE_ARGS[*]} up ${UP_ARGS[*]}"
docker compose "${COMPOSE_ARGS[@]}" up "${UP_ARGS[@]}"
echo

# ── Healthcheck poll ─────────────────────────────────────────────
if [ -z "$HEALTH_PATH" ] || [ -z "$PORT" ] || [ "$PORT" = "0" ]; then
  echo "✓ Service started (worker variant — no healthcheck)"
  exit 0
fi

URL="http://127.0.0.1:$PORT$HEALTH_PATH"
echo "→ polling $URL (up to ${HEALTH_TIMEOUT}s)..."
DEADLINE=$(($(date +%s) + HEALTH_TIMEOUT))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if curl -fsS "$URL" >/dev/null 2>&1; then
    echo "✓ healthy"
    echo
    echo "═══ Done — $NAME is up ═══"
    [ -n "$VHOST" ] && echo "  Reachable via: https://$VHOST (after Caddy reload)"
    exit 0
  fi
  sleep 2
done

echo "✗ Healthcheck did not pass within ${HEALTH_TIMEOUT}s" >&2
echo "  Container logs:" >&2
docker compose --env-file .env logs --tail=40 >&2
exit 1
