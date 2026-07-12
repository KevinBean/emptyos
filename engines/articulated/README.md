# engines/articulated/

Articulated 3D object authoring SDK. Wraps CadQuery + writes URDF.

**Runs inside a Python 3.12 user-home venv**, not the EmptyOS daemon (3.13).
CadQuery 2.7.0 lacks reliable 3.13 wheels. See
`reference_userhome_python_envs_for_heavy_deps` (memory).

## Two consumers, both via subprocess

1. **`plugins/cadquery/`** (when it lands) — the EmptyOS-kernel-facing
   plugin. Shells out to `%LOCALAPPDATA%/eos/envs/cadquery-3.12/Scripts/python.exe`
   running this package's `compile.py`.
2. **`apps/personal/robot-modeller/`** — the agent loop. LLM writes Python that
   imports from this package; the plugin runs + compiles that Python.

## Public API

```python
from engines.articulated import (
    Box, Cylinder, Sphere, Mesh,        # primitives (Mesh wraps a CadQuery callable)
    Part, ArticulatedObject,            # composition
    Revolute, Fixed,                    # joints (Prismatic = v0.2)
    export_urdf,                        # ArticulatedObject -> URDF file (+ sibling .stl per Mesh)
    TestContext,                        # baseline sanity checks
)
```

## Running the smoke test

From any shell (uses the 3.12 venv, NOT the system Python):

```bash
"%LOCALAPPDATA%/eos/envs/cadquery-3.12/Scripts/python.exe" -m pytest engines/articulated/tests/ -v
```

If you get `ModuleNotFoundError: cadquery`, the venv isn't set up. See
`reference_userhome_python_envs_for_heavy_deps` for one-time setup.

## What's NOT in this engine

- LLM agent loop — lives in `apps/personal/robot-modeller/harness.py`
- URDF rendering — lives in `plugins/blender/` (URDF → PNG composition)
- Joint sliders / 3D viewer — lives in `apps/personal/robot-modeller/pages/index.html`

The engine is pure CAD: shapes in, URDF + meshes out.
