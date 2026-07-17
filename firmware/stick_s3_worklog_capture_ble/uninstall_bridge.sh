#!/bin/sh
set -eu

LABEL="net.binbian.emptyos.sticks3-worklog"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM=$(id -u)

launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Stopped and removed $LABEL"

