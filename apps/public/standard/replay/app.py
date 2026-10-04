"""Replay — Record & Replay for semantic work.

Distill a finished agent session (a trace of verbs + think calls) into a
reusable, parameterized **Recipe** (vault note) and an optional
``.claude/skills/`` SKILL.md draft, then replay the recipe later as a dynamic
Pipeline where every state-changing step routes through the autopilot
eligibility floor + the rooms review gate. The EmptyOS-native analogue of
Codex's Record & Replay, over semantic ``call_app`` verbs — not screen clicks,
no Computer Use, no new capability.

Composes existing primitives: trace stitching (emptyos.sdk.trace_reader),
distill (emptyos.sdk.recipe_distill + self.think), the review gate
(self.propose_action → rooms), and (Phase 2) the pipeline runner
(emptyos.sdk.pipeline + self.runs). A recipe is an *appearance* of what a
session did, never ground truth — replay re-derives every step against current
state (three-natures lens).

Dark-default: gated behind ``replay.feature.enabled`` (Settings service first
for a live toggle, then emptyos.toml). Byte-identical to absent when off.

Spine owns lifecycle + the RecipeLibrary; helpers carry the work:
  * shared.py   — pure constants + recipe<->note round-trip + template resolver
  * library.py  — RecipeLibrary (VaultLibrary)
  * distill.py  — record + distill (this phase)
  * routes.py   — HTTP surface
  * replay.py   — the dynamic-pipeline runner (Phase 2)
"""

from __future__ import annotations

from emptyos.sdk import BaseApp

from . import distill as _distill
from . import replay as _replay
from . import routes as _routes
from . import trigger as _trigger
from .library import RecipeLibrary


class ReplayApp(BaseApp):

    # ── Distill (distill.py) ──
    distill_from_session = _distill.distill_from_session
    distill_from_trace = _distill.distill_from_trace
    _think_distill = _distill._think_distill
    _finish_distill = _distill._finish_distill
    _propose_recipe = _distill._propose_recipe
    _propose_skill = _distill._propose_skill
    _known_verbs = _distill._known_verbs
    _repo_available = _distill._repo_available

    # ── Replay runner (replay.py) ──
    dry_run = _replay.dry_run
    run = _replay.run
    resume_run = _replay.resume_run
    _build_pipeline = _replay._build_pipeline
    _make_stage_runner = _replay._make_stage_runner
    _eligibility_class = _replay._eligibility_class
    _decide_verb = _replay._decide_verb
    _bind_inputs = _replay._bind_inputs
    _load_recipe = _replay._load_recipe
    _get_pending = _replay._get_pending
    _translate = _replay._translate
    _post_run_emit = _replay._post_run_emit
    _on_action_applied = _replay._on_action_applied

    # ── HTTP surface (routes.py) ──
    api_config = _routes.api_config
    api_distill_session = _routes.api_distill_session
    api_distill_trace = _routes.api_distill_trace
    api_recipes = _routes.api_recipes
    api_recipe = _routes.api_recipe
    api_dry_run = _routes.api_dry_run
    api_run = _routes.api_run
    api_run_status = _routes.api_run_status
    api_resume = _routes.api_resume

    # ── Scheduled triggers (trigger.py) ──
    _recipe_scheduler = _trigger._recipe_scheduler
    _register_recipe_jobs = _trigger._register_recipe_jobs
    _unregister_recipe_jobs = _trigger._unregister_recipe_jobs
    _sync_recipe_job = _trigger._sync_recipe_job
    api_set_trigger = _trigger.api_set_trigger

    async def setup(self):
        await super().setup()
        self.recipes = RecipeLibrary(self)
        self._register_recipe_jobs()

    async def teardown(self):
        self._unregister_recipe_jobs()
        await super().teardown()

    def _enabled(self) -> bool:
        # Settings service first (⚙ panel toggle, live), then emptyos.toml.
        v = self.setting("replay.feature.enabled", None)
        if v is not None:
            return bool(v)
        return bool(self.app_config("feature.enabled", False))
