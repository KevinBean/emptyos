#!/usr/bin/env bash
# Runs every time the codespace starts (initial create + every resume).
# Boots the EmptyOS daemon in the background and waits for port 9000.

set -euo pipefail

echo ""
echo "── EmptyOS daemon boot ─────────────────────────"

# Kill any leftover daemon from a prior session (paranoid; harmless if none).
pkill -f "emptyos.cli" 2>/dev/null || true
pkill -f "uvicorn.*9000" 2>/dev/null || true
sleep 1

mkdir -p data/logs
LOG=data/logs/daemon.log

# Re-generate emptyos.toml on every start so a newly-added Codespaces
# secret takes effect on the next 'Rebuild & Reload' (no manual editing).
if [ -f .devcontainer/setup.sh ] && [ ! -f emptyos.toml ]; then
    bash .devcontainer/setup.sh
fi

echo "▶ starting daemon → $LOG"
nohup python -m emptyos start > "$LOG" 2>&1 &
DAEMON_PID=$!
echo "$DAEMON_PID" > data/logs/daemon.pid

# Wait for port 9000 to respond (max 45s).
echo -n "▶ waiting for port 9000 "
for i in $(seq 1 45); do
    if curl -fsS -o /dev/null http://localhost:9000/api/health 2>/dev/null; then
        echo " ✓"
        break
    fi
    echo -n "."
    sleep 1
done

if ! curl -fsS -o /dev/null http://localhost:9000/api/health 2>/dev/null; then
    echo ""
    echo "⚠ daemon did not respond in 45s. Tail the log:"
    echo "    tail -f data/logs/daemon.log"
    exit 0  # don't fail the container — let user investigate
fi

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Improv app is live."
echo ""
echo "  In VS Code: open the PORTS panel (bottom bar)."
echo "  Tap the 🌐 globe next to port 9000 → opens in a new tab."
echo "  Once open, navigate to /improv/ for the app."
echo ""
echo "  Logs:  tail -f data/logs/daemon.log"
echo "  Stop:  kill \$(cat data/logs/daemon.pid)"
echo "════════════════════════════════════════════════════════"
echo ""
