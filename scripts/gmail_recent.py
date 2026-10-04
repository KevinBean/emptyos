#!/usr/bin/env python3
"""Smoke test: print recent Gmail message metadata (read-only). Run OUTSIDE the daemon.

Proves the read-only connection works before any classifier logic is wired in.
Prints ONLY metadata (sender / subject / date / Gmail snippet) — never message
bodies. No kernel import.

Usage:
  python scripts/gmail_recent.py                 # 10 most recent
  python scripts/gmail_recent.py 20 "newer_than:30d"
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_client():
    spec = importlib.util.spec_from_file_location(
        "gmail_client", REPO / "plugins" / "gmail" / "client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    client = _load_client()
    secrets = REPO / "data" / "secrets"
    creds = client.load_credentials(secrets)
    if creds is None:
        print("No Gmail token. Run: python scripts/gmail_auth.py")
        return 2
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    query = sys.argv[2] if len(sys.argv) > 2 else ""
    prof = client.get_profile(creds)
    print(f"Account: {prof.get('emailAddress','?')}  |  query={query!r}  |  showing {n}\n")
    for m in client.recent_meta(creds, query, n):
        print(f"• {m['date'][:25]:25}  {m['from'][:35]:35}  {m['subject'][:50]}")
        if m["snippet"]:
            print(f"    {m['snippet'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
