"""AppExporter — bundles EmptyOS apps into standalone HTML+JS directories.

An export is a first-class app lifecycle, declared in ``manifest.toml``::

    [provides.export]
    enabled = true
    mode = "standalone"
    fallbacks = ["vault:indexeddb", "think:byok-openai", "events:local-bus", ...]
    hook = "export"              # optional module with export_state / stub_routes / client_overrides
    entry = "drill.html"         # optional; page promoted to index.html when the
                                 # export is a focused subset, not the whole UI

The exporter:
1. Copies the app's ``pages/`` into the output directory.
2. Copies shared static assets into ``_assets/``.
3. Injects the export shim + a bootstrap ``<script>`` that sets
   ``window.EOS_IS_EXPORT = true`` and publishes snapshot data/fallbacks.
4. Rewrites absolute asset URLs (``/static/...``) so the bundle works from
   ``file://`` or any static host.
5. Calls the app's optional ``export`` hook to snapshot state, declare
   GET-route stubs, and provide client-side override JS (e.g. POST handlers).
6. Writes ``_data/`` (state + routes + overrides) and ``_meta/export.json``
   (build metadata).
7. Optionally zips the directory.

An exported app is NOT a perfect replica — it degrades gracefully. The
``[provides.export].fallbacks`` list drives the shim's behaviour for each
capability (read, think, speak, etc.).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from emptyos.sdk.base_app import BaseApp

# Shared static files grouped by feature. ``base`` is always shipped; the other
# bundles are opt-in via ``[provides.export].assets = [...]``. When ``assets``
# is not declared, every bundle is shipped (today's default — keeps existing
# exports working without touching their manifests).
ASSET_BUNDLES: dict[str, tuple[str, ...]] = {
    "base": (
        "theme.css",
        "eos-components.css",
        "eos-components.js",
        "eos.js",
        "eos-export-shim.js",
        # Inline-handler bridge for CSP-locked hosts (MV3 extensions). Inert
        # on pages where inline handlers run natively.
        "eos-csp-bridge.js",
    ),
    "keys": ("eos-keys.js", "eos-keys.css"),
    "maps": ("eos-map.js", "eos-map.css"),
    "hands-free": ("eos-hands-free.js", "eos-hands-free.css"),
    "realtime": ("realtime.js",),
    "assistant": ("page-assistant.js",),
}

# Back-compat — anything that used SHARED_ASSETS before continues to work.
SHARED_ASSETS: tuple[str, ...] = tuple(
    name for files in ASSET_BUNDLES.values() for name in files
)


_CAPABILITY_FALLBACK_KEYS: tuple[str, ...] = (
    "think",
    "speak",
    "listen",
    "draw",
    "animate",
    "see",
    "read",
    "write",
)


def _build_capabilities_matrix(
    fallbacks: list[str],
    *,
    has_overrides: bool,
    bundled_apps: list[str] | None = None,
) -> dict[str, Any]:
    """Render an honest "what works here" matrix from the declared fallbacks.

    Status values:
      - ``"available"`` — works offline (e.g. ``vault.write`` via IndexedDB,
        ``speak`` via Web Speech API).
      - ``"byok"`` — works only if the user supplies an API key in the pill.
      - ``"disabled"`` — explicitly degraded (e.g. ``viewer:none``).
      - ``"unavailable"`` — no fallback declared. UI should hide / dim.

    Consumed by the export shim's pill panel (read from ``_meta/capabilities.json``)
    so users get a single honest view of what their bundle can do.
    """
    fb = list(fallbacks or [])

    def _has(prefix: str) -> str | None:
        for f in fb:
            if f == prefix:
                return ""
            if f.startswith(prefix + ":"):
                return f.split(":", 1)[1]
        return None

    vault_strategy = _has("vault")
    matrix: dict[str, Any] = {}
    for cap in _CAPABILITY_FALLBACK_KEYS:
        # read/write are vault-backed by default — defer to vault: when set.
        if cap in ("read", "write") and vault_strategy is not None:
            matrix[cap] = {"status": "available", "strategy": vault_strategy}
            continue
        strategy = _has(cap)
        if strategy is None:
            matrix[cap] = {"status": "unavailable", "reason": "no fallback declared"}
        elif strategy == "byok-openai":
            matrix[cap] = {"status": "byok", "strategy": strategy, "needs": "openai_key"}
        elif strategy == "none":
            matrix[cap] = {"status": "disabled", "strategy": strategy}
        elif strategy in ("web-speech-api", "web-speech-recognition"):
            matrix[cap] = {"status": "available", "strategy": strategy, "note": "browser-only"}
        elif strategy == "indexeddb":
            matrix[cap] = {"status": "available", "strategy": "indexeddb"}
        else:
            matrix[cap] = {"status": "available", "strategy": strategy or "declared"}

    # Vault is the meta-capability that backs read/write.
    matrix["vault"] = (
        {"status": "available", "strategy": vault_strategy or "indexeddb"}
        if vault_strategy is not None or _has("read") is not None or _has("write") is not None
        else {"status": "unavailable", "reason": "no vault fallback declared"}
    )

    # Viewer (Obsidian etc.) — explicit none means we degrade to copy-path.
    viewer_strategy = _has("viewer")
    matrix["viewer"] = (
        {"status": "disabled", "strategy": "copy-path"}
        if viewer_strategy == "none" or viewer_strategy is None
        else {"status": "available", "strategy": viewer_strategy}
    )

    # Events: in-page bus is always wired by the shim. The "events:local-bus"
    # fallback is just an explicit declaration users can read in the panel.
    matrix["events"] = {"status": "available", "strategy": "local-bus"}

    # Cross-app calls.
    if bundled_apps is None:
        matrix["call_app"] = {"status": "single-app", "note": "no other apps bundled"}
    else:
        matrix["call_app"] = {
            "status": "available",
            "strategy": "bundled",
            "apps": list(bundled_apps),
        }

    matrix["overrides"] = {"status": "available" if has_overrides else "auto-rpc-only"}
    return matrix


def _resolve_assets(declared: list[str] | None) -> tuple[str, ...]:
    """Resolve ``[provides.export].assets`` into a flat list of file names.

    - ``declared is None`` (no opt-in) → ship every bundle. Backwards-compatible.
    - ``declared == ["minimal"]`` → just the base bundle. Smallest viable shim.
    - Otherwise → ``base`` + each named bundle. Unknown names are ignored.
    """
    if declared is None:
        return SHARED_ASSETS
    out: list[str] = list(ASSET_BUNDLES["base"])
    for name in declared:
        n = (name or "").strip().lower()
        if n in ("base", "minimal", ""):
            continue
        out.extend(ASSET_BUNDLES.get(n, ()))
    # de-dupe while preserving order
    seen: set[str] = set()
    return tuple(x for x in out if not (x in seen or seen.add(x)))


class ExportConfigError(Exception):
    """Raised when an app has not declared ``[provides.export]`` or declares it incorrectly."""


def _rewrite_static_refs(html: str, assets_prefix: str, shipped: list[str] | None) -> str:
    """Rewrite static ``/static/X`` link/script refs to the bundle's assets dir —
    or DROP the tag when X isn't shipped (mirrors the shim's dynamic-append
    logic; a group that trims assets must not leave dangling 404 tags)."""
    import re

    def _sub(match):
        name = match.group("name")
        if shipped is not None and name not in shipped:
            return ""
        return match.group(0).replace(f'/static/{name}', f'{assets_prefix}{name}')

    html = re.sub(
        r'<link[^>]+href="/static/(?P<name>[^"]+)"[^>]*>', _sub, html
    )
    html = re.sub(
        r'<script[^>]+src="/static/(?P<name>[^"]+)"[^>]*>\s*</script>', _sub, html
    )
    # Anything left (inline-JS strings etc.) keeps the old blanket behaviour.
    return html.replace('href="/static/', f'href="{assets_prefix}').replace(
        'src="/static/', f'src="{assets_prefix}'
    )


# ─────────────────────────────────────────────────────────────────────────
# MV3 extension shell — shared by scripts/build_standalone_release.py and
# `eos app export --format extension` (.claude/rules/standalone-distribution.md).
# ─────────────────────────────────────────────────────────────────────────

_EXT_BACKGROUND_JS = """\
// {name} (Offline) — toolbar click opens the bundled app in a tab.
// Deliberately the entire service worker: no network, no content scripts,
// no host permissions. {entry_dir} is EmptyOS AppExporter output (generated
// — not hand-edited).
chrome.action.onClicked.addListener(function () {{
  chrome.tabs.create({{ url: chrome.runtime.getURL('{entry}') }});
}});
"""

_ICON_SIZES = (16, 48, 128)


def _generate_extension_icons(icons_dir: Path, letter: str) -> bool:
    """Simple generated icons (dark tile + initial). Fail-soft: returns False
    when Pillow is unavailable — load-unpacked works without icons; they only
    matter for toolbar polish and an eventual Web Store listing."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    letter = (letter or "E").strip()[:1].upper() or "E"
    icons_dir.mkdir(parents=True, exist_ok=True)
    for size in _ICON_SIZES:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        radius = max(2, size // 6)
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=(11, 16, 32, 255))
        d.rounded_rectangle(
            [0, 0, size - 1, size - 1], radius=radius, outline=(122, 108, 255, 255),
            width=max(1, size // 32),
        )
        font = None
        for candidate in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(candidate, int(size * 0.62))
                break
            except OSError:
                continue
        if font is None:
            try:
                font = ImageFont.load_default(size=int(size * 0.62))
            except TypeError:  # Pillow < 10.1
                font = ImageFont.load_default()
        bbox = d.textbbox((0, 0), letter, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), letter,
               font=font, fill=(232, 235, 247, 255))
        img.save(icons_dir / f"{size}.png")
    return True


