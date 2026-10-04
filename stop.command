#!/usr/bin/env bash
# EmptyOS Stop — Finder-double-clickable wrapper for macOS. Forwards to stop.sh.
exec "$(cd "$(dirname "$0")" && pwd)/stop.sh" "$@"
