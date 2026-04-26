"""Lint: every `self.call_app("X", ...)` must have X in the caller's manifest
`[requires] apps`. Prevents the kind of drift the `capture → journal` fix caught
— an app calling another that isn't declared, which silently breaks export
groups (the exporter uses the declared dep list to build the bundle roster).

Runs in a few ms, no daemon required. Called from CI's collect-only step.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
APPS_ROOTS = [REPO / "apps", REPO / "apps" / "personal"]

# Exclude self, kernel, and a few known placeholder call sites.
_IGNORE_TARGETS = {"self", "kwargs"}

# Matches self.call_app("<app_id>", ...) — app_id must be a bare string literal.
# String-concatenation / variable-based call_app is not checked (rare and
# intentional — e.g. assistant's slash dispatch routes by user input).
_CALL_APP_RE = re.compile(r"""self\.call_app\(\s*['"]([a-z0-9_-]+)['"]""")


def _discover_apps() -> list[Path]:
    out: list[Path] = []
    for root in APPS_ROOTS:
        if not root.exists():
            continue
        for manifest in sorted(root.glob("*/manifest.toml")):
            out.append(manifest.parent)
    return out


def _call_app_targets(app_dir: Path) -> set[str]:
    """All distinct app ids referenced by `self.call_app("id", ...)` in this app."""
    targets: set[str] = set()
    for py in app_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            source = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _CALL_APP_RE.finditer(source):
            target = match.group(1)
            if target in _IGNORE_TARGETS:
                continue
            targets.add(target)
    return targets


def _declared_apps(manifest_path: Path) -> set[str]:
    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return set(data.get("requires", {}).get("apps", []) or [])


def _app_id(manifest_path: Path) -> str:
    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        return data.get("app", {}).get("id", manifest_path.parent.name)
    except Exception:
        return manifest_path.parent.name


APPS = _discover_apps()


def _personal_app_ids() -> set[str]:
    """Ids of apps under apps/personal/ — these are gitignored and may be absent
    on fresh clones. Calls from core apps to personal apps are tolerated
    (graceful degradation via call_app returning an error)."""
    personal_root = REPO / "apps" / "personal"
    if not personal_root.exists():
        return set()
    out: set[str] = set()
    for manifest in personal_root.glob("*/manifest.toml"):
        out.add(_app_id(manifest))
    return out


_PERSONAL_IDS = _personal_app_ids()


def _is_core(app_dir: Path) -> bool:
    return app_dir.parent.name == "apps"


@pytest.mark.parametrize("app_dir", APPS, ids=[p.name for p in APPS])
def test_call_app_targets_are_declared(app_dir: Path):
    """Every app id called via self.call_app(...) must be in [requires] apps,
    with two tolerations:
      - core → personal is fine (personal apps are gitignored; absence degrades
        gracefully via call_app returning an error).
      - personal apps themselves are skipped here — they're user-local, not
        visible in CI, and their consistency is the user's responsibility. Run
        the same test locally to audit personal-app drift.
    """
    manifest = app_dir / "manifest.toml"
    if not manifest.exists():
        pytest.skip(f"no manifest at {manifest}")
    if not _is_core(app_dir):
        pytest.skip(f"'{app_dir.name}' is a personal app; lint core apps only in CI")

    caller_id = _app_id(manifest)
    declared = _declared_apps(manifest)
    called = _call_app_targets(app_dir)

    missing = called - declared - {caller_id} - _PERSONAL_IDS

    assert not missing, (
        f"'{caller_id}' calls app(s) not in [requires] apps: {sorted(missing)}. "
        f"Declared: {sorted(declared) or '<none>'}. "
        f"Add the missing id(s) to {manifest.relative_to(REPO)}."
    )
