"""AppExporter — bundles EmptyOS apps into standalone HTML+JS directories.

An export is a first-class app lifecycle, declared in ``manifest.toml``::

    [provides.export]
    enabled = true
    mode = "standalone"
    fallbacks = ["vault:indexeddb", "think:byok-openai", "events:local-bus", ...]
    hook = "export"              # optional module with export_state / stub_routes / client_overrides

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


# Shared static files that every export needs (loaded before the app's own JS).
# eos-export-shim.js is the polyfill that makes EOS.* safe in the absence of a
# daemon; everything else matches /static/ on the live server.
SHARED_ASSETS: tuple[str, ...] = (
    "theme.css",
    "eos-components.css",
    "eos-components.js",
    "eos.js",
    "eos-keys.js",
    "eos-keys.css",
    "eos-map.js",
    "eos-map.css",
    "eos-hands-free.js",
    "eos-hands-free.css",
    "realtime.js",
    "page-assistant.js",
    "eos-export-shim.js",
)


class ExportConfigError(Exception):
    """Raised when an app has not declared ``[provides.export]`` or declares it incorrectly."""


class AppExporter:
    """Bundles an EmptyOS app into a standalone directory, zip, or single HTML file."""

    def __init__(
        self,
        app: BaseApp,
        *,
        out_dir: Path | str | None = None,
        fmt: Literal["dir", "zip", "single-html"] = "dir",
    ):
        self.app = app
        self.fmt = fmt
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
            raise ExportConfigError(
                f"App '{manifest.id}' has no pages/ directory — cannot export"
            )
        for entry in pages_dir.iterdir():
            dst = self.out_dir / entry.name
            if entry.is_dir():
                shutil.copytree(entry, dst)
            else:
                shutil.copy(entry, dst)

        # 2. Copy shared static assets into _assets/.
        assets_dir = self.out_dir / "_assets"
        assets_dir.mkdir()
        for asset in SHARED_ASSETS:
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
        body_injection = (
            f"<script>\n{components_js}\n</script>\n"
            f"<script>\n{eos_js}\n</script>\n"
        )

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
    ) -> str:
        """Rewrite asset URLs and inject the bootstrap + shim + overrides scripts.

        We inline ``state`` and ``stub_routes`` directly into the HTML so the
        bundle works from ``file://`` URLs — browsers block ``fetch()`` against
        the local filesystem. The ``_data/*.json`` files on disk are kept for
        auditability / reuse by external tooling.
        """
        # 1. Rewrite absolute /static/ → _assets/
        html = html.replace('href="/static/', 'href="_assets/').replace(
            'src="/static/', 'src="_assets/'
        )
        # 2. Rewrite the app's own /{prefix}/pages/... → sibling relative
        if app_prefix:
            html = html.replace(
                f'href="{app_prefix}/pages/', 'href="'
            ).replace(f'src="{app_prefix}/pages/', 'src="')

        # Inline snapshot + routes so fetch() doesn't need to hit the filesystem.
        state_json = json.dumps(state or {}, ensure_ascii=False, default=str)
        routes_json = json.dumps(stub_routes or {}, ensure_ascii=False, default=str)
        bootstrap = (
            "<script>\n"
            "window.EOS_IS_EXPORT = true;\n"
            f"window.EOS_APP_ID = {json.dumps(app_id)};\n"
            f"window.EOS_APP_PREFIX = {json.dumps(app_prefix)};\n"
            f"window.EOS_EXPORT_FALLBACKS = {json.dumps(fallbacks)};\n"
            f"window.EOS_EXPORT_DATA = {state_json};\n"
            f"window.EOS_EXPORT_ROUTES = {routes_json};\n"
            "</script>"
        )

        shim_tag = '<script src="_assets/eos-export-shim.js"></script>'
        overrides_tag = (
            '<script src="_data/overrides.js"></script>' if has_overrides else ""
        )

        head_inject = f"{bootstrap}\n{shim_tag}\n{overrides_tag}\n"

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

        html = re.sub(
            r'<link[^>]+href="_assets/([^"]+)"[^>]*>', _inline_link, html
        )
        html = re.sub(
            r'<script[^>]+src="_assets/([^"]+)"[^>]*></script>', _inline_script, html
        )

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

    def __init__(self, kernel, group: dict, *, out_dir: Path | str | None = None,
                 fmt: Literal["dir", "zip", "single-html"] = "dir"):
        self.kernel = kernel
        self.group = group
        self.fmt = fmt
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
                warnings.append(f"member '{app_id}' has [provides.export].enabled = false — skipped")
                continue
            inst = self.kernel.apps.instances.get(app_id) or await self.kernel.apps.load(app_id)
            loaded[app_id] = (manifest, inst)

        if not loaded:
            raise ExportConfigError(
                f"Group '{self.group.get('id')}' has no export-enabled members"
            )

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

        # 1. Copy shared static assets once.
        assets_dir = self.out_dir / "_assets"
        assets_dir.mkdir()
        for asset in SHARED_ASSETS:
            src = self.web_static_dir / asset
            if src.exists():
                shutil.copy(src, assets_dir / asset)

        # 2. Per-app: copy pages, load hook, collect state + stub_routes + overrides.
        merged_state: dict[str, Any] = {}
        merged_routes: dict[str, Any] = {}
        all_overrides: list[str] = []
        member_summaries = []
        rpc_methods: dict[str, list[str]] = {}   # app_id → [public method names]

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

            # Collect public @web_route GET/POST/... methods for RPC auto-registration.
            try:
                web_methods = inst.get_web_methods() or []
                rpc_methods[app_id] = [meta.get("path", "") for meta, _ in web_methods]
            except Exception:
                rpc_methods[app_id] = []

            # Rewrite the sub-app's index.html with the bootstrap + shim + RPC setup.
            index_file = app_sub / "index.html"
            if index_file.exists():
                html = index_file.read_text(encoding="utf-8")
                html = self._rewrite_sub_app_html(
                    html,
                    app_id=app_id,
                    app_prefix=manifest.provides.get("web", {}).get("prefix", ""),
                    fallbacks=self.group.get("fallbacks", []),
                    member_ids=sorted(member_ids),
                    state=state,
                    stub_routes=stub_routes or {},
                    has_overrides=bool(overrides),
                )
                index_file.write_text(html, encoding="utf-8")

            member_summaries.append({
                "id": app_id, "name": manifest.name, "version": manifest.version,
                "description": manifest.description,
                "prefix": manifest.provides.get("web", {}).get("prefix", ""),
            })

        # 3. Merged _data files.
        data_dir = self.out_dir / "_data"
        data_dir.mkdir()
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
        (meta_dir / "export.json").write_text(json.dumps({
            "group_id": self.group.get("id"),
            "group_name": self.group.get("name"),
            "entry": self.group.get("entry"),
            "members": [m["id"] for m in member_summaries],
            "fallbacks": self.group.get("fallbacks", []),
            "warnings": warnings,
            "built_at": datetime.now().isoformat(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

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
            f'<div class="card-desc">{m.get("description","")}</div>'
            f'</a>'
            for m in members
        )
        entry_redirect = (
            f'<script>if (location.hash === "#auto") location.replace("{entry}/index.html");</script>'
            if entry else ""
        )
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

    def _rewrite_sub_app_html(self, html, *, app_id, app_prefix, fallbacks,
                              member_ids, state, stub_routes, has_overrides):
        """Rewrite a member app's index.html to load shared assets from ../_assets/
        and bootstrap the multi-app shim."""
        # Assets live one level up.
        html = html.replace('href="/static/', 'href="../_assets/').replace(
            'src="/static/', 'src="../_assets/'
        )
        if app_prefix:
            html = html.replace(f'href="{app_prefix}/pages/', 'href="').replace(
                f'src="{app_prefix}/pages/', 'src="'
            )

        state_json = json.dumps(state or {}, ensure_ascii=False, default=str)
        routes_json = json.dumps(stub_routes or {}, ensure_ascii=False, default=str)
        members_json = json.dumps(member_ids)

        bootstrap = (
            "<script>\n"
            "window.EOS_IS_EXPORT = true;\n"
            "window.EOS_EXPORT_MODE = 'group';\n"
            f"window.EOS_APP_ID = {json.dumps(app_id)};\n"
            f"window.EOS_APP_PREFIX = {json.dumps(app_prefix)};\n"
            f"window.EOS_EXPORT_FALLBACKS = {json.dumps(fallbacks)};\n"
            f"window.EOS_BUNDLED_APPS = {members_json};\n"
            f"window.EOS_EXPORT_DATA = {state_json};\n"
            f"window.EOS_EXPORT_ROUTES = {routes_json};\n"
            "</script>"
        )
        shim_tag = '<script src="../_assets/eos-export-shim.js"></script>'
        overrides_tag = '<script src="../_data/overrides.js"></script>' if has_overrides else ""
        head_inject = f"{bootstrap}\n{shim_tag}\n{overrides_tag}\n"
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

