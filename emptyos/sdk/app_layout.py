"""Discover app directories across the ``apps/`` track tree.

EmptyOS organises apps into three tracks, each holding tier/group sub-folders:

    apps/public/{core,standard,labs}/<id>/
    apps/extension/{engineering,english-learning,portfolio,plekto,dev,others,labs}/<id>/
    apps/personal/<id>/   and   apps/personal/labs/<id>/
    apps/_catalog/<id>/   (parked — uninstalled-but-kept)

App **ids** are unchanged by folder location — release tiers, ``call_app``,
``data/apps/<id>/`` and events all key on the id, not the path. This module is
the single id ↔ on-disk-path source so the kernel app-loader and the release
tooling can't drift on the discovery rule.

The scan rule is depth-agnostic: an app dir is any directory containing
``manifest.toml``; the walker stops descending once it finds one (an app's own
internals are never scanned). Underscore-prefixed dirs are skipped, except the
top-level ``_catalog/`` (parked apps, gated by ``include_catalog``). The
``personal/`` subtree is gitignored and gated by ``include_personal`` so release
tooling never sees it. This handles the flat legacy layout too (a leftover
``apps/<id>/`` with a manifest is found), so a half-migrated tree still loads.

Pure ``pathlib`` only — no kernel/sdk-internal imports — so release scripts can
load it by path in a bare CI checkout (same reason ``release_tiers.py`` stays
pure).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

# Top-level dirs under apps/ that are never apps and never scanned.
_SKIP_TOP = {"_example", "_retired"}
_PERSONAL = "personal"
_CATALOG = "_catalog"


def _manifest_id(app_dir: Path) -> str:
    """Read the app id from manifest.toml; fall back to the dir name if the
    manifest is missing/unparseable (the loader surfaces the parse error
    separately). The dir name and the id usually match, but not always —
    e.g. the ``cables/`` dir ships id ``cable-network`` — so callers that
    need the canonical id (resolve_app_dir, tier matching) rely on this."""
    try:
        with open(app_dir / "manifest.toml", "rb") as f:
            aid = tomllib.load(f).get("app", {}).get("id")
        if isinstance(aid, str) and aid:
            return aid
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return app_dir.name


def _find_apps_under(root: Path) -> Iterator[Path]:
    """Yield every dir under ``root`` that contains a manifest.toml, stopping
    descent at each app root. Skips underscore-prefixed dirs."""
    if not root.is_dir():
        return
    if (root / "manifest.toml").is_file():
        yield root
        return  # an app root — never scan its internals
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        yield from _find_apps_under(child)


def iter_app_dirs(
    apps_root: Path,
    *,
    include_personal: bool = False,
    include_catalog: bool = False,
) -> Iterator[tuple[str, Path]]:
    """Yield ``(app_id, app_dir)`` for every app under ``apps_root``.

    Covers the track tree at any depth (``apps/public/core/<id>/`` etc.) and a
    flat legacy ``apps/<id>/``. ``personal/`` is excluded unless
    ``include_personal`` (release tooling must never see it); ``_catalog/``
    (parked) is excluded unless ``include_catalog``.
    """
    apps_root = Path(apps_root)
    if not apps_root.is_dir():
        return
    for child in sorted(apps_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name in _SKIP_TOP:
            continue
        if name == _CATALOG:
            if include_catalog:
                for d in _find_apps_under(child):
                    yield _manifest_id(d), d
            continue
        if name == _PERSONAL:
            if include_personal:
                for d in _find_apps_under(child):
                    yield _manifest_id(d), d
            continue
        if name.startswith("_"):
            continue
        # A track dir (public, extension) or a leftover flat app.
        for d in _find_apps_under(child):
            yield _manifest_id(d), d


def resolve_app_dir(
    apps_root: Path, app_id: str, *, include_personal: bool = True
) -> Path | None:
    """On-disk dir for ``app_id`` wherever it lives, or ``None``.

    The single replacement for the old ``repo / "apps" / app_id`` (+ personal
    fallback) pattern that breaks once apps move under a track folder. Defaults
    to including personal — most non-release consumers want it. Does not include
    parked (``_catalog``) apps.
    """
    for aid, p in iter_app_dirs(apps_root, include_personal=include_personal):
        if aid == app_id:
            return p
    return None


def app_dir_map(apps_root: Path, *, include_personal: bool = False) -> dict[str, Path]:
    """``{app_id: app_dir}`` across the tree. Last one wins on id collision.
    Personal excluded by default (release tooling); opt in for dev consumers."""
    return {aid: p for aid, p in iter_app_dirs(apps_root, include_personal=include_personal)}


def _rel_parts(app_dir: Path, apps_root: Path) -> tuple[str, ...]:
    try:
        return Path(app_dir).resolve().relative_to(Path(apps_root).resolve()).parts
    except (ValueError, OSError):
        return ()


def track_of(app_dir: Path, apps_root: Path) -> str:
    """Top-level track of an app dir: ``public`` / ``extension`` / ``personal``
    / ``_catalog``, or ``""`` for a flat legacy app (depth-1)."""
    parts = _rel_parts(app_dir, apps_root)
    if len(parts) >= 2:  # <track>/.../<id>
        return parts[0]
    return ""


def group_of(app_dir: Path, apps_root: Path) -> str:
    """Group/tier folder of an app dir (e.g. ``core``, ``engineering``,
    ``labs``), or ``""`` when there's no group level (flat app, or
    ``personal/<id>`` directly under personal)."""
    parts = _rel_parts(app_dir, apps_root)
    if len(parts) >= 3:  # <track>/<group>/<id>
        return parts[1]
    return ""


def is_parked(app_dir: Path, apps_root: Path) -> bool:
    """True if the app lives under ``apps/_catalog/`` (parked, not loaded)."""
    return _CATALOG in _rel_parts(app_dir, apps_root)


def find_repo_root(start: Path) -> Path:
    """Walk up from ``start`` to the EmptyOS repo root — the dir containing both
    ``emptyos/`` and ``apps/``.

    Replaces the fragile ``Path(__file__).parent.parent.parent`` idiom in app
    code, which silently broke when apps moved deeper into the track tree
    (apps/<track>/<group>/<id>/<file> is 4 levels under apps/, not 1). Falls
    back to the start's parent if no marker is found.
    """
    p = Path(start).resolve()
    for cand in (p, *p.parents):
        if (cand / "emptyos").is_dir() and (cand / "apps").is_dir():
            return cand
    return p.parent
