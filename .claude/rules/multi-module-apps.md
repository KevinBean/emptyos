# Multi-Module App Decomposition — splitting a monolith app.py

When an app's `app.py` grows past **1200 lines** (the P4 Atomic threshold checked by `apps/personal/integrity/`), split it into helper modules using this pattern. Battle-tested across `apps/dogfood-agent/` (3616L → 459L spine + 8 helpers) and `apps/rooms/` (3223L → 237L spine + 8 helpers). The `apps/projects/` layout is the older reference (extended.py / operations.py / panels.py / dev_features.py / etc.).

**Don't decompose pre-emptively.** Below ~1200L, a single file is easier to navigate. The threshold exists because at ~3000L+ the file stops fitting in working memory and changes start breaking each other.

## The pattern

Helper modules contain **module-level functions taking `self` as first arg**, with their original decorators preserved. `app.py` re-binds them as class attributes inside the class body.

```python
# apps/myapp/feature.py
from __future__ import annotations
from typing import TYPE_CHECKING
from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import MyApp  # noqa: F401 — for type hints only


# ─── Bind to MyApp class as ──────────────────────────────────────────
#   api_list   = _feature.api_list
#   _helper    = _feature._helper
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/list")
async def api_list(self, request):
    return {"items": self._helper()}


def _helper(self):
    return [...]
```

```python
# apps/myapp/app.py
from emptyos.sdk import BaseApp
from . import feature as _feature

class MyApp(BaseApp):
    async def setup(self):
        await super().setup()
        ...

    # ── Feature (extracted to feature.py) ──
    api_list = _feature.api_list
    _helper  = _feature._helper
```

`@web_route`, `@cli_command`, `@on_event`, `@staticmethod`, `@classmethod` all survive the move — the decorator stamps metadata on the function object, and re-binding to a class attribute is exactly what the class body normally does. Verified working under Python 3.10+ where `staticmethod`/`classmethod` are callable as descriptors when assigned across modules.

## Mandatory conventions (skip any and a maintainer will trip on it)

### 1. Top-of-module docstring states extraction rationale

```python
"""MyApp — <one-line responsibility, 5-10 words>.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: <2-3 sentences listing what this module is the source of
truth for + which other modules read its outputs>.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: <list, or "no cross-module reach">.
Do not import from ``.app`` (it imports us, which would cycle).
"""
```

Without this, a maintainer reading the module has no idea what its boundary is or what owns what. The `apps/projects/` modules have a thinner version of this — adopt the fuller template.

### 2. Binding banner shows every binding line verbatim

Right after the imports, list every name this module exports back to the app class:

```python
# ─── Bind to MyApp class as ──────────────────────────────────────────
#   api_foo            = _feature.api_foo
#   _internal_helper   = _feature._internal_helper
#   _static_helper     = _feature._static_helper       # @staticmethod
#   _class_helper      = _feature._class_helper        # @classmethod
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────
```

Annotate static/class methods so the maintainer sees they need descriptor handling. This banner is the single highest-value reduction of maintainer surprise — without it, a new method added here silently gets no route registration because nobody binds it in `app.py`.

### 3. `TYPE_CHECKING` guard for type hints back to the app class

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .app import MyApp  # noqa: F401 — for type hints only
```

Even if you don't use the import today, leaving the slot ready means the next person who tries to add `self: "MyApp"` annotations doesn't reintroduce a cycle.

### 4. Helpers DO NOT import from each other or from `.app`

A helper module imports from the SDK, from external libraries, and (if needed) from pure-function siblings like `behavior.py`. **Never** from another helper. **Never** from `.app`.

Cross-module method calls go through `self.X` — Python resolves them at call time against the class hierarchy, where every helper has been re-bound. The bindings make the calls work; the modules stay independent.

A helper importing another helper creates a graph that's hard to reason about and risks circular-import errors during loader spin-up.

### 5. Class-level constants travel with their consumer

If `_UI_WALK_PRESETS` is read only by `_run_ui_walk` and `_smoke_tick`, it belongs in `ui_walk.py` at module level (not `app.py`). Same for prompt constants (`CATCH_UP_SYSTEM`, `DISTILL_SYSTEM`, etc.) — they live with the method that uses them.

This avoids the bug where the constant gets left behind in `app.py` while its consumer moves to a helper, producing a `NameError` at runtime.

### 6. Name-collision rule

If a method name matches a module name (e.g. `chat` method + `chat` module), the module alias in `app.py` must be distinct from both:

```python
from . import chat as _chat_mod   # both `chat` and `_chat` exist as methods
```

The binding lines then read `chat = _chat_mod.chat`, `_chat = _chat_mod._chat`. Document the alias choice in the binding banner's header.

Other modules without collisions use the standard `from . import feature as _feature` convention.

## Gotchas the decompositions caught

These are real bugs that surfaced during dogfood-agent / rooms decomposition. Each could have hidden indefinitely without test coverage:

**Module-level constants don't move with their consumer.** If `_run_subprocess` (now in `runs.py`) references `_ALLOWED_TOOLS` as a bare module-level name, it expects to find `_ALLOWED_TOOLS` in `runs.py`'s namespace — NOT in `app.py` where the constant originally sat. After the move, the call NameError's at runtime. Always sweep the helper module for bare module-level identifiers that aren't imports and add a `defined in this module?` check.

**Latent imports.** `_gate_server_actions` calls `extract_do_tokens()`, which is imported at the top of `app.py` but invisible until the function actually runs. The extraction script needs to include every name used inside the function bodies, not just names in signatures. Sweep with: `python -m pytest <app's logic tests>` — they exercise the function bodies and surface NameError quickly.

