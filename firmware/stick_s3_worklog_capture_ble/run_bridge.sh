#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
BRIDGE="$HERE/.build/stick-s3-worklog-bridge"

if [ ! -x "$BRIDGE" ]; then
  "$HERE/build_bridge.sh"
fi

cd "$ROOT"
exec "$BRIDGE" --config "$ROOT/emptyos.toml"

