#!/usr/bin/env bash
# redeploy-all.sh — pull latest git + redeploy every service in services/*.
#
# Usage:  bash scripts/redeploy-all.sh
#
# Order:
#   1. git fetch + reset --hard origin/main (force-sync)
#   2. For every services/<name>/ that has a service.toml + .env, run
#      deploy-service.sh <name>. Skip directories without an .env (they're
#      not configured for this host).
#   3. Reload Caddy at the end if any service has a caddy.snippet.
#
# Use this on the VPS when shipping a release that touches multiple services
# (e.g. chatbot v0.2 + a future webhook service v0.1). For deploying a single
# service, prefer `deploy-service.sh <name>` directly.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "═══ Pulling latest from main ═══"
git fetch origin
git reset --hard origin/main
echo

# Find all services with both service.toml and .env (= configured + ready).
SERVICES=()
for dir in services/*/; do
  name="$(basename "$dir")"
  if [ -f "$dir/service.toml" ] && [ -f "$dir/.env" ]; then
    SERVICES+=("$name")
  elif [ -f "$dir/service.toml" ]; then
    echo "⏭  Skipping $name — service.toml present but .env missing (not configured on this host)"
  fi
done

if [ ${#SERVICES[@]} -eq 0 ]; then
  echo "No deployable services found in services/*/"
  exit 0
fi

echo "═══ Deploying ${#SERVICES[@]} service(s): ${SERVICES[*]} ═══"
echo

FAILED=()
for name in "${SERVICES[@]}"; do
  echo
  if bash "$REPO_ROOT/scripts/deploy-service.sh" "$name"; then
    :
  else
    FAILED+=("$name")
  fi
done

# Reload Caddy if any service ships a snippet.
if ls services/*/caddy.snippet >/dev/null 2>&1; then
  echo
  echo "═══ Reloading Caddy ═══"
  if command -v systemctl >/dev/null; then
    sudo systemctl reload caddy || echo "⚠  Caddy reload failed; check `sudo systemctl status caddy`"
  else
    echo "⚠  systemctl not available — reload Caddy manually"
  fi
fi

echo
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "═══ ✓ All services deployed ═══"
else
  echo "═══ ✗ Failed: ${FAILED[*]} ═══" >&2
  exit 1
fi
