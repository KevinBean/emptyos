"""EmptyOS — A personal AI-powered operating system."""


def _read_version() -> str:
    """The release version, from ``release.toml`` beside the package.

    ``release.toml`` is the single source of truth (``scripts/check_version_sync.py``);
    a literal here sat at 0.1.0 through every release up to v0.7.1. Installed
    metadata is the fallback for a wheel install with no source tree beside it.
    """
    import tomllib
    from pathlib import Path

    # Never let a malformed release.toml break `import emptyos` — every daemon,
    # CLI and SDK import goes through here. ValueError covers TOMLDecodeError
    # and a non-UTF-8 byte; TypeError a `release` that is not a table.
    try:
        with open(Path(__file__).resolve().parent.parent / "release.toml", "rb") as f:
            found = tomllib.load(f)["release"]["version"]
        if isinstance(found, str) and found:
            return found
    except (OSError, KeyError, TypeError, ValueError):
        pass
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("emptyos")
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _read_version()