**Test fixtures using bare `importlib.spec_from_file_location("app", path)`.** Once `app.py` adds `from . import scheduling as _scheduling`, loading it as a standalone module without registering `apps.<app>` as a package fails. Fix shape:

```python
# Register parent packages so relative imports resolve.
apps_pkg = types.ModuleType("apps"); apps_pkg.__path__ = [".../apps"]
sys.modules["apps"] = apps_pkg
rooms_pkg = types.ModuleType("apps.rooms"); rooms_pkg.__path__ = [".../apps/rooms"]
sys.modules["apps.rooms"] = rooms_pkg
# Pre-load helpers so app.py's `from . import X` succeeds.
for sub in ("agents", "chat", "participants", ...):
    spec = importlib.util.spec_from_file_location(f"apps.rooms.{sub}", ...)
    ...
```

See `tests/test_sys_rooms_logic.py` for the working reference.

**Source-grep tests pinning to `app.py`.** Tests that assert "this string is in `app.py`" break the moment the string moves to a helper. Two patterns: (a) update the grep to point at the helper that now owns the content; (b) replace the grep with a class-attribute check (`hasattr(cls, "X")`) which is layout-independent.

**Forward-reference type annotations.** `_dispatch_cli_turn(self, ...) -> AsyncIterator[dict]` works at import time because `from __future__ import annotations` stringifies all annotations — but `AsyncIterator` still needs to be in scope for `get_type_hints()` calls or future readers. Include the typing import in each helper's `from typing import ...` block.

## Tooling

`scripts/decompose_app.py` is the parameterizable extractor. Input: a JSON routes table mapping `module_name → [method_name, ...]`. Output: one helper file per module + a rewritten `app.py` spine. Generated files include the templated docstring, binding banner, and TYPE_CHECKING guard. See its docstring for usage.

The script is opinionated about the conventions in this rule file — running it produces a layout that already passes the checks above.

## When to decompose (and when not)

Decompose when:
- `app.py` is over ~1200 lines AND
- The file has 3+ distinct concerns that don't share state (e.g. CRUD vs. cron vs. UI vs. analytics)

Don't decompose when:
- The file is under 1200L — the threshold exists for a reason
- The "concerns" actually share heavy state — splitting forces every helper to reach into `self` for the same 8 attributes, which is busywork
- The file is large but cohesive (one big feature with many sub-methods) — sub-methods extracting to private helpers in the same file is the right move, not new modules

How to size the helpers:
- **Aim for 200-600 lines per helper.** Below 200L it's probably premature; above 600L the helper itself should split.
- **Group by concern, not by file position.** When `dogfood-agent`'s pre-extraction "UI walkthrough" comment band held three unrelated concerns (Playwright walks + run lifecycle + friction routing), the split followed concern lines, not source position.
- **The spine (`app.py`) lands at ~250-500L** after a good decomposition — imports, class declaration, class constants, `setup`/`teardown`, and the binding blocks.

## What stays in `app.py` (the spine)

- All imports (including the helper module imports)
- Class declaration + class-level constants that span multiple helpers
- `setup()` and `teardown()`
- 1-2 small methods that are genuinely the public spine (e.g. `chat()` if it's the app's primary entry point and the helpers are implementation detail) — judgment call

Everything else extracts.
