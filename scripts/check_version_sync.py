"""Flag a `pyproject.toml` version that has drifted from `release.toml`.

`release.toml` is the single source of truth — `package-release.py` stamps it
into every `MANIFEST.json`, `release-public.py` tags with it, and the product
About panel shows it to the user. `pyproject.toml` has to be a literal (setuptools
reads it at build time, before any of our code runs), so it cannot derive the
value and must be bumped alongside.

`tests/test_unit_package_release.py::TestVersionIsOneNumber` has asserted this
since the three-way fragmentation was collapsed — but nothing ran it on the
release path, so the versions drifted apart across **seven** consecutive releases
(v0.5.7 → v0.6.4) with the test red the whole time and the release still shipping.
A bump is a hand edit of two files; `scripts/release.py` only ever *reads* the
version, so there is no automation to blame and no automation to fix. The gap was
that the assertion never ran where the bump happens.

So this is the same assertion, moved to where it fires: preflight's `always` and
`release` scopes, gating. The pytest test now delegates here, so there is one
implementation rather than two that can disagree.

Exit code = number of drifted files (0 = in sync). Silent on a healthy tree.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def release_version(repo: Path | None = None) -> str:
    """The authoritative version, from `release.toml`."""
    root = repo or REPO
    with open(root / "release.toml", "rb") as f:
        return tomllib.load(f)["release"]["version"]


def pyproject_version(repo: Path | None = None) -> str:
    root = repo or REPO
    with open(root / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def findings(repo: Path | None = None) -> list[dict]:
    """Return one finding per file whose version disagrees with `release.toml`."""
    root = repo or REPO
    try:
        authoritative = release_version(root)
    except (OSError, KeyError, tomllib.TOMLDecodeError) as e:
        return [{"file": "release.toml", "issue": "unreadable", "detail": str(e)}]

    out: list[dict] = []
    try:
        found = pyproject_version(root)
    except (OSError, KeyError, tomllib.TOMLDecodeError) as e:
        out.append({"file": "pyproject.toml", "issue": "unreadable", "detail": str(e)})
    else:
        if found != authoritative:
            out.append(
                {
                    "file": "pyproject.toml",
                    "issue": "drift",
                    "found": found,
                    "expected": authoritative,
                    "detail": f"pyproject.toml is {found}, release.toml is {authoritative}",
                }
            )
    return out


def main() -> int:
    as_json = "--json" in sys.argv
    hits = findings()
    ok = not hits

    if as_json:
        emit_json(
            ok,
            "ok" if ok else "version_drift",
            f"version {release_version()} in sync"
            if ok
            else f"{len(hits)} file(s) drifted from release.toml",
            {"findings": hits, "release_version": release_version() if ok else None},
        )
        return len(hits)

    if ok:
        print(f"version sync: OK (v{release_version()})")
        return 0

    print(f"version sync: {len(hits)} file(s) drifted from release.toml\n")
    for f in hits:
        print(f"  [{f['issue']}] {f['file']}")
        print(f"      {f['detail']}")
    print("\n  release.toml is the source of truth — bump both, or fix pyproject.toml.")
    return len(hits)


if __name__ == "__main__":
    raise SystemExit(main())
