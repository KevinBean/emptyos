"""compile_source — execute LLM-written model.py source, capture the model, export URDF.

This is what the cadquery plugin (or any consumer) calls to turn a string of
Python source into a written URDF + a structured signals report.

The source string MUST define a top-level `build_object_model() -> ArticulatedObject`
function (matches the Articraft contract; the agent loop is told this in the
system prompt).

Execution is in a fresh module namespace per call. There is no sandboxing
beyond `sys.path` isolation — only run sources from trusted authors (the agent
loop or the user, never raw web input).
"""

from __future__ import annotations

import sys
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gltf_export import export_glb
from .scene_export import export_scene
from .testing import TestContext
from .types import ArticulatedObject, Scene
from .urdf_export import export_urdf


@dataclass
class CompileReport:
    """Returned by compile_source. JSON-serializable via report.to_dict()."""
    ok: bool
    stage: str                                 # parse | execute | build | baseline | export | done
    error: str | None = None                   # human-readable error message
    error_type: str | None = None              # exception class name
    traceback: str | None = None
    model_name: str | None = None
    part_count: int = 0
    joint_count: int = 0
    errors: list[dict] = field(default_factory=list)     # TestContext error findings
    warnings: list[dict] = field(default_factory=list)
    urdf_path: str | None = None
    urdf_size_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "error": self.error,
            "error_type": self.error_type,
            "traceback": self.traceback,
            "model": {
                "name": self.model_name,
                "part_count": self.part_count,
                "joint_count": self.joint_count,
            },
            "errors": self.errors,
            "warnings": self.warnings,
            "urdf": {
                "path": self.urdf_path,
                "size_bytes": self.urdf_size_bytes,
            },
        }


