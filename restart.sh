#!/usr/bin/env bash
# EmptyOS Restart — cross-platform (macOS / Linux / RPi)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=9000

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

if [ -n "$PID" ]; then
    echo "  Killing PID $PID (port $PORT)"
    kill "$PID" 2>/dev/null || true
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

# Clean up SQLite WAL/SHM lock files left by force-kill
find "$SCRIPT_DIR/data" -name "*.db-wal" -o -name "*.db-shm" 2>/dev/null | while read f; do
    rm -f "$f"
done

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
        ollama serve &>/dev/null &
        sleep 3
    fi
fi

# Check ComfyUI (report only)
if curl -s http://localhost:8188/system_stats >/dev/null 2>&1; then
    echo "  ComfyUI: OK"
else
    echo "  ComfyUI: Not running (start manually if needed)"
fi

# ── 3/3  Start EmptyOS ───────────────────────────

echo ""
echo "[3/3] Starting EmptyOS..."
cd "$SCRIPT_DIR"
python3 -m emptyos start
