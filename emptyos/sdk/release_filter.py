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


# Repo subtrees that are git-tracked in the private repo but can never reach a
# public snapshot: both release paths prune `apps/personal/` via `drop_tracks`,
# `release-public.py` additionally prunes `tests/personal/` via `drop_test_dirs`,
# and no release tier lists a personal app or engine.
#
# This tuple exists because three safety scanners were written under the
# assumption "git-tracked ⇒ ships publicly" — true while these paths were
# gitignored, false from 2026-08-16 when the private repo started tracking them.
# `check-personal.py`, `check-branding.py` and `release-public.py`'s
# `check_no_private_apps` all consult it, so the exemption is one definition
# rather than three drifting lists. `assert_never_published_absent` is the
# receipt: it proves the claim against a built snapshot instead of trusting it.
#
# Adding a path here EXEMPTS it from those scanners, so it must be matched by an
# actual prune. Never add one without the corresponding drop.
#
# `apps/personal/` is a nested git repository (remote: emptyos-personal) and so
# is not tracked by this repo at all — it is listed anyway because a snapshot
# must not carry it either, and because the exemption should already be correct
# on the day someone flattens that repo into this one.
NEVER_PUBLISHED_PATHS = (
    "apps/personal/",
    "engines/personal/",
    "tests/personal/",
)


def is_never_published(rel_path: str) -> bool:
    """True if ``rel_path`` lives in a subtree that no public snapshot carries.

    Takes a repo-relative path in either separator style; callers hand these in
    straight from ``git ls-files`` (posix) or ``Path`` walks (native).
    """
    # `removeprefix`, not `lstrip("./")` — lstrip takes a character SET, so it
    # would eat every leading dot and slash and turn "../x" into "x".
    normalised = str(rel_path).replace("\\", "/").removeprefix("./")
    return normalised.startswith(NEVER_PUBLISHED_PATHS)


def prune_never_published(root: Path) -> list[str]:
    """Strictly remove every :data:`NEVER_PUBLISHED_PATHS` subtree from a snapshot.

    Exists because the per-kind gates each cover only one leg and a caller has to
    remember all three: ``drop_tracks`` handles ``apps/`` only, ``drop_test_dirs``
    handles ``tests/`` only, and the engine allowlist is *disabled*
    (``allowed_engines=None``) for the private daemon targets — which left
    ``engines/personal/`` with nothing between it and a dist once it became
    git-tracked. One call that removes exactly what the constant names cannot
    forget a leg. Idempotent: already-absent paths are skipped.
    """
    root = Path(root)
    removed: list[str] = []
    for rel in NEVER_PUBLISHED_PATHS:
        target = root / rel
        if target.is_dir():
            _rmtree_strict(target)
            removed.append(rel)
    return removed


def assert_never_published_absent(root: Path) -> None:
    """Raise :class:`PruneError` if a never-published subtree survived pruning.

    The scanners above stop looking at these paths, so their absence from the
    snapshot has to be *proved* rather than inferred from the prune arguments
    having been passed. Cheap, and it fails the release rather than shipping.
    """
    root = Path(root)
    survivors = [p for p in NEVER_PUBLISHED_PATHS if (root / p).exists()]
    if survivors:
        joined = ", ".join(survivors)
        raise PruneError(
            f"never-published paths survived into the snapshot: {joined}. "
            "These are exempt from the personal-data and branding scanners, so "
            "shipping them would ship unscanned personal content."
        )


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
    drop_test_dirs: Iterable[str] = (),
) -> PruneReport:
    """Prune a collected release snapshot to resolved tier membership.

    ``allowed_engines`` has no default: passing ``None`` disables the engine
    gate entirely, so every caller states that intent explicitly. Private daemon
    targets pass ``None`` because their engines ship wholesale; public releases
    pass an explicit allowlist.

    ``drop_test_dirs`` removes whole subdirectories of ``tests/`` by name. It
    exists because :func:`drop_tests_bound_to` globs ``tests/test_*.py``
    non-recursively and so cannot see ``tests/personal/`` at all — harmless
    while that path was gitignored (``git archive`` never collected it), a leak
    from the moment the private repo started tracking it.

    Raises :class:`PruneError` if a track, test dir, plugin, or engine that had
    to go is still on disk afterwards.
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

    tests_dir = root / "tests"
    if tests_dir.is_dir():
        for name in drop_test_dirs:
            candidate = tests_dir / name
            if candidate.is_dir():
                _rmtree_strict(candidate)
                dropped_apps.append(f"tests/{name}/")

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

# A system test binds to an app through its HTTP ROUTE, not a Python import:
# test_sys_grill.py never imports the app, it just GETs /grill/api/*. Every regex
# above is import-shaped, so those tests sailed into the public snapshot and could
# never pass there (v0.5.5 shipped test_sys_{grill,explore,bess_analyser}.py against
# apps that do not exist in the public tree).
#
# Deliberately narrow to stay false-positive-free: the prefix must be followed by
# `api/` or immediately closed. That matches "/grill/" and "/bess-analyser/api/runs"
# but NOT a filesystem path like "/tmp/foo" or a URL like "https://x/y/".
_HTTP_ROUTE_RE = re.compile(r"""['"]/([a-z][a-z0-9-]*)/(?:api/|['"])""")