def build_extension_shell(
    shell_dir: Path,
    *,
    name: str,
    version: str,
    description: str = "",
    entry: str = "app/index.html",
) -> None:
    """Write the MV3 shell (manifest.json + background.js + icons) around an
    already-exported bundle at ``shell_dir/<entry's first segment>/``.

    Trust story is fixed by design: ``unlimitedStorage`` ONLY — zero host
    permissions, no content scripts, no network. A standalone-target app
    must never gain host permissions (see standalone-distribution.md)."""
    shell_dir = Path(shell_dir)
    shell_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "manifest_version": 3,
        "name": f"EmptyOS {name} (Offline)",
        "version": version,
        "description": (description or f"Offline {name} — runs entirely locally.")[:130],
        "permissions": ["unlimitedStorage"],
        "background": {"service_worker": "background.js"},
        "action": {"default_title": f"Open {name}"},
    }
    if _generate_extension_icons(shell_dir / "icons", name[:1]):
        icons = {str(s): f"icons/{s}.png" for s in _ICON_SIZES}
        manifest["icons"] = icons
        manifest["action"]["default_icon"] = icons
    (shell_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    entry_dir = entry.split("/", 1)[0]
    (shell_dir / "background.js").write_text(
        _EXT_BACKGROUND_JS.format(name=name, entry=entry, entry_dir=entry_dir),
        encoding="utf-8",
    )


class AppExporter:
    """Bundles an EmptyOS app into a standalone directory, zip, or single HTML file."""

    def __init__(
        self,
        app: BaseApp,
        *,
        out_dir: Path | str | None = None,
        fmt: Literal["dir", "zip", "single-html"] = "dir",
        csp_safe: bool = False,
    ):
        self.app = app
        self.fmt = fmt
        # csp_safe: target hosts whose CSP forbids inline scripts (MV3
        # extension pages). Bootstrap goes to _data/bootstrap.js instead of an
        # inline <script>, and the eos-csp-bridge.js tag is injected so inline
        # on*="" handlers keep working under script-src 'self'.
        self.csp_safe = csp_safe
        self.out_dir = Path(out_dir) if out_dir else None
        self.web_static_dir = Path(__file__).parent.parent / "web" / "static"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build(self) -> Path:
        """Produce the export bundle and return the final path (dir or zip file)."""
        manifest = self.app.manifest
        export_cfg = manifest.provides.get("export", {}) or {}
        if not export_cfg.get("enabled", False):
            raise ExportConfigError(
                f"App '{manifest.id}' has not declared [provides.export].enabled = true"
            )

        fallbacks = list(export_cfg.get("fallbacks", []))
        mode = export_cfg.get("mode", "standalone")
        hook_module_name = export_cfg.get("hook", "export")
        # ``assets`` opt-in: list of bundle names. Absent → ship everything.
        assets = _resolve_assets(export_cfg.get("assets"))

        # Prepare output directory
        if self.out_dir is None:
            self.out_dir = Path(tempfile.mkdtemp(prefix=f"eos-export-{manifest.id}-"))
        else:
            self.out_dir = Path(self.out_dir)
            if self.out_dir.exists():
                shutil.rmtree(self.out_dir)
            self.out_dir.mkdir(parents=True)

        # 1. Copy pages/ (the app's UI) into out_dir root.
        pages_dir = manifest.path / "pages"
        if not pages_dir.exists():
            raise ExportConfigError(f"App '{manifest.id}' has no pages/ directory — cannot export")
        for entry in pages_dir.iterdir():
            dst = self.out_dir / entry.name
            if entry.is_dir():
                shutil.copytree(entry, dst)
            else:
                shutil.copy(entry, dst)

        # 1b. Promote the declared entry page to index.html.
        #
        # An app may export a focused subset rather than its whole UI — jobs
        # ships only its rehearsal drill, because its pipeline/coverage/outreach
        # surfaces are write-heavy and would export as a shell that looks
        # operable and isn't. Every downstream step (the HTML rewrite, the
        # single-file collapse, the MV3 shell) addresses index.html by name, so
        # promoting here means none of them need to know an entry was chosen.
        entry_page = str(export_cfg.get("entry") or "").strip()
        if entry_page and entry_page != "index.html":
            entry_src = self.out_dir / entry_page
            if not entry_src.is_file():
                raise ExportConfigError(
                    f"App '{manifest.id}' declares [provides.export].entry = "
                    f"'{entry_page}' but pages/{entry_page} does not exist"
                )
            shutil.copy(entry_src, self.out_dir / "index.html")

        # 2. Copy shared static assets into _assets/.
        assets_dir = self.out_dir / "_assets"
        assets_dir.mkdir()
        for asset in assets:
            src = self.web_static_dir / asset
            if src.exists():
                shutil.copy(src, assets_dir / asset)

        # 3. Invoke the optional app-side export hook.
        state, stub_routes, client_overrides = self._load_hook(manifest, hook_module_name)
        if callable(state):
            result = state(self.app)
            if hasattr(result, "__await__"):
                state = await result
            else:
                state = result
        state = state or {}
        stub_routes = stub_routes or {}

        # 4. Write _data/
        data_dir = self.out_dir / "_data"
        data_dir.mkdir()
        (data_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (data_dir / "routes.json").write_text(
            json.dumps(stub_routes, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        if client_overrides:
            (data_dir / "overrides.js").write_text(client_overrides, encoding="utf-8")

        # 5. Write _meta/export.json
        meta_dir = self.out_dir / "_meta"
        meta_dir.mkdir()
        meta = {
            "app_id": manifest.id,
            "app_name": manifest.name,
            "app_version": manifest.version,
            "description": manifest.description,
            "mode": mode,
            "fallbacks": fallbacks,
            "has_hook": bool(client_overrides or stub_routes or state),
            "built_at": datetime.now().isoformat(),
        }
        (meta_dir / "export.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        capabilities = _build_capabilities_matrix(
            fallbacks, has_overrides=bool(client_overrides), bundled_apps=None
        )
        (meta_dir / "capabilities.json").write_text(
            json.dumps(capabilities, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 6. Rewrite index.html — rewire asset URLs, inject bootstrap + shim.
        index_file = self.out_dir / "index.html"
        if index_file.exists():
            app_prefix = manifest.provides.get("web", {}).get("prefix", "")
            html = index_file.read_text(encoding="utf-8")
            html = self._rewrite_html(
                html,
                app_id=manifest.id,
                app_prefix=app_prefix,
                fallbacks=fallbacks,
                state=state,
                stub_routes=stub_routes,
                has_overrides=bool(client_overrides),
                shipped_assets=list(assets),
            )
            index_file.write_text(html, encoding="utf-8")

        # 7. Format: zip or single-html
        if self.fmt == "zip":
            zip_base = str(self.out_dir)
            archive = shutil.make_archive(zip_base, "zip", root_dir=str(self.out_dir))
            shutil.rmtree(self.out_dir)
            self.out_dir = Path(archive)
        elif self.fmt == "single-html":
            self._inline_into_single_html()

        return self.out_dir

    # ------------------------------------------------------------------
    # Back-compat shim for the older one-shot HTML bundler
    # ------------------------------------------------------------------

    def bundle_app(self, app_id: str, data: dict, template_path: Path) -> str:
        """Legacy API — returns a single HTML string with everything inlined.

        Kept for older callers (e.g. the existing `boards` export endpoint).
        New code should use ``await build()`` instead.
        """
        if not template_path.exists():
            raise FileNotFoundError(f"Template not found: {template_path}")

        html_content = template_path.read_text(encoding="utf-8")
        data_json = json.dumps(data, ensure_ascii=False, default=str)
        manifest = self.app.manifest
        export_cfg = manifest.provides.get("export", {}) or {}
        fallbacks = list(export_cfg.get("fallbacks", []))
        app_prefix = manifest.provides.get("web", {}).get("prefix", "")

        # Rewrite /static/ → inline styles+scripts, keep app prefix for shim to intercept.
        theme = self._read_static("theme.css")
        components_css = self._read_static("eos-components.css")
        components_js = self._read_static("eos-components.js")
        eos_js = self._read_static("eos.js")
        shim_js = self._read_static("eos-export-shim.js")

        bootstrap = (
            f"<script>\n"
            f"window.EOS_IS_EXPORT = true;\n"
            f"window.EOS_APP_ID = {json.dumps(app_id)};\n"
            f"window.EOS_APP_PREFIX = {json.dumps(app_prefix)};\n"
            f"window.EOS_EXPORT_FALLBACKS = {json.dumps(fallbacks)};\n"
            f"window.EOS_EXPORT_DATA = {data_json};\n"
            f"</script>"
        )

        head_injection = (
            f"<style>\n{theme}\n{components_css}\n</style>\n"
            f"{bootstrap}\n"
            f"<script>\n{shim_js}\n</script>\n"
        )
        body_injection = f"<script>\n{components_js}\n</script>\n<script>\n{eos_js}\n</script>\n"

        # Strip references to absolute /static/ files — everything is inlined.
        html_content = self._strip_static_links(html_content)

        if "</head>" in html_content:
            html_content = html_content.replace("</head>", head_injection + "</head>")
        else:
            html_content = head_injection + html_content
        if "</body>" in html_content:
            html_content = html_content.replace("</body>", body_injection + "</body>")
        else:
            html_content += body_injection

        return html_content

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read_static(self, filename: str) -> str:
        path = self.web_static_dir / filename
        return path.read_text(encoding="utf-8") if path.exists() else ""

    @staticmethod
    def _load_hook(manifest, module_name: str) -> tuple[Any, dict, str]:
        """Import ``apps/<id>/<module_name>.py`` if it exists and return its exports."""
        hook_file = manifest.path / f"{module_name}.py"
        if not hook_file.exists():
            return ({}, {}, "")
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            f"eos_apps.{manifest.id}.{module_name}", hook_file
        )
        if spec is None or spec.loader is None:
            return ({}, {}, "")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        state = getattr(module, "export_state", None)
        stub_routes_fn = getattr(module, "stub_routes", None)
        client_overrides_fn = getattr(module, "client_overrides", None)
        routes = stub_routes_fn() if callable(stub_routes_fn) else {}
        overrides = client_overrides_fn() if callable(client_overrides_fn) else ""
        return (state, routes, overrides)

    def _rewrite_html(
        self,
        html: str,
        *,
        app_id: str,
        app_prefix: str,
        fallbacks: list[str],
        state: dict,
        stub_routes: dict,
        has_overrides: bool,
        shipped_assets: list[str] | None = None,
    ) -> str:
        """Rewrite asset URLs and inject the bootstrap + shim + overrides scripts.

        We inline ``state`` and ``stub_routes`` directly into the HTML so the
        bundle works from ``file://`` URLs — browsers block ``fetch()`` against
        the local filesystem. The ``_data/*.json`` files on disk are kept for
        auditability / reuse by external tooling.
        """
        # 1. Rewrite absolute /static/ → _assets/ (dropping refs to assets the
        # bundle doesn't ship, e.g. realtime.js under a trimmed asset set).
        html = _rewrite_static_refs(html, "_assets/", shipped_assets)
        # 2. Rewrite the app's own /{prefix}/pages/... → sibling relative
        if app_prefix:
            html = html.replace(f'href="{app_prefix}/pages/', 'href="').replace(
                f'src="{app_prefix}/pages/', 'src="'
            )

        # Inline snapshot + routes so fetch() doesn't need to hit the filesystem.
        state_json = json.dumps(state or {}, ensure_ascii=False, default=str)
        routes_json = json.dumps(stub_routes or {}, ensure_ascii=False, default=str)
        capabilities_json = json.dumps(
            _build_capabilities_matrix(fallbacks, has_overrides=has_overrides, bundled_apps=None),
            ensure_ascii=False,
        )
        bootstrap_body = (
            "window.EOS_IS_EXPORT = true;\n"
            f"window.EOS_APP_ID = {json.dumps(app_id)};\n"
            f"window.EOS_APP_PREFIX = {json.dumps(app_prefix)};\n"
            f"window.EOS_EXPORT_FALLBACKS = {json.dumps(fallbacks)};\n"
            f"window.EOS_EXPORT_ASSETS = {json.dumps(shipped_assets or [])};\n"
            f"window.EOS_EXPORT_DATA = {state_json};\n"
            f"window.EOS_EXPORT_ROUTES = {routes_json};\n"
            f"window.EOS_EXPORT_CAPABILITIES = {capabilities_json};\n"
        )
        if self.csp_safe and self.out_dir is not None:
            # MV3 extension pages forbid inline scripts — externalize.
            data_dir = self.out_dir / "_data"
            data_dir.mkdir(exist_ok=True)
            (data_dir / "bootstrap.js").write_text(bootstrap_body, encoding="utf-8")
            bootstrap = '<script src="_data/bootstrap.js"></script>'
            bridge_tag = '<script src="_assets/eos-csp-bridge.js"></script>'
        else:
            bootstrap = f"<script>\n{bootstrap_body}</script>"
            bridge_tag = ""

        shim_tag = '<script src="_assets/eos-export-shim.js"></script>'
        overrides_tag = '<script src="_data/overrides.js"></script>' if has_overrides else ""

        head_inject = f"{bootstrap}\n{bridge_tag}\n{shim_tag}\n{overrides_tag}\n"

        # Insert right after <head> so the shim is visible before any other script loads.
        if "<head>" in html:
            html = html.replace("<head>", f"<head>\n{head_inject}", 1)
        else:
            html = head_inject + html

        return html

    def _strip_static_links(self, html: str) -> str:
        """For single-html mode: drop <link>/<script> tags pointing at /static/."""
        import re

        html = re.sub(
            r'<link[^>]*href="/static/[^"]*"[^>]*>\s*',
            "",
            html,
            flags=re.IGNORECASE,
        )
        html = re.sub(
            r'<script[^>]*src="/static/[^"]*"[^>]*></script>\s*',
            "",
            html,
            flags=re.IGNORECASE,
        )
        return html

    def _inline_into_single_html(self) -> None:
        """Collapse the directory into a single index.html + delete everything else."""
        index_file = self.out_dir / "index.html"
        if not index_file.exists():
            return
        html = index_file.read_text(encoding="utf-8")

        # Tell the shim there is no _assets/ on disk — dynamically-appended
        # daemon-chrome scripts (keys/i18n/tour) are dropped, not 404ed.
        html = html.replace(
            "window.EOS_IS_EXPORT = true;",
            "window.EOS_IS_EXPORT = true;\nwindow.EOS_EXPORT_SINGLE_FILE = true;",
            1,
        )

        # Inline every _assets/ stylesheet and script.
        import re

        def _safe_js(text: str) -> str:
            # Inlined JS must not contain a literal </script> sequence, which
            # would terminate the wrapping script tag. Replace it with an
            # equivalent that the browser's JS parser sees identically.
            return text.replace("</script>", "<\\/script>")

        def _inline_link(match):
            name = match.group(1)
            path = self.out_dir / "_assets" / name
            return f"<style>\n{path.read_text(encoding='utf-8')}\n</style>" if path.exists() else ""

        def _inline_script(match):
            name = match.group(1)
            path = self.out_dir / "_assets" / name
            if not path.exists():
                return ""
            return f"<script>\n{_safe_js(path.read_text(encoding='utf-8'))}\n</script>"

        html = re.sub(r'<link[^>]+href="_assets/([^"]+)"[^>]*>', _inline_link, html)
        html = re.sub(r'<script[^>]+src="_assets/([^"]+)"[^>]*></script>', _inline_script, html)

        # Inline page-local refs too (boards.js, style.css, vendor/*.js, …).
        # _rewrite_html turned /{prefix}/pages/foo.js into sibling-relative
        # "foo.js"; without this pass those tags dangle after out_dir is wiped.
        def _local_file(name: str) -> Path | None:
            if name.startswith(("http://", "https://", "//", "data:", "_data/")):
                return None
            path = (self.out_dir / name).resolve()
            try:
                path.relative_to(self.out_dir.resolve())
            except ValueError:
                return None
            return path if path.is_file() else None

        def _inline_local_link(match):
            name = match.group(1)
            path = _local_file(name)
            if path is None or path.suffix.lower() != ".css":
                return match.group(0)  # icons, absolute URLs, etc. — untouched
            return f"<style>\n{path.read_text(encoding='utf-8')}\n</style>"

        def _inline_local_script(match):
            name = match.group(1)
            if name.startswith(("http://", "https://", "//", "data:", "_data/")):
                return match.group(0)  # absolute URLs kept; _data/ handled below
            path = _local_file(name)
            if path is None:
                return ""  # missing local file → drop the dangling tag
            return f"<script>\n{_safe_js(path.read_text(encoding='utf-8'))}\n</script>"

        html = re.sub(r'<link[^>]+href="([^"]+)"[^>]*>', _inline_local_link, html)
        html = re.sub(r'<script[^>]+src="([^"]+)"[^>]*></script>', _inline_local_script, html)

        # Inline _data/state.json + routes.json into window globals.
        state_file = self.out_dir / "_data" / "state.json"
        routes_file = self.out_dir / "_data" / "routes.json"
        overrides_file = self.out_dir / "_data" / "overrides.js"
        state = state_file.read_text(encoding="utf-8") if state_file.exists() else "{}"
        routes = routes_file.read_text(encoding="utf-8") if routes_file.exists() else "{}"
        bootstrap = (
            f"<script>\n"
            f"window.EOS_EXPORT_DATA = {state};\n"
            f"window.EOS_EXPORT_ROUTES = {routes};\n"
            f"</script>"
        )
        # Replace the dynamic fetch loader with the inlined data.
        html = re.sub(
            r"<script>\s*\(async function\(\)\{[\s\S]+?\}\)\(\);\s*</script>",
            bootstrap,
            html,
        )
        if overrides_file.exists():
            html = html.replace(
                '<script src="_data/overrides.js"></script>',
                f"<script>\n{overrides_file.read_text(encoding='utf-8')}\n</script>",
            )

        # Write back and wipe sidecar dirs.
        single_path = self.out_dir.parent / f"{self.out_dir.name}.html"
        single_path.write_text(html, encoding="utf-8")
        shutil.rmtree(self.out_dir)
        self.out_dir = single_path


# ─────────────────────────────────────────────────────────────────────────
# Group export — multi-app bundles (Phase 3)
# ─────────────────────────────────────────────────────────────────────────


class GroupExporter:
    """Bundles several apps into one shell so they can call each other in the
    browser.

    Layout::

        out/
        ├── index.html                # chooser shell with EOS.nav over members
        ├── <app>/index.html          # each app under its own subdir
        ├── <app>/(page assets)
        ├── _assets/                  # shared static, copied once
        ├── _data/state.<app>.json    # snapshot per member
        ├── _data/routes.json         # merged stub_routes for the shim
        └── _meta/export.json         # group metadata + warnings

    Cross-app ``call_app()`` lands in the in-page RPC registry that the shim
    builds up at boot from each app's exported methods.
    """

    def __init__(
        self,
        kernel,
        group: dict,
        *,
        out_dir: Path | str | None = None,
        fmt: Literal["dir", "zip", "single-html"] = "dir",
        csp_safe: bool = False,
    ):
        self.kernel = kernel
        self.group = group
        self.fmt = fmt
        # csp_safe: MV3 extension hosts forbid inline scripts — per-app
        # bootstrap goes to _data/bootstrap.<app>.js, the auto-RPC wiring to
        # a shared _data/auto-rpc.js, the chooser's entry-redirect to
        # _data/chooser.js, and the eos-csp-bridge tag is injected.
        self.csp_safe = csp_safe
        self.out_dir = Path(out_dir) if out_dir else None
        self.web_static_dir = Path(__file__).parent.parent / "web" / "static"

    async def build(self) -> tuple[Path, list[str]]:
        """Build the group bundle. Returns (output_path, warnings).

        Warnings list build-time issues the user should know about: members
        with `[provides.export].enabled = false`, declared cross-app deps
        pointing at apps not in the group, etc.
        """
        members = self.group.get("apps", []) or []
        if not members:
            raise ExportConfigError(f"Group '{self.group.get('id')}' has no apps")

        warnings: list[str] = []

        # Resolve member manifests + enforce export-enabled.
        loaded = {}
        for app_id in members:
            manifest = self.kernel.apps.manifests.get(app_id)
            if not manifest:
                warnings.append(f"member '{app_id}' not found — skipped")
                continue
            exp = manifest.provides.get("export", {}) or {}
            if not exp.get("enabled"):
                warnings.append(
                    f"member '{app_id}' has [provides.export].enabled = false — skipped"
                )
                continue
            inst = self.kernel.apps.instances.get(app_id) or await self.kernel.apps.load(app_id)
            loaded[app_id] = (manifest, inst)

        if not loaded:
            raise ExportConfigError(f"Group '{self.group.get('id')}' has no export-enabled members")

        # Warn on unmet cross-app deps (declared but not in group).
        member_ids = set(loaded.keys())
        for app_id, (manifest, _) in loaded.items():
            for dep in manifest.requires.get("apps", []):
                if dep not in member_ids:
                    warnings.append(
                        f"'{app_id}' requires '{dep}' which is not in group '{self.group.get('id')}' — "
                        f"cross-app calls to it will return {{unavailable: true}}"
                    )

        # Set up output directory.
        if self.out_dir is None:
            self.out_dir = Path(tempfile.mkdtemp(prefix=f"eos-group-{self.group.get('id')}-"))
        else:
            self.out_dir = Path(self.out_dir)
            if self.out_dir.exists():
                shutil.rmtree(self.out_dir)
            self.out_dir.mkdir(parents=True)

        # 1. Copy shared static assets once. Take the union of members' declared
        # ``[provides.export].assets``; if ANY member doesn't declare it, fall
        # back to shipping everything (safe default — never strip an asset a
        # silent member might depend on). Group config can also override via
        # ``assets`` at the group level.
        group_assets_decl = self.group.get("assets")
        if group_assets_decl is not None:
            assets = _resolve_assets(list(group_assets_decl))
        else:
            declared_per_member = []
            any_undeclared = False
            for _aid, (m, _i) in loaded.items():
                d = (m.provides.get("export", {}) or {}).get("assets")
                if d is None:
                    any_undeclared = True
                    break
                declared_per_member.extend(d)
            if any_undeclared:
                assets = SHARED_ASSETS
            else:
                assets = _resolve_assets(declared_per_member)
        assets_dir = self.out_dir / "_assets"
        assets_dir.mkdir()
        for asset in assets:
            src = self.web_static_dir / asset
            if src.exists():
                shutil.copy(src, assets_dir / asset)

        # 2. Per-app: copy pages, load hook, collect state + stub_routes + overrides.
        merged_state: dict[str, Any] = {}
        merged_routes: dict[str, Any] = {}
        all_overrides: list[str] = []
        member_summaries = []
        # app_id → method_name → {method, path, prefix}
        # Used to auto-register `EOS.callApp(app, name, kwargs)` handlers that
        # round-trip through the shim's fetch interceptor (and per-app
        # registerRoute handlers from client_overrides).
        rpc_methods_map: dict[str, dict[str, dict]] = {}

        for app_id, (manifest, inst) in loaded.items():
            app_sub = self.out_dir / app_id
            app_sub.mkdir()
            pages_dir = manifest.path / "pages"
            if pages_dir.exists():
                for entry in pages_dir.iterdir():
                    dst = app_sub / entry.name
                    if entry.is_dir():
                        shutil.copytree(entry, dst)
                    else:
                        shutil.copy(entry, dst)

            # Call the app's export hook if present.
            hook_name = (manifest.provides.get("export", {}) or {}).get("hook", "export")
            state, stub_routes, overrides = AppExporter._load_hook(manifest, hook_name)
            if callable(state):
                result = state(inst)
                if hasattr(result, "__await__"):
                    state = await result
                else:
                    state = result
            state = state or {}
            merged_state[app_id] = state
            if stub_routes:
                merged_routes.update(stub_routes)
            if overrides:
                all_overrides.append(f"/* overrides for {app_id} */\n{overrides}")

            # Collect @web_route methods, keyed by python method name. The
            # registered name is what other apps pass to EOS.callApp(app, name, kwargs).
            # When a method name has both GET and POST/PATCH/DELETE bindings,
            # prefer the write verb so kwargs land in the body.
            prefix = manifest.provides.get("web", {}).get("prefix", "")
            methods: dict[str, dict] = {}
            try:
                for meta, fn in inst.get_web_methods() or []:
                    name = getattr(fn, "__name__", "") or ""
                    if not name or name.startswith("_"):
                        continue
                    verb = (meta.get("method") or "GET").upper()
                    path = meta.get("path") or ""
                    cur = methods.get(name)
                    if cur is None or (cur["method"] == "GET" and verb != "GET"):
                        methods[name] = {"method": verb, "path": path, "prefix": prefix}
            except Exception:
                pass
            rpc_methods_map[app_id] = methods

        # Now that rpc_methods_map covers every member, rewrite each sub-app's
        # HTML with the bootstrap + shim + auto-RPC registration script.
        for app_id, (manifest, inst) in loaded.items():
            member_summaries.append(
                {
                    "id": app_id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "description": manifest.description,
                    "prefix": manifest.provides.get("web", {}).get("prefix", ""),
                }
            )
            app_sub = self.out_dir / app_id
            index_file = app_sub / "index.html"
            if not index_file.exists():
                continue
            # Re-load the hook to pull per-app stub_routes / overrides for the rewriter.
            hook_name = (manifest.provides.get("export", {}) or {}).get("hook", "export")
            _, sr, ov = AppExporter._load_hook(manifest, hook_name)
            html = index_file.read_text(encoding="utf-8")
            html = self._rewrite_sub_app_html(
                html,
                app_id=app_id,
                app_prefix=manifest.provides.get("web", {}).get("prefix", ""),
                fallbacks=self.group.get("fallbacks", []),
                member_ids=sorted(member_ids),
                state=merged_state.get(app_id, {}),
                stub_routes=sr or {},
                has_overrides=bool(ov),
                rpc_methods_map=rpc_methods_map,
                shipped_assets=list(assets),
            )
            index_file.write_text(html, encoding="utf-8")

        # 3. Merged _data files. (csp_safe already created _data/ for the
        # per-app bootstrap + auto-rpc files.)
        data_dir = self.out_dir / "_data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "state.json").write_text(
            json.dumps(merged_state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (data_dir / "routes.json").write_text(
            json.dumps(merged_routes, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        if all_overrides:
            (data_dir / "overrides.js").write_text("\n\n".join(all_overrides), encoding="utf-8")

        # 4. Top-level index.html (chooser shell).
        (self.out_dir / "index.html").write_text(
            self._chooser_html(member_summaries), encoding="utf-8"
        )

        # 5. Metadata.
        meta_dir = self.out_dir / "_meta"
        meta_dir.mkdir()
        (meta_dir / "export.json").write_text(
            json.dumps(
                {
                    "group_id": self.group.get("id"),
                    "group_name": self.group.get("name"),
                    "entry": self.group.get("entry"),
                    "members": [m["id"] for m in member_summaries],
                    "fallbacks": self.group.get("fallbacks", []),
                    "warnings": warnings,
                    "built_at": datetime.now().isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        capabilities = _build_capabilities_matrix(
            self.group.get("fallbacks", []),
            has_overrides=bool(all_overrides),
            bundled_apps=[m["id"] for m in member_summaries],
        )
        (meta_dir / "capabilities.json").write_text(
            json.dumps(capabilities, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 6. Zip if asked.
        if self.fmt == "zip":
            archive = shutil.make_archive(str(self.out_dir), "zip", root_dir=str(self.out_dir))
            shutil.rmtree(self.out_dir)
            self.out_dir = Path(archive)

        return self.out_dir, warnings

    def _chooser_html(self, members: list[dict]) -> str:
        """Top-level shell — app cards that link into each bundled app."""
        group_name = self.group.get("name", "EmptyOS bundle")
        group_desc = self.group.get("description", "")
        fallbacks = json.dumps(self.group.get("fallbacks", []))
        entry = self.group.get("entry", "")
        cards = "\n".join(
            f'<a class="card" href="{m["id"]}/index.html">'
            f'<div class="card-name">{m["name"]}</div>'
            f'<div class="card-desc">{m.get("description", "")}</div>'
            f"</a>"
            for m in members
        )
        entry_redirect = ""
        if entry:
            redirect_js = f'if (location.hash === "#auto") location.replace("{entry}/index.html");\n'
            if self.csp_safe:
                data_dir = self.out_dir / "_data"
                data_dir.mkdir(exist_ok=True)
                (data_dir / "chooser.js").write_text(redirect_js, encoding="utf-8")
                entry_redirect = '<script src="_data/chooser.js"></script>'
            else:
                entry_redirect = f"<script>{redirect_js}</script>"
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{group_name}</title>
<link rel="stylesheet" href="_assets/theme.css">
<link rel="stylesheet" href="_assets/eos-components.css">
<style>
body {{ font-family: var(--font); background: var(--bg); color: var(--text); margin: 0; padding: 2rem; min-height: 100vh; }}
.wrap {{ max-width: 960px; margin: 0 auto; }}
h1 {{ color: var(--text-heading); font-size: 1.8rem; margin: 0 0 0.3rem; }}
.subtitle {{ color: var(--text-secondary); font-size: 0.95rem; margin-bottom: 2rem; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 0.8rem; }}
.card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg);
        padding: 1.2rem; text-decoration: none; color: inherit; transition: border-color 0.12s, background 0.12s; }}
.card:hover {{ border-color: var(--accent); background: var(--bg-card-hover); }}
.card-name {{ font-weight: 600; color: var(--text-heading); margin-bottom: 0.3rem; }}
.card-desc {{ font-size: 0.82rem; color: var(--text-secondary); line-height: 1.4; }}
.pill {{ position: fixed; top: 10px; right: 10px; background: var(--bg-card); color: var(--text-secondary);
        border: 1px solid var(--border); border-radius: 999px; padding: 4px 12px; font-size: 0.72rem; }}
</style></head><body>
<div class="pill">🔒 Offline bundle · {len(members)} apps</div>
<div class="wrap">
<h1>{group_name}</h1>
<p class="subtitle">{group_desc}</p>
<div class="grid">{cards}</div>
<p style="margin-top: 2rem; color: var(--text-muted); font-size: 0.78rem">
Bundled apps can call each other via the in-page RPC registry. Fallbacks: <code>{fallbacks}</code>.
</p>
</div>
{entry_redirect}
</body></html>
"""

    def _rewrite_sub_app_html(
        self,
        html,
        *,
        app_id,
        app_prefix,
        fallbacks,
        member_ids,
        state,
        stub_routes,
        has_overrides,
        rpc_methods_map=None,
        shipped_assets=None,
    ):
        """Rewrite a member app's index.html to load shared assets from ../_assets/
        and bootstrap the multi-app shim."""
        # Assets live one level up; refs to assets the group trimmed are dropped.
        html = _rewrite_static_refs(html, "../_assets/", shipped_assets)
        if app_prefix:
            html = html.replace(f'href="{app_prefix}/pages/', 'href="').replace(
                f'src="{app_prefix}/pages/', 'src="'
            )

        state_json = json.dumps(state or {}, ensure_ascii=False, default=str)
        routes_json = json.dumps(stub_routes or {}, ensure_ascii=False, default=str)
        members_json = json.dumps(member_ids)
        capabilities_json = json.dumps(
            _build_capabilities_matrix(
                fallbacks, has_overrides=has_overrides, bundled_apps=member_ids
            ),
            ensure_ascii=False,
        )

        bootstrap_body = (
            "window.EOS_IS_EXPORT = true;\n"
            "window.EOS_EXPORT_MODE = 'group';\n"
            f"window.EOS_APP_ID = {json.dumps(app_id)};\n"
            f"window.EOS_APP_PREFIX = {json.dumps(app_prefix)};\n"
            f"window.EOS_EXPORT_FALLBACKS = {json.dumps(fallbacks)};\n"
            f"window.EOS_EXPORT_ASSETS = {json.dumps(shipped_assets or [])};\n"
            f"window.EOS_BUNDLED_APPS = {members_json};\n"
            f"window.EOS_EXPORT_DATA = {state_json};\n"
            f"window.EOS_EXPORT_ROUTES = {routes_json};\n"
            f"window.EOS_EXPORT_CAPABILITIES = {capabilities_json};\n"
        )
        bridge_tag = ""
        if self.csp_safe:
            data_dir = self.out_dir / "_data"
            data_dir.mkdir(exist_ok=True)
            (data_dir / f"bootstrap.{app_id}.js").write_text(bootstrap_body, encoding="utf-8")
            bootstrap = f'<script src="../_data/bootstrap.{app_id}.js"></script>'
            bridge_tag = '<script src="../_assets/eos-csp-bridge.js"></script>'
        else:
            bootstrap = f"<script>\n{bootstrap_body}</script>"
        shim_tag = '<script src="../_assets/eos-export-shim.js"></script>'
        # Auto-RPC: walk every bundled app's @web_route methods and register
        # an EOS.callApp(app, method, kwargs) handler that round-trips through
        # the shim's fetch interceptor. Per-app client_overrides load AFTER and
        # may registerAppMethod again to replace specific bindings with native
        # IndexedDB write paths.
        rpc_map_json = json.dumps(rpc_methods_map or {})
        auto_rpc_body = (
            "(function(){\n"
            "  function _wire(){\n"
            "    if (!window.EOS_EXPORT) return setTimeout(_wire, 20);\n"
            f"    var MAP = {rpc_map_json};\n"
            "    Object.keys(MAP).forEach(function(appId){\n"
            "      var ms = MAP[appId] || {};\n"
            "      Object.keys(ms).forEach(function(name){\n"
            "        var info = ms[name];\n"
            "        window.EOS_EXPORT.registerAppMethod(appId, name, async function(kwargs){\n"
            "          var url = (info.prefix || '') + (info.path || '');\n"
            "          var init = { method: info.method, headers: {'Content-Type':'application/json'} };\n"
            "          if (info.method === 'GET' || info.method === 'HEAD') {\n"
            "            if (kwargs && Object.keys(kwargs).length){\n"
            "              var qs = new URLSearchParams();\n"
            "              Object.keys(kwargs).forEach(function(k){ if (kwargs[k]!=null) qs.set(k, kwargs[k]); });\n"
            "              var q = qs.toString(); if (q) url += '?' + q;\n"
            "            }\n"
            "          } else {\n"
            "            init.body = JSON.stringify(kwargs || {});\n"
            "          }\n"
            "          var res = await fetch(url, init);\n"
            "          try { return await res.json(); } catch(_) { return { ok: res.ok }; }\n"
            "        });\n"
            "      });\n"
            "    });\n"
            "  }\n"
            "  _wire();\n"
            "})();\n"
        )
        if self.csp_safe:
            # Identical for every member — write once, reference everywhere.
            rpc_file = self.out_dir / "_data" / "auto-rpc.js"
            if not rpc_file.exists():
                rpc_file.write_text(auto_rpc_body, encoding="utf-8")
            auto_rpc_tag = '<script src="../_data/auto-rpc.js"></script>'
        else:
            auto_rpc_tag = f"<script>\n{auto_rpc_body}</script>"
        overrides_tag = '<script src="../_data/overrides.js"></script>' if has_overrides else ""
        head_inject = f"{bootstrap}\n{bridge_tag}\n{shim_tag}\n{auto_rpc_tag}\n{overrides_tag}\n"
        if "<head>" in html:
            html = html.replace("<head>", f"<head>\n{head_inject}", 1)
        else:
            html = head_inject + html
        return html


def load_groups(path: Path | str = "export-groups.toml") -> list[dict]:
    """Parse the top-level export-groups.toml and return each group as a dict."""
    import tomllib

    p = Path(path)
    if not p.exists():
        return []
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    groups = data.get("group") or []
    if isinstance(groups, dict):
        groups = [groups]
    return groups
