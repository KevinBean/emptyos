"""The stub — the exe the user's shortcut points at. Its whole job is to pick a version.

It finds the newest `app-*/` directory that carries an `.ok` marker, spawns the
app inside it, and **exits immediately**. Exiting is the point: because the stub
is not running while the app is, its own file is never locked, so an update can
replace the stub too.

It must be the most boring program in the repo. It imports nothing from EmptyOS,
touches no config, and does no I/O beyond a directory listing — because it is the
one thing that has to start even when everything else is broken. If the stub
fails, the user has an icon that does nothing.

    EmptyOS.exe                  # this, at the install root
    EmptyOS.exe --version 0.5.6  # force an older version (the rollback escape hatch)
    EmptyOS.exe --list           # what's installed
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared.updater import find_app_exe, installed_versions  # noqa: E402


def install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2] / "dist" / "product"


def fail(message: str) -> int:
    print(f"EmptyOS: {message}", file=sys.stderr)
    if sys.platform == "win32":
        # A double-clicked exe has no console to read the error in. Say it in the
        # only place the user will actually see it.
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "EmptyOS", 0x10)
        except Exception:
            pass
    return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = install_root()
    versions = installed_versions(root)

    if not versions:
        return fail(
            f"No installed version found in {root}.\n\n"
            "The app-<version> folder is missing or incomplete. "
            "Re-extract the download and try again."
        )

    if "--list" in args:
        for version, path in versions:
            print(f"{version}\t{path}")
        return 0

    wanted = ""
    if "--version" in args:
        i = args.index("--version")
        if i + 1 < len(args):
            wanted = args[i + 1]
            args = args[:i] + args[i + 2:]

    if wanted:
        match = [(v, p) for v, p in versions if v == wanted]
        if not match:
            have = ", ".join(v for v, _ in versions)
            return fail(f"Version {wanted} is not installed. Installed: {have}")
        version, app_dir = match[0]
    else:
        version, app_dir = versions[0]   # newest complete version

    exe = find_app_exe(app_dir)
    if exe is None:
        return fail(f"{app_dir} contains no executable.")

    # Detached: the app owns its own lifetime from here, and this process is gone
    # a moment later so nothing about it stays locked.
    kwargs: dict = {"cwd": str(app_dir), "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen([str(exe), *args], **kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
