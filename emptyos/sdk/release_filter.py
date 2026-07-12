"""Filesystem pruning shared by EmptyOS release snapshot collectors.

``release_tiers.py`` owns pure tier resolution. This sibling module owns the
I/O step that applies a resolved tier to an already-collected snapshot.
``release-public.py`` and ``release.py`` use it because both prune an existing
tree; ``package-release.py`` remains separate because it builds ``dist/`` by
copying an allowlisted set of paths rather than pruning a snapshot.

:func:`rmtree_forgiving` also lives here as the one Windows-safe removal both
release scripts call, rather than each keeping its own copy.

Keep this module free of module-level ``emptyos`` imports: it is loaded by path
from release scripts that must run in a bare checkout, where importing the
``emptyos.sdk`` package would pull in the daemon runtime.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


def _load_iter_app_dirs():
    """Resolve ``iter_app_dirs`` without forcing ``emptyos.sdk.__init__``.

    The package initialiser imports the daemon runtime (starlette, pydantic), so
    a plain ``from emptyos.sdk.app_layout import ...`` makes this module — and
    every release script that loads it — unusable in a bare checkout. Prefer the
    normal import, and fall back to loading the stdlib-only sibling by path.
    """
    try:
        from emptyos.sdk.app_layout import iter_app_dirs
    except ModuleNotFoundError:
        import importlib.util
        import sys

        name = "eos_release_app_layout"
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("app_layout.py")
        )
        module = importlib.util.module_from_spec(spec)
        # Register before exec: dataclasses/enums resolve cls.__module__ here.
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module.iter_app_dirs
    return iter_app_dirs


class PruneError(RuntimeError):
    """A snapshot path that had to be removed is still on disk."""


@dataclass(frozen=True)
class PruneReport:
    """IDs and track markers removed from a release snapshot."""

    dropped_apps: tuple[str, ...] = ()
    dropped_plugins: tuple[str, ...] = ()
    dropped_engines: tuple[str, ...] = ()


def _force_writable_then_remove(func, path, _exc_info) -> None:
    """Clear Windows read-only bits when a snapshot contains git artifacts."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        pass


def rmtree_forgiving(path: Path, *, ignore_errors: bool = False) -> None:
    """``shutil.rmtree`` that copes with Windows read-only files (git packs).

    A no-op when ``path`` is absent. Note the forgiveness is real: the retry
    handler swallows what it cannot chmod past, so this can return with files
    still on disk. Callers that must guarantee removal need
    :func:`_rmtree_strict`, not this.
    """
    path = Path(path)
    if not path.exists():
        return
    if ignore_errors:
        shutil.rmtree(path, ignore_errors=True)
        return
    try:  # Python 3.12+ uses onexc; older uses onerror.
        shutil.rmtree(path, onexc=_force_writable_then_remove)
    except TypeError:
        shutil.rmtree(
            path,
            onerror=lambda f, p, e: _force_writable_then_remove(f, p, e),
        )


def _rmtree_strict(path: Path) -> None:
    """Remove ``path``, or raise if anything survives.

    :func:`rmtree_forgiving` swallows the errors it cannot retry past, so it can
    return with files still on disk. The tracks, plugins, and engines pruned
    here gate held IP out of a snapshot that is then pushed to a public repo — a
    partial removal must abort the release, never be reported as a clean drop.
    """
    rmtree_forgiving(path)
    if path.exists():
        raise PruneError(f"failed to prune {path} from the release snapshot")


