#!/usr/bin/env python3
"""One-time Gmail OAuth — read-only. Run this OUTSIDE the daemon.

It opens your browser, asks Google for the gmail.readonly scope ONLY, and saves
the resulting token to data/secrets/gmail-token.json (gitignored). The daemon's
gmail plugin then loads + auto-refreshes that token; it can never send, modify,
label, or delete mail, because that scope is the only one ever granted.

PREREQUISITE — create a Google Cloud OAuth client (~5 min, one time):
  1. https://console.cloud.google.com/ → create (or pick) a project.
  2. "APIs & Services" → "Enable APIs" → enable the **Gmail API**.
  3. "APIs & Services" → "OAuth consent screen" → External → add yourself as a
     Test user (keeps it in testing mode; no Google verification needed).
  4. "Credentials" → "Create credentials" → "OAuth client ID" → **Desktop app**.
  5. Download the JSON, save it as:  data/secrets/gmail-client.json
  6. Run:  python scripts/gmail_auth.py

No kernel import here — safe to run while the daemon is up.
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
    cs = client.client_secret_path(secrets)
    if not cs.exists():
        print(f"Missing {cs}\n\nDo the Google Cloud setup in this script's "
              "docstring first, then re-run.")
        return 2
    print(f"Requesting scope: {client.SCOPES[0]} (read-only)\n"
          "A browser window will open for consent...")
    prof = client.run_auth_flow(secrets)
    print(f"\nConnected read-only as {prof.get('emailAddress','?')} "
          f"({prof.get('messagesTotal','?')} messages).")
    print(f"Token saved to {client.token_path(secrets)}. Restart the daemon to "
          "activate the gmail plugin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
