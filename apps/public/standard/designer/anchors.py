"""designer — element anchoring + edit-feature gating helpers.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
designer-specific wrappers over the shared `emptyos.sdk.html_anchors` parser —
stamping generated pages with `data-eos-el` anchors so the element-anchored edit
loop (editing.py) can resolve a clicked element to an exact source span, the
dark-flag gate, and the guard that keeps opaque baked-viz `<iframe srcdoc>`
blocks out of the scoped-edit path.

The actual parsing/splicing lives in the SDK (shared with the platform
pixel->source locator); this module is the thin designer-facing seam.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk.html_anchors import inject_anchors as _inject_anchors

if TYPE_CHECKING:
    from .app import DesignerApp  # noqa: F401 — for type hints only


# ─── Bind to DesignerApp class as ────────────────────────────────────
#   _edit_enabled         = _anchors._edit_enabled
#   _annotate_enabled     = _anchors._annotate_enabled
#   _maybe_anchor         = _anchors._maybe_anchor
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _edit_enabled(self) -> bool:
    """True when the element-anchored edit loop is turned on for this machine.

    Dark default (per project_feature_pipeline_flag_default_dark) — the page
    generator only stamps anchors, and the edit endpoints only act, when this
    is explicitly flipped in `[apps.designer] feature.element-edit.enabled`.
    """
    return bool(self.app_config("feature.element-edit.enabled", False))


def _annotate_enabled(self) -> bool:
    """True when the 墨刀-style annotation overlay is turned on for this machine.

    Dark default (per project_feature_pipeline_flag_default_dark) — flipped in
    `[apps.designer] feature.annotations.enabled`. Annotations pin onto the same
    `data-eos-el` anchors the element-edit loop stamps, so the live `?annotate=1`
    overlay additionally requires `_edit_enabled()` (a page generated with edit
    off carries no anchors to pin); `api_annotate` checks that dependency
    explicitly so the error message can name it.
    """
    return bool(self.app_config("feature.annotations.enabled", False))


def _maybe_anchor(self, html: str) -> str:
    """Stamp `data-eos-el` anchors on editable elements iff the edit loop is on.

    Called at the single `_persist` write choke-point so both generate and
    iterate produce anchored pages (and an iterate re-stamps — anchors are
    idempotent, so unchanged elements keep stable ids within one version).
    When the flag is off this is a no-op and saved pages are byte-identical to
    the pre-feature output.
    """
    if not self._edit_enabled():
        return html
    return _inject_anchors(html, attr="data-eos-el", prefix="e")