def compile_source(
    source: str,
    out_dir: str | Path,
    *,
    model_name: str = "model",
    texture_base_url: str | None = None,
) -> CompileReport:
    """Compile a python `source` string into a URDF written under `out_dir`.

    The source must define `build_object_model()`. Returns a CompileReport
    describing what happened at each stage. `out_dir` is created if missing.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: parse + compile to bytecode (catches SyntaxError early) ──
    try:
        code = compile(source, "<model.py>", "exec")
    except SyntaxError as exc:
        return CompileReport(
            ok=False, stage="parse",
            error=f"SyntaxError: {exc.msg} at line {exc.lineno}",
            error_type="SyntaxError",
            traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

    # ── Stage 2: execute in a fresh module namespace ──
    mod = types.ModuleType(f"_compiled_{model_name}")
    # Make `engines.articulated` accessible to the source (the LLM imports from it)
    mod.__dict__["__name__"] = mod.__name__
    try:
        exec(code, mod.__dict__)
    except Exception as exc:
        return CompileReport(
            ok=False, stage="execute",
            error=f"{type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )

    # ── Stage 3: build_object_model() must exist + be callable ──
    builder = mod.__dict__.get("build_object_model")
    if not callable(builder):
        return CompileReport(
            ok=False, stage="build",
            error="Source does not define `build_object_model() -> ArticulatedObject`",
            error_type="MissingEntryPoint",
        )

    try:
        model = builder()
    except Exception as exc:
        return CompileReport(
            ok=False, stage="build",
            error=f"build_object_model() raised: {type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )

    if not isinstance(model, ArticulatedObject):
        return CompileReport(
            ok=False, stage="build",
            error=f"build_object_model() returned {type(model).__name__}, expected ArticulatedObject",
            error_type="WrongReturnType",
        )

    # ── Stage 4: baseline test context ──
    ctx = TestContext(model)
    try:
        ctx.run_baseline()
    except Exception as exc:
        return CompileReport(
            ok=False, stage="baseline",
            error=f"baseline checks crashed: {type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
            model_name=model.name,
            part_count=len(model.parts),
            joint_count=len(model.joints),
        )

    report = ctx.report()
    if not report["ok"]:
        return CompileReport(
            ok=False, stage="baseline",
            error=f"{len(report['errors'])} baseline check failure(s)",
            error_type="BaselineFailure",
            model_name=model.name,
            part_count=report["part_count"],
            joint_count=report["joint_count"],
            errors=report["errors"],
            warnings=report["warnings"],
        )

    # ── Stage 5: URDF export ──
    urdf_path = out / "output.urdf"
    try:
        export_urdf(model, urdf_path)
    except Exception as exc:
        return CompileReport(
            ok=False, stage="export",
            error=f"URDF export raised: {type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
            model_name=model.name,
            part_count=report["part_count"],
            joint_count=report["joint_count"],
            warnings=report["warnings"],
        )

    # ── Stage 6: glTF export (Tier 3 M1) ──
    # The .glb is the visual sidecar; URDF stays for kinematics. Export
    # failure is NOT fatal — URDF still works, viewer falls back to it.
    # Surface as a warning so the LLM sees something went sideways.
    warnings_out = list(report["warnings"])
    try:
        export_glb(model, out / "output.glb", texture_base_url=texture_base_url)
    except Exception as exc:
        warnings_out.append({
            "code": "GLB_EXPORT_FAILED",
            "message": f"glb export raised: {type(exc).__name__}: {exc}",
            "where": "gltf_export",
        })

    return CompileReport(
        ok=True, stage="done",
        model_name=model.name,
        part_count=report["part_count"],
        joint_count=report["joint_count"],
        warnings=warnings_out,
        urdf_path=str(urdf_path.resolve()),
        urdf_size_bytes=urdf_path.stat().st_size,
    )


# ─── Scene compile (multi-object) ────────────────────────────────────────


@dataclass
class SceneCompileReport:
    """Returned by compile_scene_source. JSON-serializable via to_dict()."""
    ok: bool
    stage: str                                          # parse | execute | build | baseline | export | done
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    scene_name: str | None = None
    object_count: int = 0
    total_parts: int = 0
    total_joints: int = 0
    per_object: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    scene_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "error": self.error,
            "error_type": self.error_type,
            "traceback": self.traceback,
            "scene": {
                "name": self.scene_name,
                "object_count": self.object_count,
                "total_parts": self.total_parts,
                "total_joints": self.total_joints,
                "per_object": self.per_object,
            },
            "errors": self.errors,
            "warnings": self.warnings,
            "scene_path": self.scene_path,
        }


def compile_scene_source(
    source: str,
    out_dir: str | Path,
    *,
    model_name: str = "scene",
    texture_base_url: str | None = None,
) -> SceneCompileReport:
    """Compile python `source` that defines `build_scene_model() -> Scene`.

    Mirrors `compile_source` but expects the Scene entrypoint instead of
    `build_object_model`. Each object inside the scene is validated
    individually (single-root, joint refs) via the same TestContext as the
    single-object path. Stage labels match `compile_source` so the agent
    loop's signal formatter understands both reports.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: parse ──
    try:
        code = compile(source, "<scene.py>", "exec")
    except SyntaxError as exc:
        return SceneCompileReport(
            ok=False, stage="parse",
            error=f"SyntaxError: {exc.msg} at line {exc.lineno}",
            error_type="SyntaxError",
            traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

    # ── Stage 2: execute ──
    mod = types.ModuleType(f"_compiled_scene_{model_name}")
    mod.__dict__["__name__"] = mod.__name__
    try:
        exec(code, mod.__dict__)
    except Exception as exc:
        return SceneCompileReport(
            ok=False, stage="execute",
            error=f"{type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )

    # ── Stage 3: build_scene_model() must exist ──
    builder = mod.__dict__.get("build_scene_model")
    if not callable(builder):
        return SceneCompileReport(
            ok=False, stage="build",
            error="Source does not define `build_scene_model() -> Scene`",
            error_type="MissingEntryPoint",
        )

    try:
        scene = builder()
    except Exception as exc:
        return SceneCompileReport(
            ok=False, stage="build",
            error=f"build_scene_model() raised: {type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )

    if not isinstance(scene, Scene):
        return SceneCompileReport(
            ok=False, stage="build",
            error=f"build_scene_model() returned {type(scene).__name__}, expected Scene",
            error_type="WrongReturnType",
        )

    # ── Stage 4: scene-level + per-object baseline checks ──
    scene_errs = scene.validate()
    if scene_errs:
        return SceneCompileReport(
            ok=False, stage="baseline",
            error=f"{len(scene_errs)} scene-level check failure(s)",
            error_type="SceneInvariantFailure",
            scene_name=scene.name,
            object_count=len(scene.objects),
            errors=[{"code": "SceneInvariant", "message": e, "where": "scene"} for e in scene_errs],
        )

    total_parts = 0
    total_joints = 0
    per_object: list[dict] = []
    errors: list[dict] = []
    warnings: list[dict] = []
    for obj in scene.objects:
        ctx = TestContext(obj)
        try:
            ctx.run_baseline()
        except Exception as exc:
            return SceneCompileReport(
                ok=False, stage="baseline",
                error=f"baseline checks crashed on object '{obj.name}': {type(exc).__name__}: {exc}",
                error_type=type(exc).__name__,
                traceback=traceback.format_exc(),
                scene_name=scene.name,
                object_count=len(scene.objects),
            )
        rep = ctx.report()
        per_object.append({
            "name": obj.name,
            "ok": rep["ok"],
            "part_count": rep["part_count"],
            "joint_count": rep["joint_count"],
        })
        total_parts += rep["part_count"]
        total_joints += rep["joint_count"]
        # Tag each finding with the object so the LLM knows which one to fix.
        for e in rep["errors"]:
            errors.append({**e, "where": f"{obj.name}:{e.get('where', '')}".rstrip(":")})
        for w in rep["warnings"]:
            warnings.append({**w, "where": f"{obj.name}:{w.get('where', '')}".rstrip(":")})

    if errors:
        return SceneCompileReport(
            ok=False, stage="baseline",
            error=f"{len(errors)} baseline failure(s) across {len(scene.objects)} objects",
            error_type="BaselineFailure",
            scene_name=scene.name,
            object_count=len(scene.objects),
            total_parts=total_parts,
            total_joints=total_joints,
            per_object=per_object,
            errors=errors,
            warnings=warnings,
        )

    # ── Stage 5: export (per-object URDFs + scene.json) ──
    try:
        manifest = export_scene(scene, out, texture_base_url=texture_base_url)
    except Exception as exc:
        return SceneCompileReport(
            ok=False, stage="export",
            error=f"Scene export raised: {type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
            scene_name=scene.name,
            object_count=len(scene.objects),
            total_parts=total_parts,
            total_joints=total_joints,
            per_object=per_object,
            warnings=warnings,
        )

    return SceneCompileReport(
        ok=True, stage="done",
        scene_name=scene.name,
        object_count=manifest["object_count"],
        total_parts=total_parts,
        total_joints=total_joints,
        per_object=per_object,
        warnings=warnings,
        scene_path=str((out / "scene.json").resolve()),
    )


def format_scene_signals(report: SceneCompileReport) -> str:
    """Render a SceneCompileReport as a `<compile_signals>` block.

    Same wrapper tag as the single-object format so the robot-modeller's retry
    prompt builder doesn't need a scene-specific branch — the inner lines
    describe scene + per-object shape.
    """
    d = report.to_dict()
    lines = ["<compile_signals>"]
    lines.append(f"  status: {'pass' if d['ok'] else 'fail'}")
    lines.append(f"  stage: {d['stage']}")
    sc = d["scene"]
    if sc["name"]:
        lines.append(
            f"  scene: {sc['name']}  objects={sc['object_count']}  "
            f"parts={sc['total_parts']}  joints={sc['total_joints']}"
        )
    for po in sc.get("per_object", []):
        lines.append(
            f"  object[{po['name']}]: ok={po['ok']}  "
            f"parts={po['part_count']}  joints={po['joint_count']}"
        )
    if d["error"]:
        lines.append(f"  error: {d['error']}")
    for e in d["errors"]:
        where = f"  (at {e['where']})" if e.get("where") else ""
        lines.append(f"  [error] {e['code']}: {e['message']}{where}")
    for w in d["warnings"]:
        where = f"  (at {w['where']})" if w.get("where") else ""
        lines.append(f"  [warn]  {w['code']}: {w['message']}{where}")
    if d["ok"] and d.get("scene_path"):
        lines.append(f"  scene: {d['scene_path']}")
    lines.append("</compile_signals>")
    return "\n".join(lines)


def format_signals(report: CompileReport) -> str:
    """Format a CompileReport as the <compile_signals> block the LLM sees.

    Used by the agent loop's tool-result formatter. Stable shape so the LLM
    can pattern-match on it across iterations.
    """
    d = report.to_dict()
    lines = ["<compile_signals>"]
    lines.append(f"  status: {'pass' if d['ok'] else 'fail'}")
    lines.append(f"  stage: {d['stage']}")
    m = d["model"]
    if m["name"]:
        lines.append(f"  model: {m['name']}  parts={m['part_count']}  joints={m['joint_count']}")
    if d["error"]:
        lines.append(f"  error: {d['error']}")
    for e in d["errors"]:
        lines.append(f"  [error] {e['code']}: {e['message']}" + (f"  (at {e['where']})" if e.get("where") else ""))
    for w in d["warnings"]:
        lines.append(f"  [warn]  {w['code']}: {w['message']}" + (f"  (at {w['where']})" if w.get("where") else ""))
    if d["ok"]:
        lines.append(f"  urdf: {d['urdf']['path']}  ({d['urdf']['size_bytes']} bytes)")
    lines.append("</compile_signals>")
    return "\n".join(lines)
