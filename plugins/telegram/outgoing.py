"""Outbound-file guard for the Telegram plugin — pure, kernel-free.

Sending a local file to Telegram is an **exfiltration primitive**: one call with
a caller-chosen path ships that file off the machine. The plugin is reachable by
any app that requires the `telegram` service, so the path a caller hands in is
never trusted here. (Spelled out rather than shown as a call, because
`tests/test_unit_proactive_raw_senders.py` greps for that literal to find
senders that skip the proactive gate — this one does not skip it.)

`open_outgoing()` is the single choke point every file-sending method goes
through (`send_photo`, `send_video`, `send_document`). It refuses rather than
raises, because the plugin's whole surface returns `{"error": ...}` dicts.

**It returns an open file handle, not a path.** Every check runs against the
*opened* file and the caller uploads from that same handle, so a path swapped
between the check and the read (a symlink dropped in by a concurrent task)
cannot smuggle a different file through. Returning a path re-opened later is the
TOCTOU version of this function and is why it does not exist.

Checks, in order — the first failure wins and the reason names it:

1. the path resolves (symlinks/junctions followed) to an existing regular file;
2. it sits under one of the caller's allowed roots — by default the *output*
   locations, not the whole vault, so a note, a repo file or `../../` escape is
   refused;
3. it is not on the credential/state denylist (`data/secrets/`, any dot-directory
   such as `.git/` or `.obsidian/`, `emptyos.toml`, dotfiles, `*.key`/`*.pem`/
   `*.db`), compared case-insensitively — Windows and macOS treat `Secrets` and
   `secrets` as one directory;
4. its extension is allowed for the kind being sent;
5. it is within the size cap for that kind (Telegram: 10 MB photos, 50 MB
   everything else);
6. if its *content* looks like text, the WHOLE file passes `scan_outbound` — a
   secret or `.eos-personal` hit refuses the send.

Check 6 sniffs content rather than trusting the extension, because renaming a
note to `notes.md.mp4` would otherwise skip the scan entirely.

**The known gap, stated rather than papered over:** a compressed container can
carry text this scanner cannot see. `.zip` is therefore not sendable at all;
`.pdf` is, and its text is not scanned. That is the reason the allowlist is
narrow and the default roots are output directories — a filter over arbitrary
vault content would be security theatre.

Captions go through `scan_caption()` for the same reason: the caption is text
the caller supplies and it leaves the machine alongside the file.

Not `emptyos.sdk.attachments.confine()`: that answers a different question —
one root, a *relative* path, and it refuses absolute paths outright. Here the
caller hands in an absolute path and several roots are legitimate, so
containment is only the first of six checks. If a second consumer ever needs
this exact shape, extract it then (rule 9).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO, Callable

MB = 1_000_000  # decimal, so the refusal message and the config key agree

#: Telegram bot-API upload caps per method (core.telegram.org/bots/api —
#: "Sending files": 10 MB for photos, 50 MB for other files by multipart).
#: A caller's own cap is clamped to these; exceeding them earns a 413 from
#: Telegram rather than a reason from us.
HARD_CAP: dict[str, int] = {
    "photo": 10 * MB,
    "video": 50 * MB,
    "document": 50 * MB,
}
DEFAULT_CAP = 45 * MB

ALLOWED_EXT: dict[str, set[str]] = {
    "photo": {".jpg", ".jpeg", ".png", ".webp"},
    "video": {".mp4", ".mov", ".webm", ".mkv"},
    # No .zip: an archive is an arbitrary-content envelope the content scan
    # cannot read, which would void the scan for anything worth hiding.
    "document": {
        ".pdf", ".md", ".txt", ".csv", ".json", ".log",
        ".png", ".jpg", ".jpeg", ".webp", ".svg",
        ".mp3", ".wav", ".m4a", ".flac", ".mp4", ".mov",
    },
}

SNIFF_BYTES = 8192  # enough to classify text vs binary
SCAN_CHUNK = 1 << 20  # scan in 1 MiB reads rather than loading the whole file
SCAN_OVERLAP = 4096  # carried between chunks so a secret spanning a boundary is seen

# Path components that are never sendable, wherever they appear under a root.
# Dot-directories are denied as a class (.git, .obsidian, .ssh, .claude …).
DENY_PARTS = {"secrets", "node_modules", "__pycache__"}
DENY_NAMES = {"emptyos.toml", "emptyos.example.toml", "id_rsa", "id_ed25519"}
DENY_SUFFIXES = {
    ".env", ".key", ".pem", ".p12", ".pfx", ".crt", ".cer", ".keystore",
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".pyc",
}


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def effective_cap(kind: str, requested_bytes: int | None = None) -> int:
    """The smaller of what the operator asked for and what Telegram accepts."""
    hard = HARD_CAP.get(kind, DEFAULT_CAP)
    if requested_bytes is None or requested_bytes <= 0:
        return min(DEFAULT_CAP, hard)
    return min(int(requested_bytes), hard)


def _looks_like_text(head: bytes) -> bool:
    """Content sniff. A NUL byte means binary; otherwise it must decode UTF-8.

    Deliberately not extension-based: `notes.md.mp4` is a text file and must be
    scanned, and a real .mp4 never decodes cleanly this far in.
    """
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        # A multi-byte character cut by the sniff boundary is still text.
        try:
            head[:-4].decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False


def _scan_stream(fh: BinaryIO, scan: Callable) -> list:
    """Scan the whole open file in chunks, carrying an overlap across reads."""
    findings: list = []
    tail = ""
    while True:
        chunk = fh.read(SCAN_CHUNK)
        if not chunk:
            break
        text = tail + chunk.decode("utf-8", "replace")
        findings.extend(scan(text))
        tail = text[-SCAN_OVERLAP:]
    return findings


def open_outgoing(
    raw_path: str,
    *,
    roots: list[Path],
    kind: str,
    scan: Callable,
    max_bytes: int | None = None,
) -> tuple[BinaryIO | None, Path | None, str]:
    """Return (open handle, resolved path, "") when `raw_path` may be sent,
    else (None, None, reason).

    `scan` is required — a guard whose content check defaults to "skip" is not a
    guard. Pass `emptyos.capabilities.outbound_scan.scan_outbound`.
    """
    if not raw_path or not str(raw_path).strip():
        return None, None, "no path given"
    if kind not in ALLOWED_EXT:
        return None, None, f"unknown send kind: {kind}"
    if not roots:
        return None, None, "no allowed roots configured"
    if scan is None:
        return None, None, "no outbound scanner supplied"

    try:
        path = Path(raw_path).expanduser().resolve()
    except (OSError, RuntimeError) as e:
        return None, None, f"path does not resolve: {e}"
    if not path.is_file():
        return None, None, "not a file"

    resolved_roots = []
    for r in roots:
        try:
            resolved_roots.append(Path(r).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
    if not any(_under(path, r) for r in resolved_roots):
        return None, None, "outside the allowed roots"

    lower_parts = [p.lower() for p in path.parts]
    if set(lower_parts) & DENY_PARTS:
        return None, None, "path contains a denied directory"
    # Every dot-directory, not just .git — .obsidian, .ssh, .claude, .venv.
    if any(p.startswith(".") and len(p) > 1 for p in lower_parts[:-1]):
        return None, None, "path contains a dot-directory"
    name = path.name.lower()
    if name in DENY_NAMES:
        return None, None, f"denied file name: {path.name}"
    if name.startswith("."):
        return None, None, f"denied dotfile: {path.name}"
    suffix = path.suffix.lower()
    if suffix in DENY_SUFFIXES:
        return None, None, f"denied file type: {suffix}"
    if suffix not in ALLOWED_EXT[kind]:
        return None, None, f"{suffix or 'no extension'} is not allowed for {kind}"

    cap = effective_cap(kind, max_bytes)
    stat_before = path.stat()
    if stat_before.st_size == 0:
        return None, None, "file is empty"
    if stat_before.st_size > cap:
        return None, None, (
            f"file is {stat_before.st_size / MB:.1f} MB, over the {cap // MB} MB cap for {kind}")

    try:
        fh = open(path, "rb")
    except OSError as e:
        return None, None, f"cannot open file: {e}"

    def refuse(reason: str) -> tuple[None, None, str]:
        """Every refusal past the open() must close the handle it was given."""
        try:
            fh.close()
        except OSError:
            pass
        return None, None, reason

    try:
        # Everything above judged the path; from here on judge the OPEN file, so
        # a swap between the checks and the upload cannot change what is sent.
        after = os.fstat(fh.fileno())
        if (after.st_dev, after.st_ino, after.st_size) != (
                stat_before.st_dev, stat_before.st_ino, stat_before.st_size):
            return refuse("file changed while it was being checked")

        if _looks_like_text(fh.read(SNIFF_BYTES)):
            fh.seek(0)
            findings = _scan_stream(fh, scan)
            if findings:
                names = ", ".join(sorted({f[0] for f in findings}))
                return refuse(f"refused: outbound scan flagged {names}")
        fh.seek(0)
    except OSError as e:
        return refuse(f"cannot read file: {e}")
    except BaseException:
        fh.close()
        raise

    return fh, path, ""


def scan_caption(caption: str, scan: Callable) -> str:
    """Return "" when the caption may be sent, else the refusal reason."""
    if not caption:
        return ""
    if scan is None:
        return "refused: no outbound scanner supplied for the caption"
    findings = scan(caption)
    if not findings:
        return ""
    names = ", ".join(sorted({f[0] for f in findings}))
    return f"refused: caption flagged by outbound scan ({names})"