# Platform routes served by the kernel, not by any app — never a binding signal.
_PLATFORM_ROUTE_PREFIXES = frozenset(
    {"api", "static", "ws", "docs", "system", "topology", "debug", "pages"}
)

# test_sys_* files that test the PLATFORM, not an app. They reference app routes
# incidentally (auth drives a public-face route; snapshot_boot boots a synthetic
# app tree), so the app-scoped heuristic below would drop them. Each entry is a
# measured false positive, not a guess.
_PLATFORM_TEST_STEMS = frozenset(
    {
        "test_sys_auth",
        "test_sys_snapshot_boot",
        "test_sys_mobile",
        "test_sys_pwa",
        "test_sys_readability",
        "test_sys_app_nav",
        "test_sys_ui_affordance",
    }
)
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
        # A route reference alone is NOT binding — test_journeys.py and
        # test_edge_cases.py legitimately touch many apps (the latter pokes
        # /zzz-no-such-app/ on purpose), and dropping them would delete real public
        # coverage. Measured: the bare-route signal drops 113 files and takes
        # test_sys_kb.py — a PUBLIC app — with it.
        #
        # Bound means the absent app is the test's own SUBJECT, which its filename
        # declares: test_sys_grill.py ↔ /grill/, test_dogfood_jobs.py ↔ /jobs/.
        # Require both, and the signal is exact.
        stem = test_file.stem  # e.g. test_sys_bess_analyser, test_sys_cable_rating_report
        # An app-scoped test (test_sys_*) that never names a SHIPPED app in its
        # filename is a test for an app that does not ship — whatever routes it
        # happens to call. test_sys_cable_rating_report.py hits /cable-network/,
        # so a filename↔route match alone would have missed it.
        # test_sys_* usually means "system test FOR an app", but a few test the
        # PLATFORM and merely poke an app's route while doing so (auth exercises a
        # public-face route; snapshot_boot boots a synthetic tree). Measured as real
        # false positives — they must not be treated as app-bound.
        app_scoped = (
            stem.startswith("test_sys_") and stem not in _PLATFORM_TEST_STEMS
        )
        names_public_app = any(
            ("_" + app.replace("-", "_")) in stem for app in app_allowlist
        )
        for match in _HTTP_ROUTE_RE.finditer(source):
            prefix = match.group(1)
            if prefix in _PLATFORM_ROUTE_PREFIXES or prefix in app_allowlist:
                continue
            # Containment, not endswith: test_sys_cable_rating_report.py IS a
            # cable-rating test — the suffix names the aspect, not another subject.
            bound_by_name = ("_" + prefix.replace("-", "_")) in stem
            if bound_by_name or (app_scoped and not names_public_app):
                reasons.add(f"/{prefix}/ (route of an absent app)")

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
