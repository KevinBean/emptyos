"""Self-update — download a release, unpack it beside the running one, switch on restart.

The whole design exists to dodge one Windows fact: **you cannot overwrite a
running exe or its DLLs.** So nothing is ever overwritten. Each version lives in
its own directory, and a tiny stub at the install root picks the newest one that
is marked good::

    <install root>/
        EmptyOS.exe          stub — spawns the newest app-*/ and exits (so its own
        │                    file is never locked, and can itself be replaced)
        app-0.5.6/
        │   EmptyOS.exe      the real app: launcher + daemon + the whole tier
        │   .ok              written only after the download verified and unpacked
        app-0.5.7/           staged by the updater while 0.5.6 is still running
        updates/             partial downloads; nothing here is ever executed

Switching versions is therefore just *which directory the stub picks next time* —
there is no swap, no in-place patch, and no window during which the install is
half-updated. A download that dies mid-way leaves a `.partial` file and no `.ok`,
so it is invisible to the stub and gets cleaned up on the next run.

This module is pure: no kernel, no daemon, no product config. It takes paths and
URLs and returns facts. Both consumers — the launcher's background check and the
daemon's Settings endpoint — drive it from outside.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

#: Marks an app directory as complete and verified. Its absence is what makes a
#: half-extracted or corrupt version invisible rather than dangerous.
OK_MARKER = ".ok"

#: Versions kept on disk: the running one, plus one to fall back to.
KEEP_VERSIONS = 2

APP_DIR_RE = re.compile(r"^app-(.+)$")

DOWNLOAD_CHUNK = 1 << 16


class UpdateError(Exception):
    pass


# ── versions ─────────────────────────────────────────────────────────────────
def parse_version(text: str) -> tuple[int, ...]:
    """"0.5.10" -> (0, 5, 10). Non-numeric parts sort as 0 rather than raising —
    an unparseable version must not be able to crash the stub, which is the one
    thing that always has to start."""
    parts = []
    for chunk in str(text).split("."):
        m = re.match(r"\d+", chunk.strip())
        parts.append(int(m.group()) if m else 0)
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    """Strictly newer. Compared numerically, so 0.5.10 > 0.5.9 (a string compare
    would have said otherwise and stranded every user on .9)."""
    return parse_version(candidate) > parse_version(current)


# ── the feed ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int = 0
    notes_url: str = ""
    stub_version: int = 1


def platform_key() -> str:
    if sys.platform == "win32":
        return "windows-x64"
    if sys.platform == "darwin":
        return "macos-universal"
    return "linux-x64"


def parse_feed(data: dict, *, platform: str | None = None) -> Release:
    """Read a latest.json. Raises UpdateError on anything it can't trust —
    a malformed feed must fail loudly, never half-install."""
    plat = platform or platform_key()
    version = str(data.get("version") or "").strip()
    if not version:
        raise UpdateError("feed has no version")

    platforms = data.get("platforms") or {}
    entry = platforms.get(plat)
    if not entry:
        raise UpdateError(f"feed has no build for {plat}")

    url = str(entry.get("url") or "").strip()
    sha256 = str(entry.get("sha256") or "").strip().lower()
    if not url:
        raise UpdateError(f"feed entry for {plat} has no url")
    if len(sha256) != 64:
        # Without a checksum we cannot tell a truncated download from a good one,
        # and we would be executing whatever arrived. Refuse.
        raise UpdateError(f"feed entry for {plat} has no usable sha256")

    return Release(
        version=version,
        url=url,
        sha256=sha256,
        size=int(entry.get("size") or 0),
        notes_url=str(data.get("notes_url") or ""),
        stub_version=int(data.get("stub_version") or 1),
    )


def fetch_feed(feed_url: str, *, timeout: float = 15.0) -> dict:
    with urllib.request.urlopen(feed_url, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def check(feed_url: str, current_version: str, *, timeout: float = 15.0,
          platform: str | None = None) -> Release | None:
    """The newer release, or None. Never raises on a network failure: being
    offline is the normal state of a laptop, not an error worth a dialog."""
    if not feed_url:
        return None
    try:
        release = parse_feed(fetch_feed(feed_url, timeout=timeout), platform=platform)
    except Exception:
        return None
    return release if is_newer(release.version, current_version) else None


# ── the install tree ─────────────────────────────────────────────────────────
def app_dir_name(version: str) -> str:
    return f"app-{version}"


def installed_versions(install_root: Path) -> list[tuple[str, Path]]:
    """Every *complete* version on disk, newest first.

    A directory without its .ok marker is a failed or partial install and is
    treated as if it does not exist — that is the entire safety property here.
    """
    found = []
    for child in install_root.iterdir() if install_root.is_dir() else []:
        if not child.is_dir():
            continue
        m = APP_DIR_RE.match(child.name)
        if m and (child / OK_MARKER).exists():
            found.append((m.group(1), child))
    return sorted(found, key=lambda p: parse_version(p[0]), reverse=True)


def newest_version(install_root: Path) -> tuple[str, Path] | None:
    versions = installed_versions(install_root)
    return versions[0] if versions else None


def find_app_exe(app_dir: Path) -> Path | None:
    """The one exe in a PyInstaller one-dir build. Found by shape, not by name,
    so the stub needs no product config."""
    exes = [p for p in app_dir.glob("*.exe")] or [
        p for p in app_dir.iterdir() if p.is_file() and p.suffix == ""
    ]
    return exes[0] if exes else None


# ── download + install ───────────────────────────────────────────────────────
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def download(release: Release, install_root: Path, *, timeout: float = 60.0,
             progress=None) -> Path:
    """Fetch the release zip into `updates/` and verify it. Returns the zip path.

    Downloads to `.partial` and only renames on a matching checksum, so a
    half-finished or corrupted file can never be mistaken for a complete one —
    including across a crash, since the name itself carries that meaning.
    """
    staging = install_root / "updates"
    staging.mkdir(parents=True, exist_ok=True)
    final = staging / f"{app_dir_name(release.version)}.zip"
    partial = final.with_suffix(".partial")

    if final.exists() and _sha256(final) == release.sha256:
        return final  # already fetched — a resumed session shouldn't re-download

    partial.unlink(missing_ok=True)
    downloaded = 0
    with urllib.request.urlopen(release.url, timeout=timeout) as r:  # noqa: S310
        with open(partial, "wb") as f:
            while chunk := r.read(DOWNLOAD_CHUNK):
                f.write(chunk)
                downloaded += len(chunk)
                if progress and release.size:
                    progress(downloaded / release.size)

    got = _sha256(partial)
    if got != release.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError(
            f"checksum mismatch for {release.version} "
            f"(expected {release.sha256[:12]}…, got {got[:12]}…)"
        )
    partial.replace(final)
    return final


def install(zip_path: Path, install_root: Path, version: str) -> Path:
    """Unpack a verified zip into `app-<version>/` and mark it good.

    Extracts to a temp directory first and renames into place, so the app
    directory either does not exist or is complete — never something in between
    that the stub could pick up. The `.ok` marker is written last, and is the
    only thing that makes the version visible.
    """
    target = install_root / app_dir_name(version)
    if (target / OK_MARKER).exists():
        return target  # already installed

    if target.exists():
        shutil.rmtree(target, ignore_errors=True)   # a previous failed attempt

    prefix = app_dir_name(version) + "/"
    with tempfile.TemporaryDirectory(dir=str(install_root)) as tmp:
        tmpdir = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            members = [n for n in zf.namelist() if n.startswith(prefix)]
            if not members:
                raise UpdateError(f"{zip_path.name} contains no {prefix} directory")
            for name in members:
                # Refuse anything that would escape the directory. The zip is
                # checksummed, but the checksum only proves it is the file we were
                # told to expect — not that we were told the truth.
                rel = name[len(prefix):]
                if not rel or rel.endswith("/"):
                    continue
                dest = (tmpdir / rel).resolve()
                if not str(dest).startswith(str(tmpdir.resolve())):
                    raise UpdateError(f"unsafe path in archive: {name}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)

        if find_app_exe(tmpdir) is None:
            raise UpdateError(f"{zip_path.name} has no executable in {prefix}")

        (tmpdir / OK_MARKER).write_text(version, encoding="utf-8")
        Path(tmpdir).replace(target)   # atomic-ish: the dir appears complete

    return target


def prune(install_root: Path, *, keep: int = KEEP_VERSIONS,
          protect: str | None = None) -> list[str]:
    """Delete old versions, keeping the newest `keep` (and the running one).

    Never removes the version passed as `protect` — deleting the directory you
    are executing from is not survivable, and on Windows would fail halfway and
    leave a corpse.
    """
    versions = installed_versions(install_root)
    removed = []
    for version, path in versions[keep:]:
        if protect and version == protect:
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            removed.append(version)
    # Stale staging files for versions we no longer have any use for.
    staging = install_root / "updates"
    if staging.is_dir():
        live = {v for v, _ in installed_versions(install_root)}
        for f in staging.glob("*.partial"):
            f.unlink(missing_ok=True)
        for f in staging.glob("app-*.zip"):
            m = APP_DIR_RE.match(f.stem)
            if m and m.group(1) not in live:
                f.unlink(missing_ok=True)
    return removed


def update_to(release: Release, install_root: Path, *, progress=None) -> Path:
    """download -> verify -> install. Returns the new app directory."""
    zip_path = download(release, install_root, progress=progress)
    return install(zip_path, install_root, release.version)
