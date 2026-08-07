"""Viz — LLM-driven single-file HTML artifact builder.

Sibling app to `robot-modeller`. Where robot-modeller's regulation buys
structural correctness for articulated CAD (URDF + glTF via CadQuery),
viz's **absence of regulation** buys visual correctness for explanatory
artifacts. The LLM writes a complete standalone HTML file freely; the
human judges it with their eyes and iterates by prompt.

v1 flow (single-shot generate + iterate, no compile loop):

    POST /viz/api/generate {"prompt": "...", "shape": "3d-scene"}
      → think (LLM writes a single complete HTML file)
      → strip code fences, validate size
      → write {vault}/30_Resources/EmptyOS/viz/outputs/<id>/scene.html
      → write outputs/<id>/record.md (frontmatter manifest)
      → emit viz:created
      → return {id, record_dir, html_path}

    POST /viz/api/iterate {"id": "<id>", "prompt": "drop drum height to 2.3m"}
      → load current scene.html + record.md
      → think (LLM rewrites the whole file with the diff applied)
      → overwrite scene.html, bump record.updated
      → emit viz:updated

Deliberately NOT here (CLAUDE.md decision, see grill spec):
  - No SDK the LLM writes against (no engines/viz/)
  - No compile-validate-retry loop (no MAX_TURNS, no critic)
  - No room review-gate (writes are user-initiated, not agent [DO:])
  - No structural validation of the HTML (LLM's responsibility)
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, ndjson_response, web_route

from . import editing as _editing
from . import embeds as _embeds
from . import examples as _examples
from . import generation as _generation
from . import refine as _refine
from . import routes as _routes
from . import streaming as _streaming
from .shared import (
    PRESETS,
    SHAPE_META,
    VIZ_3D_SCENE_PRESET,
    VIZ_ANIM_EXPLAINER_PRESET,
    VIZ_BASE_SYSTEM,
    VIZ_CHART_PRESET,
    VIZ_ITERATE_SYSTEM,
    VIZ_MATH_PRESET,
    VIZ_MERMAID_PRESET,
    VIZ_NETWORK_PRESET,
    VIZ_SCHEMATIC_PRESET,
    VIZ_SLIDE_PRESET,
    VIZ_SVG_DIAGRAM_PRESET,
    _FENCE_RE,
    _looks_like_html,
    _looks_truncated,
    _new_id,
    _now_iso,
    _rewrite_user_msg,
    _shape_min_ability,
    _strip_fences,
)

log = logging.getLogger("emptyos.viz")


# ─── System prompts (per CLAUDE.md §Development Rules 12) ──────────
# Each shape is a system-prompt preset, NOT a Python SDK. The LLM
# writes raw HTML/JS — no constraint between intent and rendered pixels.


# ─── HTML extraction (LLM may or may not wrap in fences) ──────────


# ─── App ──────────────────────────────────────────────────────────


class VizApp(BaseApp):

    SETTABLE_FIELDS = {"prompt", "shape"}  # for boards / inline edit

    async def setup(self) -> None:
        # `setup()` is the kernel-invoked lifecycle hook (NOT `on_start`).
        # super().setup() wires @on_event handlers; without it the
        # decorator-driven event subs never fire.
        await super().setup()
        log.info("viz started")
        self._register_artifact_provider()
        self._refine_setup()

    def _register_artifact_provider(self) -> None:
        """Register viz as the local `artifact` capability provider.

        Sibling to robot-modeller's RobotModellerProvider on the `model`
        capability — same provider-chain shape, different verb. After this
        fires, any other app can call:

            html_path = await self.artifact(prompt, shape="mermaid", examples=[...])

        and the kernel routes through VizProvider.execute() → self.generate().
        Returns a vault-relative path to the artifact's scene.html, or "" on
        failure.

        Cloud providers (e.g. "render via claude.ai artifacts" backend) would
        plug in as additional providers on the same capability with priority
        further from 0.
        """
        cap = self.kernel.capabilities.get("artifact")
        if cap is None:
            return

        from emptyos.capabilities import Provider

        app = self

        class VizProvider(Provider):
            name = "viz"

            async def available(self) -> bool:
                # Viz needs the `think` capability to do anything useful.
                think_cap = app.kernel.capabilities.get("think")
                if think_cap is None:
                    return False
                # Probe whether ANY think provider is reachable; we don't
                # care which one, just that some path through the chain works.
                for prov in getattr(think_cap, "providers", []):
                    try:
                        if await prov.available():
                            return True
                    except Exception:
                        continue
                return False

            async def health(self) -> dict:
                if await self.available():
                    return {"available": True, "reason": None, "recovery": None}
                return {
                    "available": False,
                    "reason": "no think provider reachable",
                    "recovery": {
                        "kind": "capability",
                        "id": "think",
                        "hint": "Check OPENAI_API_KEY / ollama / claude-cli — viz needs at least one think provider to be available.",
                    },
                }

            async def execute(self, *, prompt: str, shape: str = "3d-scene", **kwargs) -> str:
                """Generate an artifact; return the vault-relative html_path."""
                shape = shape or app.app_config("default_shape", "3d-scene")
                examples = kwargs.get("examples") or []
                result = await app.generate(prompt, shape=shape, examples=examples)
                if not result.get("ok"):
                    return ""
                return result.get("html_path", "")

        cap.add_provider(VizProvider(), priority=0)

    # ── Examples (viz wrapper over emptyos.sdk.pattern_examples) ──
    _SHAPE_LANGS            = _examples._SHAPE_LANGS
    _build_examples_block   = _examples._build_examples_block
    api_examples            = _examples.api_examples

    # ── Generation (extracted to generation.py) ──
    _outputs_root      = _generation._outputs_root
    _record_dir        = _generation._record_dir
    _rel_html          = _generation._rel_html
    _rel_record        = _generation._rel_record
    _system_for        = _generation._system_for
    _think_html        = _generation._think_html
    _check_size        = _generation._check_size
    _reject_reason     = _generation._reject_reason
    _salvage_enabled   = _generation._salvage_enabled
    _truncation_salvage = _generation._truncation_salvage
    _persist           = _generation._persist
    _think_html_stream = _generation._think_html_stream
    generate           = _generation.generate

    # ── Element-anchored edit loop (extracted to editing.py;
    #    scoped-edit core lives in emptyos.sdk.html_element_edit) ──
    _edit_enabled        = _editing._edit_enabled
    _shape_supports_edit = _editing._shape_supports_edit
    _edit_root           = _editing._edit_root
    _edit_think_fn       = _editing._edit_think_fn
    api_edit_propose     = _editing.api_edit_propose
    api_edit_apply       = _editing.api_edit_apply
    api_edit_reject      = _editing.api_edit_reject

    # ── Routes (extracted to routes.py) ──
    api_shapes    = _routes.api_shapes
    api_generate  = _routes.api_generate
    api_iterate   = _routes.api_iterate
    iterate       = _routes.iterate
    deliver_work  = _routes.deliver_work
    deliver_work_infographic = _routes.deliver_work_infographic
    api_rendered  = _routes.api_rendered
    list_items    = _routes.list_items
    api_list      = _routes.api_list
    api_get       = _routes.api_get
    api_html      = _routes.api_html
    api_delete    = _routes.api_delete
    api_export_mp4 = _routes.api_export_mp4
    api_video      = _routes.api_video
    artifact_path = _routes.artifact_path
    _html_record_enabled   = _routes._html_record_enabled
    _shape_supports_record = _routes._shape_supports_record
    _output_review_enabled = _routes._output_review_enabled
    cli_list      = _routes.cli_list
    cli_generate  = _routes.cli_generate

    # ── Streaming (extracted to streaming.py) ──
    _multipass_enabled         = _streaming._multipass_enabled
    _has_agent_runtime         = _streaming._has_agent_runtime
    _multipass_eligible        = _streaming._multipass_eligible
    _generate_stream_events    = _streaming._generate_stream_events
    _generate_multipass_events = _streaming._generate_multipass_events
    _generate_oneshot_events   = _streaming._generate_oneshot_events
    api_generate_stream       = _streaming.api_generate_stream
    _iterate_via_think_stream = _streaming._iterate_via_think_stream

    # ── Durable note embeds (snapshot + reference; embeds.py) ──
    _embed_enabled           = _embeds._embed_enabled
    _embeds_root             = _embeds._embeds_root
    _embed_sidecar           = _embeds._embed_sidecar
    _embed_sidecar_rel       = _embeds._embed_sidecar_rel
    _resync_root             = _embeds._resync_root
    bake_embed               = _embeds.bake_embed
    _referencing_notes       = _embeds._referencing_notes
    api_embed_bake           = _embeds.api_embed_bake
    api_embed_serve          = _embeds.api_embed_serve
    api_embed_refs           = _embeds.api_embed_refs
    api_embed_resync_propose = _embeds.api_embed_resync_propose
    api_embed_resync_apply   = _embeds.api_embed_resync_apply

    # ── Auto-refine loop (graph_pipeline driver consumer #2; refine.py) ──
    _refine_setup       = _refine._refine_setup
    _refine_dispatch    = _refine._refine_dispatch
    api_refine_graph    = _refine.api_refine_graph
    api_refine_start    = _refine.api_refine_start
    api_refine_list     = _refine.api_refine_list
    api_refine_get      = _refine.api_refine_get
    api_refine_decision = _refine.api_refine_decision
    _iterate_stream_events    = _streaming._iterate_stream_events
    api_iterate_stream        = _streaming.api_iterate_stream