def prune_snapshot(
    root: Path,
    *,
    allowed_apps: Iterable[str],
    allowed_plugins: Iterable[str],
    allowed_engines: Iterable[str] | None,
    drop_tracks: Iterable[str] = (),
) -> PruneReport:
    """Prune a collected release snapshot to resolved tier membership.

    ``allowed_engines`` has no default: passing ``None`` disables the engine
    gate entirely, so every caller states that intent explicitly. Private daemon
    targets pass ``None`` because their engines ship wholesale; public releases
    pass an explicit allowlist.

    Raises :class:`PruneError` if a track, plugin, or engine that had to go is
    still on disk afterwards.
    """
    root = Path(root)
    app_allowlist = set(allowed_apps)
    plugin_allowlist = set(allowed_plugins)
    engine_allowlist = None if allowed_engines is None else set(allowed_engines)

    dropped_apps: list[str] = []
    apps_dir = root / "apps"
    if apps_dir.is_dir():
        for track in drop_tracks:
            track_dir = apps_dir / track
            if track_dir.is_dir():
                _rmtree_strict(track_dir)
                dropped_apps.append(f"{track}/")

        # Lenient by design, unlike the strict drops around it. Public releases
        # have already dropped the held tracks above, so a survivor here is an
        # out-of-tier app from a public track; private targets keep their tracks
        # but push to private repos. Either way a leftover is untidy, not a leak.
        iter_app_dirs = _load_iter_app_dirs()
        for app_id, app_dir in list(
            iter_app_dirs(apps_dir, include_personal=False)
        ):
            if app_id not in app_allowlist:
                rmtree_forgiving(app_dir, ignore_errors=True)
                dropped_apps.append(app_id)

        for directory in sorted(
            (path for path in apps_dir.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                if not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:
                pass

    dropped_plugins: list[str] = []
    plugins_dir = root / "plugins"
    if plugins_dir.is_dir():
        for child in sorted(plugins_dir.iterdir()):
            if child.is_dir() and child.name not in plugin_allowlist:
                _rmtree_strict(child)
                dropped_plugins.append(child.name)

    dropped_engines: list[str] = []
    engines_dir = root / "engines"
    if engines_dir.is_dir() and engine_allowlist is not None:
        for child in sorted(engines_dir.iterdir()):
            if child.is_dir() and child.name not in engine_allowlist:
                _rmtree_strict(child)
                dropped_engines.append(child.name)

    return PruneReport(
        dropped_apps=tuple(dropped_apps),
        dropped_plugins=tuple(dropped_plugins),
        dropped_engines=tuple(dropped_engines),
    )


# Regexes for spotting hard-bindings to specific apps in test source.
# Conservative — must not false-positive on tests that only use kept apps.
_APP_IMPORT_RE = re.compile(r"\b(?:from|import)\s+apps\.([a-z][a-z0-9_-]*)\b")
_PERSONAL_IMPORT_RE = re.compile(r"\bapps\.personal\b")
# Path-load patterns the test suite uses to grab non-package files
# (e.g. apps/model-bench/agent_bench.py). Quoted forms only — avoids hits
# from comments mentioning a dir.
_PATH_LOAD_RE = re.compile(r"['\"]apps/([a-z][a-z0-9_-]*)/[a-z_]+\.py['\"]")
# Pathlib-concatenation pattern: `_REPO_ROOT / "apps" / "model-bench"` style.
_PATHLIB_APPS_RE = re.compile(
    r"""['"]apps['"]\s*/\s*['"]([a-z][a-z0-9_-]*)['"]"""
)
# Sibling-module shim used by dogfood-agent tests after a sys.path insert.
_SIBLING_BEHAVIOR_RE = re.compile(r"^\s*import\s+behavior\b", re.MULTILINE)
# Engine imports: `from engines.<id>...` / `import engines.<id>`.
_ENGINE_IMPORT_RE = re.compile(
    r"\b(?:from|import)\s+engines\.([a-z][a-z0-9_]*)"
)


def drop_tests_bound_to(
    root: Path,
    allowed_apps: Iterable[str],
    allowed_engines: Iterable[str] | None,
) -> list[str]:
    """Drop tests that hard-bind to apps or engines absent from a snapshot.

    Returns display-ready descriptions for caller-owned logging.

    ``allowed_engines`` has no default: ``None`` skips engine-import filtering
    altogether, which is only correct when the snapshot kept every engine. That
    is the lenient shape, so a caller must ask for it rather than inherit it by
    omission — pair it with ``prune_snapshot(allowed_engines=None)``.
    """
    app_allowlist = set(allowed_apps)
    engine_allowlist = None if allowed_engines is None else set(allowed_engines)
    tests_dir = Path(root) / "tests"
    if not tests_dir.is_dir():
        return []

    dropped: list[str] = []
    for test_file in sorted(tests_dir.glob("test_*.py")):
        try:
            source = test_file.read_text(encoding="utf-8")
        except Exception:
            continue

        reasons: set[str] = set()
        if _PERSONAL_IMPORT_RE.search(source):
            reasons.add("apps.personal")
        if (
            _SIBLING_BEHAVIOR_RE.search(source)
            and "dogfood-agent" not in app_allowlist
        ):
            reasons.add("behavior (dogfood-agent shim)")
        for match in _APP_IMPORT_RE.finditer(source):
            if match.group(1) not in app_allowlist:
                reasons.add(f"apps.{match.group(1)}")
        for match in _PATH_LOAD_RE.finditer(source):
            if match.group(1) not in app_allowlist:
                reasons.add(f"apps/{match.group(1)}/...")
        for match in _PATHLIB_APPS_RE.finditer(source):
            if match.group(1) not in app_allowlist:
                reasons.add(f'"apps" / "{match.group(1)}"')
        if engine_allowlist is not None:
            for match in _ENGINE_IMPORT_RE.finditer(source):
                if match.group(1) not in engine_allowlist:
                    reasons.add(f"engines.{match.group(1)}")

        if reasons:
            test_file.unlink()
            dropped.append(
                f"{test_file.name} ({', '.join(sorted(reasons))})"
            )

    return dropped
