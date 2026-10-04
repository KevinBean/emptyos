"""Designer — LLM-driven styled web-page / UI builder.

Page-level sibling to `viz` (apps/public/standard/viz). Where viz makes a single
explainer artifact per call (chart, diagram, 3D scene), designer composes a whole
styled web page / UI screen and EMBEDS viz artifacts where the model leaves a
`data-viz` placeholder.

Two ideas it leans on:
  1. **Design-system priors** — a `kind: pattern` KB note (topic: ui-design,
     e.g. design-system-linear / design-system-stripe, imported from
     awesome-design-md) is injected as a "Reference design system" few-shot via
     `emptyos.sdk.pattern_examples.resolve_pattern_examples`. "Make it look like
     Linear."
  2. **viz-embedded elements** — the page LLM marks rich elements with empty
     `<div data-viz="chart" data-brief="...">` placeholders; embed.py fills each
     via the `artifact` capability and bakes the result inline as `<iframe
     srcdoc>` so the saved page stays standalone.

v1 flow (single-shot generate + whole-file iterate, no compile loop):

    POST /designer/api/generate {"prompt": "...", "style": "design-system-linear"}
      → think (LLM writes one standalone HTML page) → validate
      → embed pass (fill data-viz placeholders via self.artifact) → validate size
      → write {vault}/30_Resources/EmptyOS/designer/outputs/<id>/{page.html,record.md}
      → emit designer:created

    POST /designer/api/iterate {"id": "<id>", "prompt": "tighten the hero"}
      → whole-file rewrite via think (preserves baked iframes) → emit designer:updated

Deliberately NOT in v1 (phase 2, see plan):
  - Streaming generate + agent-based surgical iterate (mirror viz streaming.py).
  - The edit loop (inline comments + adjustment knobs) — the real Claude Design
    differentiator, via .claude/rules/proposed-action.md render-shaped flow.
  - Registering a `design` capability provider (designer consumes `artifact`).
"""

from __future__ import annotations

import logging

from emptyos.sdk import BaseApp

from . import anchors as _anchors
from . import annotations as _annotations
from . import editing as _editing
from . import embed as _embed
from . import generation as _generation
from . import routes as _routes

log = logging.getLogger("emptyos.designer")


class DesignerApp(BaseApp):

    SETTABLE_FIELDS = {"prompt", "style"}  # for boards / inline edit

    async def setup(self) -> None:
        # Kernel-invoked lifecycle hook. super().setup() wires @on_event
        # handlers + decorator-driven subscriptions; don't skip it.
        await super().setup()
        log.info("designer started")

    # ── Generation (extracted to generation.py) ──
    _outputs_root      = _generation._outputs_root
    _record_dir        = _generation._record_dir
    _rel_html          = _generation._rel_html
    _rel_record        = _generation._rel_record
    _build_style_block = _generation._build_style_block
    _build_system      = _generation._build_system
    _think_html        = _generation._think_html
    _check_size        = _generation._check_size
    _reject_reason     = _generation._reject_reason
    _persist           = _generation._persist
    generate           = _generation.generate

    # ── Viz-embed expansion (extracted to embed.py) ──
    _expand_viz_embeds = _embed._expand_viz_embeds

    # ── Element anchoring + edit gate (extracted to anchors.py) ──
    _edit_enabled     = _anchors._edit_enabled
    _annotate_enabled = _anchors._annotate_enabled
    _maybe_anchor     = _anchors._maybe_anchor

    # ── Annotation pass (extracted to annotations.py;
    #    pure menu/validation helpers in shared.py) ──
    _annotations_path   = _annotations._annotations_path
    read_annotations    = _annotations.read_annotations
    _write_annotations  = _annotations._write_annotations
    api_annotate        = _annotations.api_annotate
    api_annotations_get = _annotations.api_annotations_get

    # ── Element-anchored edit loop (extracted to editing.py;
    #    scoped-edit core lives in emptyos.sdk.html_element_edit) ──
    _edit_root       = _editing._edit_root
    _edit_think_fn   = _editing._edit_think_fn
    api_edit_propose = _editing.api_edit_propose
    api_edit_apply   = _editing.api_edit_apply
    api_edit_reject  = _editing.api_edit_reject

    # ── Routes (extracted to routes.py) ──
    _list_styles  = _routes._list_styles
    api_meta      = _routes.api_meta
    api_generate  = _routes.api_generate
    api_iterate   = _routes.api_iterate
    iterate       = _routes.iterate
    deliver_work  = _routes.deliver_work
    api_rendered  = _routes.api_rendered
    list_items    = _routes.list_items
    api_list      = _routes.api_list
    api_get       = _routes.api_get
    api_html      = _routes.api_html
    api_delete    = _routes.api_delete
    artifact_path = _routes.artifact_path
    cli_list      = _routes.cli_list
    cli_generate  = _routes.cli_generate
