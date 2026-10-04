#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
LABEL="net.binbian.emptyos.sticks3-worklog"
APP_DIR="$HOME/Library/Application Support/EmptyOS"
BIN_DIR="$APP_DIR/bin"
LOG_DIR="$APP_DIR/logs"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/$LABEL.plist"
UID_NUM=$(id -u)

"$HERE/build_bridge.sh"
mkdir -p "$BIN_DIR" "$LOG_DIR" "$PLIST_DIR"
cp "$HERE/.build/stick-s3-worklog-bridge" "$BIN_DIR/stick-s3-worklog-bridge"
sed \
  -e "s|__BRIDGE__|$BIN_DIR/stick-s3-worklog-bridge|g" \
  -e "s|__CONFIG__|$ROOT/emptyos.toml|g" \
  -e "s|__LOG__|$LOG_DIR/stick-s3-worklog-bridge.log|g" \
  "$HERE/$LABEL.plist.in" > "$PLIST"

launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"
echo "Installed and started $LABEL"

