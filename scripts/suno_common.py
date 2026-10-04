"""Shared helpers for the Suno archive scripts (scripts/suno_*.py).

Extracted so vault-path resolution, filename sanitising, the CDN download
loop, and WAV-readiness polling live in one place instead of being copied
across suno_download_album / suno_fetch_wav / suno_batch / etc. Pure stdlib;
reads the vault path from emptyos.toml. Imported as a sibling (scripts/ is on
sys.path when a script is run as `python scripts/suno_<x>.py`).
"""
from __future__ import annotations

import re
import tomllib
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0"}
ILLEGAL = re.compile(r'[\\/:*?"<>|]+')
_REPO_ROOT = Path(__file__).resolve().parent.parent


def vault_root() -> Path:
    """Vault path from emptyos.toml [notes].path."""
    return Path(tomllib.load(open(_REPO_ROOT / "emptyos.toml", "rb"))["notes"]["path"])


def safe(name: str) -> str:
    """Sanitise a title into a filename-safe stem (Windows-illegal chars -> _)."""
    return ILLEGAL.sub("_", (name or "untitled")).strip().rstrip(".")


def download(url: str, dest: Path) -> dict:
    """Stream a URL to dest; verify non-zero + Content-Length match. Never raises."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
            expected = int(r.headers.get("Content-Length") or 0)
            tmp = dest.with_suffix(dest.suffix + ".part")
            written = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = r.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    written += len(chunk)
            tmp.replace(dest)
        ok = written > 0 and (expected == 0 or written == expected)
        return {"ok": ok, "bytes": written, "expected": expected,
                "note": "" if ok else ("zero bytes" if written == 0 else f"size mismatch {written}!={expected}")}
    except Exception as e:
        return {"ok": False, "bytes": 0, "expected": 0, "note": f"{type(e).__name__}: {str(e)[:80]}"}


def wav_ready(url: str) -> int:
    """Total size if the WAV is ready (200/206), 0 if not-yet (403), -1 on other error."""
    try:
        req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status in (200, 206):
                cr = r.headers.get("Content-Range")
                if cr and "/" in cr:
                    return int(cr.split("/")[-1])
                return int(r.headers.get("Content-Length") or 1)
            return 0
    except urllib.error.HTTPError as e:
        return 0 if e.code == 403 else -1
    except Exception:
        return -1
