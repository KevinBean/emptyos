#!/usr/bin/env bash
# EmptyOS Restart — cross-platform (macOS / Linux / RPi)
#
# macOS notes:
#   - Interpreter defaults to `python3`; override with EOS_PYTHON=/path/to/python.
#   - Make it double-clickable in Finder by symlinking to restart.command:
#       ln -s restart.sh restart.command
#   - Background services are started with nohup + disown so they survive this
#     script exiting (the macOS equivalent of the .bat's `start /b`).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=9000
PY="${EOS_PYTHON:-python3}"

echo "============================================"
echo "  EmptyOS Restart"
echo "============================================"

# ── 1/3  Stop EmptyOS ────────────────────────────

echo ""
echo "[1/3] Stopping EmptyOS..."

# Find PID listening on the port
if command -v lsof &>/dev/null; then
    PID=$(lsof -ti :"$PORT" 2>/dev/null || true)
elif command -v ss &>/dev/null; then
    PID=$(ss -tlnp "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K\d+' || true)
else
    PID=$(fuser "$PORT/tcp" 2>/dev/null || true)
fi

if [ -n "${PID:-}" ]; then
    echo "  Killing PID $PID (port $PORT)"
    kill $PID 2>/dev/null || true
    # Wait up to 15 seconds for port to free
    for i in $(seq 1 15); do
        if ! lsof -ti :"$PORT" &>/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    # Force kill if still alive
    if lsof -ti :"$PORT" &>/dev/null 2>&1; then
        echo "  Force-killing remaining processes..."
        kill -9 $(lsof -ti :"$PORT" 2>/dev/null) 2>/dev/null || true
        sleep 2
    fi
else
    echo "  No process on port $PORT"
fi

# Clean up SQLite WAL/SHM lock files left by a force-kill (portable GNU+BSD).
find "$SCRIPT_DIR/data" \( -name "*.db-wal" -o -name "*.db-shm" \) -delete 2>/dev/null || true

# ── 2/3  Check services ─────────────────────────

echo ""
echo "[2/3] Checking services..."

# Check Ollama
if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "  Ollama: OK"
else
    echo "  Ollama: Not running"
    if command -v ollama &>/dev/null; then
        echo "  Ollama: Starting..."
        nohup ollama serve >/dev/null 2>&1 &
        disown 2>/dev/null || true
        sleep 3
    fi
fi

# Check ComfyUI — no portable launcher (the .bat uses the Windows embedded
# build). Report only; start it manually on macOS if you need image/video gen.
if curl -s http://localhost:8188/system_stats >/dev/null 2>&1; then
    echo "  ComfyUI: OK"
else
    echo "  ComfyUI: Not running (start manually if needed)"
fi

# Check Voice API (port 8602). Fingerprint the response so a stray service
# doesn't fool us. Lazy-loads voices; this just gets the listener ready.
if curl -s http://localhost:8602/health 2>/dev/null | grep -q "edge_voices"; then
    echo "  Voice API: OK"
elif [ -f "$SCRIPT_DIR/services/voice-api/server.py" ]; then
    echo "  Voice API: Starting on 8602..."
    ( cd "$SCRIPT_DIR/services/voice-api" \
      && VOICE_API_PORT=8602 nohup "$PY" server.py >/dev/null 2>&1 & )
    sleep 3
fi

# Check Pronounce API (port 8603) — phoneme-scoring service. Model is lazy-
# loaded on first /score; the plugin re-spawns if missing on daemon boot.
if curl -s http://localhost:8603/health 2>/dev/null | grep -q "wav2vec2-xlsr"; then
    echo "  Pronounce API: OK"
elif [ -f "$SCRIPT_DIR/services/pronounce/server.py" ]; then
    echo "  Pronounce API: Starting on 8603..."
    ( cd "$SCRIPT_DIR/services/pronounce" \
      && PRONOUNCE_API_PORT=8603 nohup "$PY" server.py >/dev/null 2>&1 & )
    sleep 3
fi

# ── 3/3  Start EmptyOS ───────────────────────────

echo ""
echo "[3/3] Starting EmptyOS..."
cd "$SCRIPT_DIR"

# The dogfood :9001 sidecar is owned by plugins/dogfood-demo/ and auto-starts
# when the main daemon's kernel boots — no separate starter here. Enable it by
# copying dogfood/emptyos.toml.example to dogfood/emptyos.toml and setting
# `[plugins.dogfood-demo] enabled = true` in your top-level emptyos.toml.

# Clear any stale wedge-alert flag from a previous boot — the watchdog re-writes
# it only on a real mid-runtime wedge.
rm -f "$SCRIPT_DIR/data/wedge-alert.flag" 2>/dev/null || true

# Daemon watchdog — backgrounded with its own process group so Ctrl+C of the
# main daemon below doesn't take it down. Boot-grace inside the watchdog avoids
# false positives during the ~70-80s boot window. Next restart kills the old
# one via the port-PID sweep + its own restart logic.
if [ -f "$SCRIPT_DIR/scripts/daemon_watchdog.py" ]; then
    nohup "$PY" scripts/daemon_watchdog.py >/dev/null 2>&1 &
    disown 2>/dev/null || true
fi

# Main daemon (foreground; Ctrl+C stops main + plugin-spawned children).
exec "$PY" -m emptyos start
