"""Secret-file skip-set — machine credentials kept out of file-serving surfaces.

Shared by any app that serves repo/session files back to a browser and must not
surface machine secrets in the process. First consumer was `apps/extension/plekto/code`
(the code workspace tree + read endpoint); extracted here at the second consumer
(`apps/extension/dev/cockpit`'s artifact serving) per CLAUDE.md Dev Rule 9.

NOT a security boundary — the EmptyOS daemon is single-user (docs/AUTH.md) and the
same token can read these via /repo or /settings. This is exposure-reduction: it
keeps a *file-preview* affordance from casually surfacing `.env` / private keys /
`emptyos.toml`. A real boundary needs a scoped identity (docs/DEFERRED-WORK.md).
"""

from __future__ import annotations

from pathlib import Path

# Machine-local credentials + config with secrets. Example/template configs
# (`*.example`) stay visible for orientation and are not listed here.
SECRET_FILENAMES = frozenset({
    ".env", ".env.local", ".env.production", "credentials.json",
    "emptyos.toml", "secrets.toml",
})
SECRET_SUFFIXES = frozenset({".key", ".p12", ".pem", ".pfx"})


def is_secret_file(p) -> bool:
    """True for a machine-secret path that file-serving surfaces should refuse."""
    p = Path(p)
    return p.name.lower() in SECRET_FILENAMES or p.suffix.lower() in SECRET_SUFFIXES
