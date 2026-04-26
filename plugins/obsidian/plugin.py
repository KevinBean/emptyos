"""Obsidian viewer plugin — declares Obsidian as EmptyOS's vault viewer.

EmptyOS depends on an external markdown editor to open and render notes
across desktop + mobile. Obsidian is the default because it ships a URI
scheme (`obsidian://open`, `obsidian://new`) that works from any link —
exactly what `EOS.noteActions()` needs.

This plugin owns the URI templates. The web server serves them to the
frontend via `/api/health` (as `viewer.uri_templates`), and `eos.js` uses
them instead of hardcoding the scheme. To switch viewers:

- Override templates: [plugins.obsidian] uri_templates = {open = "...", new = "..."}
- Or replace with a sibling plugin that provides service = ["viewer"] instead

Templates use `{vault}` and `{path}` placeholders. `{path}` is the
vault-relative note path; `{vault}` is the vault folder name.
"""

from __future__ import annotations

import logging

from emptyos.sdk import BasePlugin

logger = logging.getLogger("obsidian")

_DEFAULT_TEMPLATES = {
    "open": "obsidian://open?vault={vault}&file={path}",
    "new": "obsidian://new?vault={vault}&file={path}",
}


class ObsidianPlugin(BasePlugin):
    name = "obsidian"

    async def connect(self):
        logger.info("Obsidian viewer registered — URI scheme: %s", self.uri_scheme())

    async def available(self) -> bool:
        # Viewer is a client-side concern (the user's device has Obsidian
        # installed or not). The plugin itself is always available as a
        # source of truth for URI templates.
        return True

    def uri_templates(self) -> dict[str, str]:
        """Return {action: template} map. Apps/frontend substitute {vault} and {path}."""
        override = self.config("uri_templates", None)
        if isinstance(override, dict) and override:
            merged = dict(_DEFAULT_TEMPLATES)
            merged.update({k: v for k, v in override.items() if isinstance(v, str)})
            return merged
        return dict(_DEFAULT_TEMPLATES)

    def uri_scheme(self) -> str:
        tmpl = self.uri_templates().get("open", "")
        return tmpl.split(":", 1)[0] if ":" in tmpl else ""
