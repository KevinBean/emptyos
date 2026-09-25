"""Store — marketplace install of third-party apps (registry / GitHub / zip / folder).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the marketplace install pipeline — fetching the official
registry index, resolving an install source to a temp dir, validating +
py_compile-gating the app, the propose/preview/confirm trust gate, and the
commit into a category folder + ``data/store/sources.json``.

Each install declares a **category** (track/group, e.g. ``extension/engineering``,
``public/labs``, ``personal``) and lands at ``apps/<category>/<id>/`` — a
first-class member of the track tree the loader scans, not a segregated dump.
Curated public tracks (``public/core``, ``public/standard``) are NOT install
targets; the default bucket is ``extension/others``. Installed third-party code
is kept out of git via a per-clone ``.git/info/exclude`` line (NOT the tracked
``.gitignore``), so it can sit in any category yet never be committed/shipped.
Provenance (source + category + install dir + exclude line) is recorded per id
in ``data/store/sources.json``.

Trust model (``.claude/rules/store.md`` + ``.claude/rules/proposed-action.md``):
preview returns a manifest summary (capabilities, deps, routes, file list)
behind a confirm token; NO code runs at install — the next ``restart.bat``
loads it, after the user confirmed. Tarball download (not ``git clone``) avoids
git-hook execution; zip extraction uses ``filter="data"`` for path safety;
a py_compile gate keeps a syntax error from wedging the next boot.

Cross-module callers reach these via ``self.X`` after re-binding in app.py.
Reaches into app.py helpers: ``_apps_root``, ``_move_dir``. Do not import
from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from emptyos.sdk import web_route
from emptyos.runtime import store_state
from emptyos.kernel.app_loader import AppManifest, ESSENTIAL_APPS
from emptyos.kernel.plugin_loader import PluginManifest, ESSENTIAL_PLUGINS

if TYPE_CHECKING:  # for type hints only
    from .app import StoreApp  # noqa: F401


# ─── Bind to StoreApp class as ───────────────────────────────────────────
#   api_marketplace_registry   = _marketplace.api_marketplace_registry
#   api_marketplace_preview    = _marketplace.api_marketplace_preview
#   api_marketplace_upload     = _marketplace.api_marketplace_upload
#   api_marketplace_install    = _marketplace.api_marketplace_install
#   api_marketplace_installed  = _marketplace.api_marketplace_installed
#   _install_dest              = _marketplace._install_dest
#   _git_exclude_path          = _marketplace._git_exclude_path
#   _add_git_exclude           = _marketplace._add_git_exclude
#   _remove_git_exclude        = _marketplace._remove_git_exclude
#   _sources_path              = _marketplace._sources_path
#   _load_sources_all          = _marketplace._load_sources_all
#   _load_sources              = _marketplace._load_sources
#   _save_sources              = _marketplace._save_sources
#   _record_source             = _marketplace._record_source
#   _drop_source               = _marketplace._drop_source
#   _mkt_uninstall             = _marketplace._mkt_uninstall
#   _find_app_dirs             = _marketplace._find_app_dirs
#   _validate_app_dir          = _marketplace._validate_app_dir
#   _validate_plugin_dir       = _marketplace._validate_plugin_dir
#   _validate_dir              = _marketplace._validate_dir
#   _read_plugin_blacklist     = _marketplace._read_plugin_blacklist
#   _pycompile_gate            = _marketplace._pycompile_gate
#   _manifest_summary          = _marketplace._manifest_summary
#   _plugin_manifest_summary   = _marketplace._plugin_manifest_summary
#   _mkt_pending_store         = _marketplace._mkt_pending_store
#   _mkt_sweep_pending         = _marketplace._mkt_sweep_pending
#   _resolve_source            = _marketplace._resolve_source
#   _fetch_registry_index      = _marketplace._fetch_registry_index
#   _mkt_tmp_base              = _marketplace._mkt_tmp_base
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────────


# Valid app id — lowercase, starts with a letter, alnum + hyphen.
_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# github.com/user/repo  (optionally /tree/<ref>/<subdir...>)
_GITHUB_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/]+)(?:/(?P<subdir>.+?))?)?/?$"
)

# Confirm-token TTL: a preview's resolved temp dir is kept this long.
_PENDING_TTL_S = 3600

# Cap on a fetched/uploaded payload before extraction (defensive).
_MAX_PAYLOAD_MB = 50


# ── Pure-ish path + state helpers ───────────────────────────────────────

def _mkt_tmp_base(self: "StoreApp") -> Path:
    base = Path(self.kernel.config.data_dir) / "store" / "_mktmp"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _sources_path(self: "StoreApp") -> Path:
    return Path(self.kernel.config.data_dir) / "store" / "sources.json"


def _load_sources_all(self: "StoreApp") -> dict:
    """Read data/store/sources.json in the namespaced shape.

    Target shape: ``{"schema": 2, "apps": {id: descr}, "plugins": {id: descr}}``.
    A legacy flat dict (``{id: descr}``, the pre-plugin format) is migrated into
    the ``apps`` namespace on read and persisted lazily on the next record/drop.
    Tolerant of a missing/corrupt/non-dict file.
    """
    p = self._sources_path()
    if not p.exists():
        return {"schema": 2, "apps": {}, "plugins": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return {"schema": 2, "apps": {}, "plugins": {}}
    if not isinstance(data, dict):
        return {"schema": 2, "apps": {}, "plugins": {}}
    # Legacy flat shape (no namespace markers) → migrate into apps. No real
    # app/plugin id is named apps/plugins/schema, so this can't misfire.
    if "schema" not in data and "apps" not in data and "plugins" not in data:
        return {"schema": 2, "apps": data, "plugins": {}}
    data.setdefault("apps", {})
    data.setdefault("plugins", {})
    data["schema"] = 2
    return data


def _load_sources(self: "StoreApp", kind: str = "apps") -> dict:
    """Per-kind source descriptors — ``{id: source_descriptor}``. Tolerant.

    Defaults to ``apps`` so every pre-plugin call site keeps working unchanged.
    """
    sub = self._load_sources_all().get(kind, {})
    return sub if isinstance(sub, dict) else {}


def _save_sources(self: "StoreApp", data: dict) -> None:
    p = self._sources_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _record_source(self: "StoreApp", item_id: str, descriptor: dict, kind: str = "apps") -> None:
    data = self._load_sources_all()
    data.setdefault(kind, {})[item_id] = descriptor
    self._save_sources(data)


def _drop_source(self: "StoreApp", item_id: str, kind: str = "apps") -> None:
    data = self._load_sources_all()
    if item_id in data.get(kind, {}):
        del data[kind][item_id]
        self._save_sources(data)


# ── Install category + git-exclude (keep installed code out of git) ─────

# An install lands in one of these category folders (track/group). Curated
# public tracks (public/core, public/standard) are NOT install targets — those
# are first-party + shipped. Default bucket is extension/others.
_DEFAULT_CATEGORY = "extension/others"
_VALID_CATEGORIES = {
    "extension/engineering", "extension/english-learning", "extension/portfolio",
    "extension/plekto", "extension/dev", "extension/others", "extension/labs",
    "public/labs", "personal", "personal/labs",
}


def _norm_category(cat: str | None) -> str:
    cat = (cat or "").strip().strip("/").replace("\\", "/")
    return cat if cat in _VALID_CATEGORIES else ""


def _install_dest(self: "StoreApp", kind: str, category: str, item_id: str) -> Path:
    """Where a marketplace install lands.

    apps    → apps/<category>/<id>/  (category track/group)
    plugins → plugins/<id>/          (flat — matches the loader's flat glob;
                                      category is ignored)
    """
    if kind == "plugins":
        return self._plugins_root() / item_id
    return self._apps_root() / category / item_id


def _git_exclude_path(self: "StoreApp") -> Path:
    return Path(self.kernel.config.path).parent / ".git" / "info" / "exclude"


def _exclude_line_for(kind: str, category: str, item_id: str) -> str:
    if kind == "plugins":
        return f"plugins/{item_id}/"
    return f"apps/{category}/{item_id}/"


def _add_git_exclude(self: "StoreApp", line: str) -> None:
    """Append a gitignore-syntax line to .git/info/exclude (per-clone, NOT the
    tracked .gitignore) so installed third-party code is never committed."""
    p = self._git_exclude_path()
    if not p.parent.is_dir():
        return
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    if line in lines:
        return
    with open(p, "a", encoding="utf-8") as f:
        if lines and lines[-1].strip():
            f.write("\n")
        f.write(line + "\n")


def _remove_git_exclude(self: "StoreApp", line: str) -> None:
    p = self._git_exclude_path()
    if not p.exists():
        return
    kept = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() != line.strip()]
    p.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


# ── Pending preview stash (in-memory, per-daemon) ───────────────────────

def _mkt_pending_store(self: "StoreApp") -> dict:
    store = getattr(self, "_mkt_pending", None)
    if store is None:
        store = {}
        self._mkt_pending = store  # type: ignore[attr-defined]
    return store


def _mkt_sweep_pending(self: "StoreApp") -> None:
    """Drop expired preview stashes and rmtree their temp dirs."""
    store = self._mkt_pending_store()
    now = time.time()
    for token in list(store.keys()):
        entry = store[token]
        if now - entry.get("created", 0) > _PENDING_TTL_S:
            shutil.rmtree(entry.get("root", ""), ignore_errors=True)
            del store[token]


# ── App-dir discovery + validation ──────────────────────────────────────

def _find_app_dirs(self: "StoreApp", root: Path) -> list[Path]:
    """Dirs under (or equal to) root that contain a manifest.toml.

    Handles three shapes: root IS the app (root/manifest.toml), root holds
    one app subdir, or a GitHub tarball's single top-level wrapper dir.
    Searched shallowly (depth ≤ 2) so we don't walk node_modules-style trees.
    """
    found: list[Path] = []
    if (root / "manifest.toml").is_file():
        found.append(root)
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        if (child / "manifest.toml").is_file():
            found.append(child)
        else:
            # one level deeper (tarball wrapper: repo-sha/<app>/manifest.toml)
            for grandchild in sorted(p for p in child.iterdir() if p.is_dir()):
                if (grandchild / "manifest.toml").is_file():
                    found.append(grandchild)
    # de-dupe while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for p in found:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _pycompile_gate(self: "StoreApp", app_dir: Path) -> str | None:
    """py_compile every *.py under app_dir. Returns an error string or None.

    A syntax error here would make the daemon fail to load the app at the
    next restart — catch it before the folder is committed.
    """
    import py_compile

    for py in sorted(app_dir.rglob("*.py")):
        try:
            py_compile.compile(str(py), doraise=True)
        except py_compile.PyCompileError as e:
            rel = py.relative_to(app_dir)
            return f"syntax error in {rel}: {e.msg if hasattr(e, 'msg') else e}"
        except OSError as e:
            return f"could not compile {py.name}: {e}"
    return None


def _validate_app_dir(self: "StoreApp", app_dir: Path) -> tuple[AppManifest | None, str | None]:
    """Parse + sanity-check an app dir. Returns (manifest, error)."""
    manifest_file = app_dir / "manifest.toml"
    if not manifest_file.is_file():
        return None, "no manifest.toml in app folder"
    try:
        m = AppManifest.from_toml(manifest_file)
    except Exception as e:  # noqa: BLE001 — surface any parse failure to the user
        return None, f"manifest.toml is invalid: {e}"

    if not m.id or not _ID_RE.match(m.id):
        return None, f"invalid app id '{m.id}' (must match ^[a-z][a-z0-9-]*$)"
    if m.id in ESSENTIAL_APPS:
        return None, f"'{m.id}' is a reserved essential app id"

    # Collision: an id already owned by a first-party / personal app can't be
    # overwritten. A same-id app previously installed from the marketplace
    # (tracked in sources.json) is an update — allowed.
    existing = self.kernel.apps.manifests.get(m.id)
    if existing is not None and m.id not in self._load_sources():
        return None, (
            f"'{m.id}' already exists as a built-in/local app — "
            "rename the app you're installing to avoid the clash"
        )

    err = self._pycompile_gate(app_dir)
    if err:
        return None, err
    return m, None


def _read_plugin_blacklist(self: "StoreApp") -> set[str]:
    """Plugin ids in plugins/BLACKLIST.toml. Tolerant of a missing/bad file.

    Required for the collision check: a blacklisted id never enters
    ``kernel.plugins.manifests`` (it's dropped at discovery), so the manifest
    collision check alone would let it install — then it'd be silently dropped
    at the next boot. Refuse it up front instead.
    """
    import tomllib

    bl = self._plugins_root() / "BLACKLIST.toml"
    if not bl.is_file():
        return set()
    try:
        with open(bl, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return set()
    out: set[str] = set()
    for entry in data.get("blacklisted", []) or []:
        pid = (entry or {}).get("id") if isinstance(entry, dict) else None
        if pid:
            out.add(pid)
    return out


def _validate_plugin_dir(self: "StoreApp", plugin_dir: Path) -> tuple["PluginManifest | None", str | None]:
    """Parse + sanity-check a plugin dir. Returns (manifest, error).

    Plugins execute code at daemon boot (``connect()``/``auto_start()``), so the
    bar is the same gates as apps PLUS a mandatory static scan in the caller.
    """
    manifest_file = plugin_dir / "manifest.toml"
    if not manifest_file.is_file():
        return None, "no manifest.toml in plugin folder"
    try:
        m = PluginManifest.from_toml(manifest_file)
    except KeyError:
        # from_toml does plugin_section["id"] — a missing [plugin] section
        # is the giveaway that this is an app, not a plugin.
        return None, "manifest has no [plugin] section — is this an app, not a plugin?"
    except Exception as e:  # noqa: BLE001 — surface any parse failure to the user
        return None, f"manifest.toml is invalid: {e}"

    if not m.id or not _ID_RE.match(m.id):
        return None, f"invalid plugin id '{m.id}' (must match ^[a-z][a-z0-9-]*$)"
    if m.id in ESSENTIAL_PLUGINS:
        return None, f"'{m.id}' is a reserved essential plugin id"
    if m.id in self._read_plugin_blacklist():
        return None, f"'{m.id}' is blacklisted and cannot be installed"

    # Collision: an id already owned by a first-party plugin can't be
    # overwritten. A same-id plugin previously installed from the marketplace
    # (tracked in the plugins sources namespace) is an update — allowed.
    existing = self.kernel.plugins.manifests.get(m.id)
    if existing is not None and m.id not in self._load_sources("plugins"):
        return None, (
            f"'{m.id}' already exists as a built-in/local plugin — "
            "rename the plugin you're installing to avoid the clash"
        )

    err = self._pycompile_gate(plugin_dir)
    if err:
        return None, err
    return m, None


def _validate_dir(self: "StoreApp", kind: str, d: Path):
    """Dispatch validation by kind. Returns (manifest, error)."""
    if kind == "plugins":
        return self._validate_plugin_dir(d)
    return self._validate_app_dir(d)


def _manifest_summary(self: "StoreApp", m: AppManifest, app_dir: Path, source: dict, category: str) -> dict:
    """Review-gate payload: what this app declares + which files it ships."""
    web = m.provides.get("web", {}) or {}
    routes = []
    if isinstance(web, dict) and web.get("prefix"):
        routes.append(web["prefix"])
    files = []
    for p in sorted(app_dir.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            files.append(str(p.relative_to(app_dir)).replace("\\", "/"))
    is_update = self.kernel.apps.manifests.get(m.id) is not None
    return {
        "id": m.id,
        "name": m.name,
        "version": m.version,
        "description": m.description,
        "capabilities": list(m.requires.get("capabilities", []) or []),
        "requires_apps": list(m.requires.get("apps", []) or []),
        "routes": routes,
        "emits": list(m.events_emits or []),
        "files": files[:200],
        "file_count": len(files),
        "source": dict(source),
        "category": category,
        "is_update": is_update,
    }


def _plugin_manifest_summary(self: "StoreApp", m: "PluginManifest", plugin_dir: Path, source: dict) -> dict:
    """Review-gate payload for a plugin. Note: load_time_exec is always true —
    a plugin's connect()/auto_start() runs at daemon boot, which drives the
    stronger trust warning in the UI."""
    prov = m.provides or {}
    cap = prov.get("capability", {}) or {}
    req = ((m.raw.get("requires", {}) or {}).get("plugins", []) or [])
    installed = self.kernel.plugins.installed_ids()
    files = [
        str(p.relative_to(plugin_dir)).replace("\\", "/")
        for p in sorted(plugin_dir.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    ]
    return {
        "kind": "plugins",
        "id": m.id,
        "name": m.name,
        "version": m.version,
        "description": m.description,
        "services": list(prov.get("services", []) or []),
        "tags": list(prov.get("tags", []) or []),
        "capabilities_provided": list(cap.keys()) if isinstance(cap, dict) else [],
        "requires_plugins": list(req),
        "missing_plugin_deps": [d for d in req if d not in installed],
        "load_time_exec": True,
        "files": files[:200],
        "file_count": len(files),
        "source": dict(source),
        "is_update": self.kernel.plugins.manifests.get(m.id) is not None,
    }


# ── Source resolution → temp dir ────────────────────────────────────────

async def _resolve_source(self: "StoreApp", source: dict, kind: str = "apps") -> tuple[Path | None, dict, str | None]:
    """Resolve an install source into a temp dir. Returns (root, norm_source, error).

    `root` is a temp directory containing the fetched payload; the caller runs
    _find_app_dirs on it. `norm_source` is the canonicalised descriptor stored
    in sources.json on commit. Zip uploads are NOT handled here (they arrive as
    raw bytes via the upload route) — types: registry, github, folder.
    """
    stype = (source or {}).get("type", "")
    if stype == "registry":
        return await _resolve_registry(self, source, kind)
    if stype == "github":
        return await _resolve_github(self, source)
    if stype == "folder":
        return _resolve_folder(self, source)
    return None, {}, f"unknown source type: {stype!r}"


async def _resolve_registry(self: "StoreApp", source: dict, kind: str = "apps") -> tuple[Path | None, dict, str | None]:
    item_id = (source or {}).get("id", "")
    if not item_id:
        return None, {}, "registry install needs an id"
    index, err = await self._fetch_registry_index()
    if err:
        return None, {}, err
    entry = next((a for a in index.get(kind, []) if a.get("id") == item_id), None)
    if entry is None:
        return None, {}, f"'{item_id}' not found in the registry"
    gh = entry.get("source", {}) or {}
    if gh.get("type") != "github":
        return None, {}, f"registry entry '{item_id}' has an unsupported source type"
    # carry the registry version + declared category forward
    gh = {**gh, "_registry_version": entry.get("version", ""),
          "_registry_category": entry.get("category", "")}
    return await _resolve_github(self, gh)


async def _resolve_github(self: "StoreApp", source: dict) -> tuple[Path | None, dict, str | None]:
    """Download a GitHub repo tarball (no git clone → no hook execution)."""
    owner = source.get("owner") or ""
    repo = source.get("repo") or ""
    ref = source.get("ref") or ""
    subdir = (source.get("subdir") or "").strip("/")

    # Accept either {owner, repo} or {repo: "owner/name"} or {url: "github.com/..."}.
    if source.get("url") and not (owner and repo):
        mm = _GITHUB_RE.match(source["url"].strip())
        if not mm:
            return None, {}, "could not parse that GitHub URL"
        owner = mm.group("owner")
        repo = mm.group("repo")
        ref = ref or (mm.group("ref") or "")
        subdir = subdir or (mm.group("subdir") or "").strip("/")
    elif repo and "/" in repo and not owner:
        owner, repo = repo.split("/", 1)

    if not owner or not repo:
        return None, {}, "GitHub source needs owner/repo"
    ref = ref or "main"

    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}"
    root = Path(self._mkt_tmp_base()) / f"gh-{secrets.token_hex(6)}"
    root.mkdir(parents=True, exist_ok=True)
    tarball = root / "src.tar.gz"
    err = await _download(self, url, tarball)
    if err:
        # Retry once on the other common default branch.
        if ref == "main":
            url2 = f"https://codeload.github.com/{owner}/{repo}/tar.gz/master"
            err2 = await _download(self, url2, tarball)
            if err2:
                shutil.rmtree(root, ignore_errors=True)
                return None, {}, f"download failed (tried main + master): {err}"
            ref = "master"
        else:
            shutil.rmtree(root, ignore_errors=True)
            return None, {}, f"download failed: {err}"

    extract_dir = root / "x"
    extract_dir.mkdir(exist_ok=True)
    perr = _safe_extract_tar(tarball, extract_dir)
    if perr:
        shutil.rmtree(root, ignore_errors=True)
        return None, {}, perr
    tarball.unlink(missing_ok=True)

    base = extract_dir
    if subdir:
        # tarball wraps everything in repo-<ref>/ ; descend into it then subdir
        wrappers = [p for p in extract_dir.iterdir() if p.is_dir()]
        wrap = wrappers[0] if len(wrappers) == 1 else extract_dir
        cand = wrap / subdir
        if not cand.is_dir():
            shutil.rmtree(root, ignore_errors=True)
            return None, {}, f"subdir '{subdir}' not found in the repo"
        base = cand

    norm = {
        "type": "github",
        "url": f"github.com/{owner}/{repo}",
        "owner": owner,
        "repo": repo,
        "ref": ref,
        "subdir": subdir,
    }
    if source.get("_registry_version"):
        norm["registry_version"] = source["_registry_version"]
    if _norm_category(source.get("_registry_category")):
        norm["category"] = _norm_category(source["_registry_category"])
    return base, norm, None


def _resolve_folder(self: "StoreApp", source: dict) -> tuple[Path | None, dict, str | None]:
    """Copy a local folder into a temp dir (the user keeps their original)."""
    raw = (source or {}).get("path", "")
    if not raw:
        return None, {}, "folder install needs a path"
    src = Path(raw).expanduser()
    if not src.is_dir():
        return None, {}, f"not a folder: {src}"
    root = Path(self._mkt_tmp_base()) / f"folder-{secrets.token_hex(6)}"
    try:
        shutil.copytree(src, root)
    except OSError as e:
        shutil.rmtree(root, ignore_errors=True)
        return None, {}, f"copy failed: {e}"
    norm = {"type": "folder", "url": str(src)}
    return root, norm, None


async def _download(self: "StoreApp", url: str, dest: Path) -> str | None:
    """Fetch url → dest (size-capped). Returns an error string or None.

    Raw aiohttp by design (CLAUDE.md Dev Rule 1 exception): no capability fits
    "download an arbitrary tarball to disk" — `browse` is a headless browser,
    `read` is the filesystem. The Store is infrastructure fetching third-party
    code (same exception class as plugins wrapping external binaries), so a
    direct, size-capped HTTP GET is the right tool. Trust comes from the
    review-gate + py_compile + skill_scan, not the transport.
    """
    try:
        import aiohttp
    except ImportError:
        return "aiohttp not installed"
    try:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return f"HTTP {resp.status} from {url}"
                limit = _MAX_PAYLOAD_MB * 1024 * 1024
                size = 0
                with open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        size += len(chunk)
                        if size > limit:
                            return f"payload exceeds {_MAX_PAYLOAD_MB} MB"
                        f.write(chunk)
        return None
    except Exception as e:  # noqa: BLE001 — network errors are user-facing
        return f"{type(e).__name__}: {e}"


def _safe_extract_tar(tarball: Path, dest: Path) -> str | None:
    import os
    import tarfile
    try:
        dest_real = os.path.realpath(dest)
        with tarfile.open(tarball, mode="r:gz") as tar:
            # Validate every member before extracting anything: reject paths
            # that escape dest (traversal) and reject sym/hard links. This makes
            # the filter="data" fallback below safe even on a hypothetical
            # interpreter without it.
            for member in tar.getmembers():
                target = os.path.realpath(os.path.join(dest, member.name))
                if target != dest_real and not target.startswith(dest_real + os.sep):
                    return f"unsafe path in archive: {member.name}"
                if member.issym() or member.islnk():
                    return f"links not allowed in archive: {member.name}"
            try:
                tar.extractall(path=dest, filter="data")  # py3.12+ path-safety
            except TypeError:
                tar.extractall(path=dest)  # members already validated above
        return None
    except (tarfile.TarError, OSError) as e:
        return f"could not extract archive: {e}"


def _safe_extract_zip(data: bytes, dest: Path) -> str | None:
    import io
    import os
    import zipfile
    try:
        dest_real = os.path.realpath(dest)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # Validate every entry before extracting: reject traversal (use an
            # os.sep boundary so "/dest" doesn't match "/dest-evil") and reject
            # symlink entries (external_attr high bits == 0o120000).
            for info in zf.infolist():
                target = os.path.realpath(os.path.join(dest, info.filename))
                if target != dest_real and not target.startswith(dest_real + os.sep):
                    return f"unsafe path in zip: {info.filename}"
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    return f"symlinks not allowed in zip: {info.filename}"
            zf.extractall(dest)
        return None
    except (zipfile.BadZipFile, OSError) as e:
        return f"could not extract zip: {e}"


# ── Registry index fetch ────────────────────────────────────────────────

async def _fetch_registry_index(self: "StoreApp") -> tuple[dict, str | None]:
    """Return the official registry index ({apps: [...]}, error).

    Source order: a configured remote ``registry_url`` (http/https), else the
    in-repo ``registry/index.json`` (the marketplace we ship). Keeps personal
    handles out of committed defaults (CLAUDE.md rule 13).
    """
    url = (self.app_config("registry_url", "") or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        dest = Path(self._mkt_tmp_base()) / "registry.json"
        err = await _download(self, url, dest)
        if err:
            return {}, f"registry fetch failed: {err}"
        try:
            data = json.loads(dest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return {}, f"registry index is invalid: {e}"
        return (data if isinstance(data, dict) else {"apps": []}), None

    local = self.repo_root / "registry" / "index.json"
    if not local.is_file():
        return {"apps": []}, None
    try:
        data = json.loads(local.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {}, f"local registry index is invalid: {e}"
    return (data if isinstance(data, dict) else {"apps": []}), None


# ── Routes ──────────────────────────────────────────────────────────────

@web_route("GET", "/api/marketplace/registry")
async def api_marketplace_registry(self: "StoreApp", request) -> dict:
    """Official registry catalog, annotated with local install state.

    `?kind=apps` (default) or `?kind=plugins` picks which registry array +
    install-state namespace to read.
    """
    kind = request.query_params.get("kind", "apps")
    if kind not in ("apps", "plugins"):
        return {"error": f"invalid kind '{kind}'", "items": []}
    index, err = await self._fetch_registry_index()
    if err:
        return {"error": err, "items": []}
    reg = self.kernel.plugins if kind == "plugins" else self.kernel.apps
    installed = reg.installed_ids()
    sources = self._load_sources(kind)
    items = []
    for a in index.get(kind, []) or []:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        aid = a["id"]
        items.append({
            "id": aid,
            "name": a.get("name", aid),
            "description": a.get("description", ""),
            "category": a.get("category", "other"),
            "version": a.get("version", ""),
            "author": a.get("author", ""),
            "capabilities": list(a.get("capabilities", []) or []),
            "installed": aid in installed,
            "from_marketplace": aid in sources,
        })
    items.sort(key=lambda x: (x["category"], x["name"].lower()))
    url = (self.app_config("registry_url", "") or "").strip()
    return {"items": items, "source": url or "registry/index.json (in-repo)"}


@web_route("POST", "/api/marketplace/preview")
async def api_marketplace_preview(self: "StoreApp", request) -> dict:
    """Resolve a source (registry / github / folder), validate, stash, and
    return the review-gate summary + a confirm token. Installs nothing."""
    body = await self.safe_json(request)
    source = body.get("source") or {}
    if not isinstance(source, dict):
        return {"error": "source must be an object"}
    kind = body.get("kind") or "apps"
    if kind not in ("apps", "plugins"):
        return {"error": f"invalid kind '{kind}' — must be 'apps' or 'plugins'"}
    req_cat = body.get("category") or ""
    # Category only applies to apps; plugins land flat at plugins/<id>/.
    if kind == "apps" and req_cat and not _norm_category(req_cat):
        return {"error": f"invalid category '{req_cat}' — choose extension/<group>, public/labs, or personal[/labs]"}
    self._mkt_sweep_pending()

    root, norm, err = await self._resolve_source(source, kind)
    if err:
        return {"error": err}
    # Category priority: registry-declared > request-supplied > default.
    category = norm.get("category") or req_cat or ""
    return _stash_and_summarise(self, root, norm, category, kind)


@web_route("POST", "/api/marketplace/upload")
async def api_marketplace_upload(self: "StoreApp", request) -> dict:
    """Zip upload → extract → validate → stash → review-gate summary + token."""
    self._mkt_sweep_pending()
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001
        return {"error": "expected a multipart form upload"}
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return {"error": "no file in upload (field name must be 'file')"}
    kind = form.get("kind") or "apps"
    if kind not in ("apps", "plugins"):
        return {"error": f"invalid kind '{kind}' — must be 'apps' or 'plugins'"}
    req_cat = form.get("category") or ""
    if kind == "apps" and req_cat and not _norm_category(req_cat):
        return {"error": f"invalid category '{req_cat}' — choose extension/<group>, public/labs, or personal[/labs]"}
    data = await upload.read()
    if not data:
        return {"error": "empty file"}
    if len(data) > _MAX_PAYLOAD_MB * 1024 * 1024:
        return {"error": f"zip exceeds {_MAX_PAYLOAD_MB} MB"}

    root = Path(self._mkt_tmp_base()) / f"zip-{secrets.token_hex(6)}"
    root.mkdir(parents=True, exist_ok=True)
    perr = _safe_extract_zip(data, root)
    if perr:
        shutil.rmtree(root, ignore_errors=True)
        return {"error": perr}
    norm = {"type": "zip", "url": getattr(upload, "filename", "") or "upload.zip"}
    return _stash_and_summarise(self, root, norm, req_cat, kind)


@web_route("POST", "/api/marketplace/install")
async def api_marketplace_install(self: "StoreApp", request) -> dict:
    """Commit a previewed install by confirm token.

    apps    → apps/<category>/<id>/
    plugins → plugins/<id>/   (flat)

    The kind is read from the stashed pending entry (the token is the source of
    truth), so the install body only carries the token.
    """
    body = await self.safe_json(request)
    token = body.get("token") or ""
    store = self._mkt_pending_store()
    self._mkt_sweep_pending()
    entry = store.get(token)
    if entry is None:
        return {"error": "preview expired or unknown — run preview again"}

    kind = entry.get("kind") or "apps"
    item_id = entry["manifest_id"]
    item_dir = Path(entry["app_dir"])
    root = Path(entry["root"])
    norm = entry["source"]
    version = entry["version"]
    category = "" if kind == "plugins" else (_norm_category(entry.get("category")) or _DEFAULT_CATEGORY)
    noun = "plugin" if kind == "plugins" else "app"
    reg = self.kernel.plugins if kind == "plugins" else self.kernel.apps

    # Re-check collision at commit time (the world may have moved). A same-id
    # item already tracked in this kind's sources namespace is an update.
    existing = reg.manifests.get(item_id)
    if existing is not None and item_id not in self._load_sources(kind):
        shutil.rmtree(root, ignore_errors=True)
        del store[token]
        return {"error": f"'{item_id}' now clashes with a built-in/local {noun} — aborted"}

    dst = self._install_dest(kind, category, item_id)
    # If updating an item that was previously installed into a DIFFERENT
    # location, remove the old folder + its exclude line first.
    prior = self._load_sources(kind).get(item_id) or {}
    if prior.get("exclude_line"):
        self._remove_git_exclude(prior["exclude_line"])
    prior_dir = prior.get("install_dir")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if prior_dir and Path(prior_dir).is_dir() and Path(prior_dir).resolve() != dst.resolve():
            shutil.rmtree(prior_dir, ignore_errors=True)
        if dst.exists():  # update in place: replace the prior copy
            shutil.rmtree(dst)
        # rename is atomic on the same volume (data/ + apps/ + plugins/ under repo)
        try:
            item_dir.rename(dst)
        except OSError:
            shutil.copytree(item_dir, dst)
    except OSError as e:
        shutil.rmtree(root, ignore_errors=True)
        del store[token]
        return {"error": f"could not place {noun}: {e}"}
    finally:
        shutil.rmtree(root, ignore_errors=True)
        store.pop(token, None)

    # Keep the installed third-party code out of git (per-clone .git/info/exclude).
    exclude_line = _exclude_line_for(kind, category, item_id)
    self._add_git_exclude(exclude_line)

    descriptor = {
        **norm,
        "version": version,
        "category": category,
        "install_dir": str(dst),
        "exclude_line": exclude_line,
        "installed_at": _now_iso(),
    }
    self._record_source(item_id, descriptor, kind)
    store_state.mark_installed(self.kernel.config.data_dir, kind, item_id, version)
    await self.emit("store:installed",
                    {"kind": kind, "id": item_id, "source": norm.get("type"), "category": category})
    dest_label = f"plugins/" if kind == "plugins" else f"apps/{category}/"
    return {
        "ok": True,
        "restart_required": True,
        "id": item_id,
        "kind": kind,
        "category": category,
        "message": f"'{item_id}' installed to {dest_label}. Run restart.bat to load it.",
    }


@web_route("GET", "/api/marketplace/installed")
async def api_marketplace_installed(self: "StoreApp", request) -> dict:
    """Apps/plugins that came from the marketplace, with source provenance.

    `?kind=apps` (default) or `?kind=plugins`.
    """
    kind = request.query_params.get("kind", "apps")
    if kind not in ("apps", "plugins"):
        return {"error": f"invalid kind '{kind}'", "items": []}
    sources = self._load_sources(kind)
    reg = self.kernel.plugins if kind == "plugins" else self.kernel.apps
    installed = reg.installed_ids()
    items = []
    for aid, descr in sorted(sources.items()):
        m = reg.manifests.get(aid)
        items.append({
            "id": aid,
            "name": m.name if m else aid,
            "version": descr.get("version", ""),
            "source_type": descr.get("type", ""),
            "source_url": descr.get("url", ""),
            "ref": descr.get("ref", ""),
            "category": descr.get("category", ""),
            "installed_at": descr.get("installed_at", ""),
            "installed": aid in installed,
            "present": m is not None,
        })
    return {"items": items}


# ── Uninstall hook (called from app.py api_uninstall) ───────────────────

def _mkt_uninstall(self: "StoreApp", item_id: str, kind: str = "apps") -> bool:
    """Remove a marketplace item's folder + git-exclude line + source record.
    Returns True if it was a marketplace item (so the caller skips the park path).

    Only deletes strictly inside the kind's root (apps/ or plugins/) — never the
    root itself — guarded so it can never remove a first-party/personal item.
    """
    descr = self._load_sources(kind).get(item_id)
    if descr is None:
        return False
    root = (self._plugins_root() if kind == "plugins" else self._apps_root()).resolve()
    fallback_cat = "" if kind == "plugins" else (_norm_category(descr.get("category")) or _DEFAULT_CATEGORY)
    target = Path(descr.get("install_dir") or self._install_dest(kind, fallback_cat, item_id))
    try:
        # Containment guard: only ever rmtree strictly inside the root (and never
        # the root itself). is_relative_to handles the Windows \ vs / mismatch.
        rt = target.resolve()
        if target.is_dir() and rt != root and rt.is_relative_to(root):
            shutil.rmtree(target, ignore_errors=True)
    except (OSError, ValueError):
        pass
    if descr.get("exclude_line"):
        self._remove_git_exclude(descr["exclude_line"])
    self._drop_source(item_id, kind)
    return True


# ── small helpers ────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat(timespec="seconds")


def _stash_and_summarise(self: "StoreApp", root: Path, norm: dict, category: str, kind: str = "apps") -> dict:
    """Shared tail of preview/upload: find the item, validate, stash, summarise.

    `category` is the destination track/group folder for apps (e.g.
    extension/engineering); it falls back to the default bucket when empty/invalid
    and is ignored for plugins (they land flat at plugins/<id>/).
    """
    noun = "plugin" if kind == "plugins" else "app"
    item_dirs = self._find_app_dirs(root)
    if not item_dirs:
        shutil.rmtree(root, ignore_errors=True)
        return {"error": f"no {noun} found (need a folder with a manifest.toml)"}
    if len(item_dirs) > 1:
        names = ", ".join(sorted(p.name for p in item_dirs))
        shutil.rmtree(root, ignore_errors=True)
        return {"error": f"multiple {noun}s found ({names}) — install one at a time"}

    item_dir = item_dirs[0]
    m, err = self._validate_dir(kind, item_dir)
    if err or m is None:
        shutil.rmtree(root, ignore_errors=True)
        return {"error": err or "validation failed"}

    if kind == "plugins":
        category = ""
        summary = self._plugin_manifest_summary(m, item_dir, norm)
    else:
        category = _norm_category(category) or _DEFAULT_CATEGORY
        summary = self._manifest_summary(m, item_dir, norm, category)

    # Static risk scan (vendored SkillSpector Stage-1 — .claude/rules/skill-scan.md).
    # Plugins execute code at daemon boot (connect/auto_start), so the scan is
    # MANDATORY for plugins regardless of the dark flag; for apps it stays
    # advisory behind feature.skill-scan.enabled. Either way findings annotate
    # the review card and the user remains the gate.
    if kind == "plugins" or self.app_config("feature.skill-scan.enabled", False):
        try:
            from emptyos.sdk.skill_scan import scan_dir
            summary["scan"] = scan_dir(item_dir).to_dict()
        except Exception as e:  # noqa: BLE001 — scan failure must never block preview
            summary["scan"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    token = secrets.token_hex(12)
    self._mkt_pending_store()[token] = {
        "root": str(root),
        "app_dir": str(item_dir),
        "manifest_id": m.id,
        "version": m.version,
        "source": norm,
        "category": category,
        "kind": kind,
        "created": time.time(),
    }
    return {"ok": True, "token": token, "summary": summary}
