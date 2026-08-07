"""Contract tests for Music Studio's deterministic Blender shot strategy."""

from __future__ import annotations

import sys

import pytest

from test_unit_music_studio_frames import APP_DIR, PKG, _load_visual

from helpers import requires_app

pytestmark = requires_app("music-studio", file="blender_experiments.py")


EXPECTED = {
    "full-3d-smoke",
    "camera-control",
    "control-passes",
    "falling-leaves",
    "water-ripples",
    "audio-reactive",
    "depth-parallax",
    "matchmove",
    "pose-guided",
    "art-directed-proof",
    "art-directed-scene-01",
    "art-directed-scene-02",
    "art-directed-scene-03",
    "art-directed-scene-04",
    "art-directed-scene-05",
    "art-directed-scene-06",
    "art-directed-scene-07",
    "art-directed-scene-08",
    "art-directed-scene-09",
}


@pytest.fixture(scope="module")
def visual():
    if not APP_DIR.is_dir():
        pytest.skip("music-studio app not installed (personal app)")
    return _load_visual()


def test_blender_strategy_is_explicit_and_normalized(visual):
    assert visual.video_strategy_for_scene({
        "video_strategy": "blender_procedural",
    }) == "blender-procedural"
    assert visual.video_strategy_for_scene({
        "video_strategy": "not-a-real-strategy",
    }) == "native-i2v"


def test_runner_declares_every_capability_reel_experiment(visual):
    runner = sys.modules[f"{PKG}.blender_experiment_runner"]
    assert runner.EXPERIMENTS == EXPECTED


def test_render_timeout_scales_with_resolution(visual):
    runner = sys.modules[f"{PKG}.blender_experiment_runner"]
    assert runner._render_timeout_seconds(4.0, 960, 540) == 180.0
    assert runner._render_timeout_seconds(4.0, 1920, 1080) == 480.0
    assert runner._render_timeout_seconds(600.0, 3840, 2160) == 3600.0


@pytest.mark.asyncio
async def test_runner_fails_closed_for_unknown_experiment(visual, tmp_path):
    runner = sys.modules[f"{PKG}.blender_experiment_runner"]
    result = await runner.render_experiment(
        "unknown",
        4.0,
        tmp_path / "clip.mp4",
        artifacts=tmp_path / "artifacts",
    )
    assert result["ok"] is False
    assert result["experiment"] == "unknown"


def test_blender_script_keeps_control_artifacts_and_pose_evidence():
    source = (APP_DIR / "blender_experiments.py").read_text(encoding="utf-8")
    for marker in (
        '"depth"',
        '"normal"',
        '"motion-vector"',
        '"object-mask"',
        '"audio-envelope.json"',
        '"matchmove-solve.json"',
        '"pose-control.json"',
        '"scene.blend"',
        '"manifest.json"',
    ):
        assert marker in source


def test_camera_motion_uses_target_constraint_without_euler_keyframe_shake():
    source = (APP_DIR / "blender_experiments.py").read_text(encoding="utf-8")
    assert 'constraints.new(type="TRACK_TO")' in source
    assert 'constraint.name = "Camera aim"' in source
    assert 'cam.keyframe_insert("rotation_euler"' not in source
    assert 'key.handle_left_type = "AUTO_CLAMPED"' in source
    assert 'key.handle_right_type = "AUTO_CLAMPED"' in source


def test_depth_parallax_fails_closed_without_reference_instead_of_camera_fallback():
    source = (APP_DIR / "blender_experiments.py").read_text(encoding="utf-8")
    start = source.index("def experiment_parallax")
    end = source.index("def experiment_matchmove", start)
    parallax_source = source[start:end]
    assert "depth-parallax requires a real reference image" in parallax_source
    assert "return experiment_camera" not in parallax_source



def test_blender_world_removes_factory_startup_objects_before_authoring():
    source = (APP_DIR / "blender_experiments.py").read_text(encoding="utf-8")
    start = source.index("def base_world")
    end = source.index("def water_and_machine", start)
    base_world_source = source[start:end]
    assert 'bpy.ops.object.select_all(action="SELECT")' in base_world_source
    assert "bpy.ops.object.delete(use_global=False)" in base_world_source

def test_depth_parallax_accepts_explicit_comfyui_depth_without_replacing_fallback():
    source = (APP_DIR / "blender_experiments.py").read_text(encoding="utf-8")
    start = source.index("def experiment_parallax")
    end = source.index("def experiment_matchmove", start)
    parallax_source = source[start:end]
    assert "explicit_depth_mesh+camera_dolly" in parallax_source
    assert "luminance_depth_mesh+camera_dolly" in parallax_source
    assert "depth_sha256" in parallax_source
    assert 'scene.view_settings.exposure = 0.0' in parallax_source
    assert 'strength.default_value = 1.0' in parallax_source
    assert 'bsdf.inputs["Base Color"]' not in parallax_source
    assert "args.depth" in source

    runner = (APP_DIR / "blender_experiment_runner.py").read_text(encoding="utf-8")
    visual = (APP_DIR / "visual.py").read_text(encoding="utf-8")
    assert '("--depth", depth)' in runner
    assert "depth=blender_depth" in visual

def test_art_directed_proof_requires_a_hash_bound_lookdev_source():
    source = (APP_DIR / "blender_experiments.py").read_text(encoding="utf-8")
    start = source.index("def experiment_art_directed_proof")
    end = source.index("EXPERIMENTS = {", start)
    proof_source = source[start:end]
    assert "approved lookdev scene script" in proof_source
    assert "lookdev_sha256" in proof_source
    assert '"visible_debug_geometry": False' in proof_source
    assert "runpy.run_path" in proof_source


def test_art_directed_proof_preserves_visible_linear_camera_motion():
    source = (APP_DIR / "blender_experiments.py").read_text(encoding="utf-8")
    assert 'args.experiment == "art-directed-proof"' in source
    assert 'key.interpolation = "LINEAR"' in source
    assert "forward * 0.68" in source


def test_art_directed_proof_handles_blender5_layered_camera_actions():
    source = (APP_DIR / "blender_experiments.py").read_text(encoding="utf-8")
    assert "strip.channelbag(slot)" in source
    assert "camera_fcurves.extend(channelbag.fcurves)" in source


def test_art_directed_scene_loader_is_hash_recorded_and_song_builder_driven():
    source = (APP_DIR / "blender_experiments.py").read_text(encoding="utf-8")
    start = source.index("def experiment_art_directed_scene")
    end = source.index("def experiment_art_directed_proof", start)
    loader = source[start:end]
    assert "production_source_sha256" in loader
    assert 'namespace.get("build_scene")' in loader
    assert 'args.experiment.startswith("art-directed-scene-")' in source

