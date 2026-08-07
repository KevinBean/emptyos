"""Fail-soft post-restart probes for the three standard local ports."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

TARGETS = (
    ("EmptyOS", "http://127.0.0.1:9000/api/health", True, None),
    ("Dogfood", "http://127.0.0.1:9001/api/health", False, None),
    ("External Lab", "http://127.0.0.1:9100/health", True, "emptyos-external-lab"),
)


def _probe(url: str, identity: str | None) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status != 200:
                return False
            if identity is None:
                return True
            body = json.loads(response.read().decode("utf-8"))
            return body.get("service") == identity
    except (OSError, ValueError, urllib.error.URLError):
        return False


def main() -> None:
    pending = {name: (url, required, identity) for name, url, required, identity in TARGETS}
    online: set[str] = set()
    for _ in range(45):
        for name, (url, _, identity) in list(pending.items()):
            if _probe(url, identity):
                online.add(name)
                pending.pop(name)
        if "EmptyOS" in online and "External Lab" in online:
            break
        time.sleep(2)

    print("\n  Local service health:", flush=True)
    for name, _, required, _ in TARGETS:
        if name in online:
            state = "OK"
        elif required:
            state = "OFFLINE (check daemon/lab logs)"
        else:
            state = "OFFLINE (optional)"
        print(f"    {name:<13} {state}", flush=True)


if __name__ == "__main__":
    main()
