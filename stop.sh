#!/usr/bin/env bash
# EmptyOS Stop — cross-platform (macOS / Linux / RPi). Mirror of stop.bat.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=9000

echo "Stopping EmptyOS daemon (port $PORT)..."

# Find the PID listening on the port.
if command -v lsof &>/dev/null; then
    PID=$(lsof -ti :"$PORT" 2>/dev/null || true)
elif command -v ss &>/dev/null; then
    PID=$(ss -tlnp "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K\d+' || true)
else
    PID=$(fuser "$PORT/tcp" 2>/dev/null || true)
fi

if [ -n "${PID:-}" ]; then
    echo "  Killing PID $PID"
    kill $PID 2>/dev/null || true
    # Wait up to 10s for the port to free.
    for i in $(seq 1 10); do
        if ! lsof -ti :"$PORT" &>/dev/null 2>&1; then
            break
        fi
        if [ "$i" -eq 10 ]; then
            echo "  WARNING: port $PORT still in use after 10s"
        fi
        sleep 1
    done
    # Force-kill anything still holding the port.
    if lsof -ti :"$PORT" &>/dev/null 2>&1; then
        kill -9 $(lsof -ti :"$PORT" 2>/dev/null) 2>/dev/null || true
    fi
else
    echo "  No process on port $PORT"
fi

# Stop the watchdog too — an intentional stop must not trip a false wedge alert.
pkill -f "scripts/daemon_watchdog.py" 2>/dev/null || true

# Clean SQLite WAL/SHM lock files left by a force-kill.
find "$SCRIPT_DIR/data" \( -name "*.db-wal" -o -name "*.db-shm" \) -delete 2>/dev/null || true

echo "Stopped."
