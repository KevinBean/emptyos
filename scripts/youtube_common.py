"""Shared plumbing for the YouTube push drivers (articles + songs).

Extracted at the second consumer (CLAUDE.md rule 9). Both drivers load the
plugin's client without booting the kernel, connect a named token profile, and
refuse to upload unless the connected channel is the intended one — that guard
is the only thing standing between a song and the engineering channel.

Named `youtube_common.py`, not `_youtube_common.py`: .gitignore swallows
`scripts/_*.py`, which would leave every importer broken in a fresh clone.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SECRETS = REPO / "data" / "secrets"


def load_client():
    """Import plugins/youtube/client.py directly (no kernel, no plugin loader).

    Importing the kernel would open a syslog SQLite handle — these drivers run
    as one-shot processes alongside a live daemon, so they stay kernel-free.
    """
    spec = importlib.util.spec_from_file_location(
        "youtube_client", REPO / "plugins" / "youtube" / "client.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def connect(client, profile: str = ""):
    """Credentials for a token profile, or None after printing the re-auth command.

    The message names the profile. Telling a user to run a bare `youtube_auth.py`
    when they wanted the music profile would re-authorize the DEFAULT token, and
    if they then picked the music channel in the browser picker it would silently
    rebind the engineering connection to the wrong channel.
    """
    creds = client.load_credentials(SECRETS, profile)
    if creds is None:
        flag = f" --profile {profile}" if profile else ""
        label = repr(profile) if profile else "default"
        print(
            f"YouTube profile {label} is not connected. Run:\n"
            f"  python scripts/youtube_auth.py{flag}"
        )
    return creds


def guard_channel(client, creds, expect: str, *, force: bool = False, profile: str = "") -> bool:
    """Print the connected channel; True when it is the intended one.

    A single OAuth client serves several channels, so the channel a token is
    bound to is decided at consent time and is invisible until asked. This is
    what keeps a song off the engineering channel and an article off the music
    channel — an upload is irreversible, so it fails closed.
    """
    ch = client.get_channel(creds)
    title = ch.get("title", "")
    print(f"Connected channel: {title}  (id: {ch.get('id', '?')})")
    if force:
        return True
    if expect.lower() in title.lower():
        return True
    flag = f" --profile {profile}" if profile else ""
    print(
        f"\nREFUSING: channel title does not contain '{expect}'.\n"
        "This guards against uploading to the wrong channel.\n"
        f"Re-run  python scripts/youtube_auth.py{flag}  and pick the right channel,\n"
        "or pass --force / --expect-channel to override."
    )
    return False
