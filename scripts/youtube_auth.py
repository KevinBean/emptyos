#!/usr/bin/env python3
"""One-time YouTube OAuth — upload connector. Run this OUTSIDE the daemon.

It opens your browser and asks Google for youtube.upload + youtube.readonly ONLY,
then saves the token to data/secrets/youtube-token.json (gitignored). The daemon's
youtube plugin then loads + auto-refreshes it. The only write it can ever perform
is a video upload — no delete, no modify-others, no account management.

MULTI-CHANNEL NOTE: if your Google account owns several channels (brand accounts),
the consent screen shows a channel picker. Pick the channel you want uploads to
land on. This script then PRINTS the connected channel title so you can confirm
it's the right one before uploading anything.

PREREQUISITE — a Google Cloud OAuth client with the YouTube Data API v3 enabled,
saved as data/secrets/youtube-client.json. (You can reuse the same OAuth client
across channels — only the token differs.) Then run:  python scripts/youtube_auth.py

No kernel import here — safe to run while the daemon is up.
"""

from __future__ import annotations

import importlib.util
import sys
import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_client():
    spec = importlib.util.spec_from_file_location(
        "youtube_client", REPO / "plugins" / "youtube" / "client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorize a named YouTube upload profile")
    parser.add_argument("--profile", default="", help="Named token profile (e.g. music); default keeps youtube-token.json")
    args = parser.parse_args()
    client = _load_client()
    secrets = REPO / "data" / "secrets"
    cs = client.client_secret_path(secrets)
    if not cs.exists():
        print(f"Missing {cs}\n\nSave a Google Cloud OAuth client (Desktop app, "
              "YouTube Data API v3 enabled) there first, then re-run.")
        return 2
    print(f"Requesting scopes: {', '.join(client.SCOPES)}\n"
          "A browser window will open for consent. If your account has multiple "
          "channels, PICK THE TARGET CHANNEL in the picker...")
    try:
        ch = client.run_auth_flow(secrets, args.profile)
    except ValueError as e:
        print(str(e))
        return 2
    title = ch.get("title", "?")
    print(f"\nConnected channel: {title}  (id: {ch.get('id', '?')})")
    print(f"Token saved to {client.token_path(secrets, args.profile)}.")
    print("\n>>> Confirm the channel title above is the one you intended. <<<")
    print("Next: push videos with  python scripts/youtube_push_articles.py")
    print("Restart the daemon to activate the youtube plugin for in-system use.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
