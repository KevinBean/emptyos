#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$HERE/.build"
/usr/bin/clang -fobjc-arc -framework Foundation -framework CoreBluetooth \
  -o "$HERE/.build/stick-s3-worklog-bridge" \
  "$HERE/bridge_mac.m"
echo "Built $HERE/.build/stick-s3-worklog-bridge"
