#!/usr/bin/env bash
# EmptyOS Restart — Finder-double-clickable wrapper for macOS.
#
# macOS only runs *.command files on double-click (a plain *.sh opens in an
# editor). This wrapper just forwards to restart.sh from the repo root, so the
# two never drift. After cloning, make it runnable once:
#   chmod +x restart.command stop.command restart.sh stop.sh
exec "$(cd "$(dirname "$0")" && pwd)/restart.sh" "$@"
