"""Template engine — serves UI templates with app-specific config injection.

Templates are HTML files in emptyos/web/templates/. Each app can declare
a template in its manifest, and the engine injects the app's config as
window.APP_CONFIG so the template can read the right API endpoints.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

if TYPE_CHECKING:
    from emptyos.kernel.app_loader import AppManifest


TEMPLATES_DIR = Path(__file__).parent / "templates"


def _build_config(prefix: str, app_id: str, manifest: AppManifest, template_config: dict) -> dict:
    """Build the APP_CONFIG object injected into the template."""
    return {
        "app_id": app_id,
        "app_name": manifest.name,
        "description": manifest.description,
        "prefix": prefix,
        "events": manifest.events_emits,
        **template_config,
    }


def serve_template(
    server: FastAPI,
    prefix: str,
    app_id: str,
    manifest: AppManifest,
    template_name: str,
    template_config: dict,
    return_html: bool = False,
) -> str | None:
    """Mount a template page for an app. Optionally return the HTML for catch-all reuse."""
    template_path = TEMPLATES_DIR / f"{template_name}.html"
    if not template_path.exists():
        print(f"[Templates] WARNING: template '{template_name}' not found for app '{app_id}'")
        return None

    raw_html = template_path.read_text(encoding="utf-8")
    config = _build_config(prefix, app_id, manifest, template_config)
    config_json = json.dumps(config, ensure_ascii=False)

    # Inject config into the template
    page_html = raw_html.replace("APP_CONFIG_JSON", config_json)
    page_html = page_html.replace("APP_NAME", manifest.name)

    async def _template_page(html=page_html):
        return HTMLResponse(html)

    _template_page.__name__ = f"{app_id}_template_page"
    server.get(f"{prefix}/")(_template_page)

    if return_html:
        return page_html
    return None
