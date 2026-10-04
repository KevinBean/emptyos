"""scripts/mv/mv_config.py — precedence, exit-2 message, no kernel import."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "mv"))

import mv_config  # noqa: E402


def _portable(tmp: Path) -> Path:
    root = tmp / "Comfy"
    (root / "python_embeded").mkdir(parents=True)
    exe = "python.exe" if sys.platform.startswith("win") else "python"
    (root / "python_embeded" / exe).write_text("")
    (root / "ComfyUI" / "models").mkdir(parents=True)
    (root / "run_nvidia_gpu.bat").write_text("")
    return root


def test_cli_flag_beats_config(tmp_path):
    cfg = {"mv_tools": {"models_dir": str(tmp_path / "from-toml")}}
    assert mv_config.resolve("models_dir", tmp_path / "cli", cfg=cfg) == tmp_path / "cli"


def test_mv_tools_beats_derived(tmp_path):
    root = _portable(tmp_path)
    cfg = {"mv_tools": {"models_dir": str(tmp_path / "explicit")},
           "plugins": {"comfyui": {"launcher": str(root / "run_nvidia_gpu.bat")}}}
    assert mv_config.resolve("models_dir", cfg=cfg) == tmp_path / "explicit"


def test_derived_from_comfyui_launcher(tmp_path):
    root = _portable(tmp_path)
    cfg = {"plugins": {"comfyui": {"launcher": str(root / "run_nvidia_gpu.bat")}}}
    assert mv_config.resolve("comfy_root", cfg=cfg) == root
    assert mv_config.resolve("models_dir", cfg=cfg) == root / "ComfyUI" / "models"
    assert mv_config.resolve("comfy_python", cfg=cfg) == root / "python_embeded" / ("python.exe" if sys.platform.startswith("win") else "python")


def test_derived_from_explicit_comfy_root(tmp_path):
    root = _portable(tmp_path)
    cfg = {"mv_tools": {"comfy_root": str(root)}}
    assert mv_config.resolve("models_dir", cfg=cfg) == root / "ComfyUI" / "models"


def test_derived_path_missing_on_disk_is_unset(tmp_path):
    cfg = {"plugins": {"comfyui": {"launcher": str(tmp_path / "nowhere" / "run.bat")}}}
    assert mv_config.resolve("models_dir", cfg=cfg) is None


def test_nothing_configured_is_none():
    assert mv_config.resolve("comfy_python", cfg={}) is None


def test_font_explicit_path_wins(tmp_path):
    cfg = {"mv_tools": {"fonts": {"zh_sans": str(tmp_path / "x.ttc")}}}
    assert mv_config.resolve("font.zh_sans", cfg=cfg) == tmp_path / "x.ttc"


def test_unknown_font_role_is_none():
    assert mv_config.resolve("font.no_such_role", cfg={}) is None


def test_require_exits_2_naming_the_key(capsys):
    with pytest.raises(SystemExit) as exc:
        mv_config.require("models_dir", cfg={})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "[mv_tools] models_dir" in err


def test_require_font_hint_names_fonts_table(capsys):
    with pytest.raises(SystemExit):
        mv_config.require("font.no_such_role", cfg={})
    assert "[mv_tools.fonts] no_such_role" in capsys.readouterr().err


def test_import_does_not_load_kernel():
    code = ("import sys; sys.path.insert(0, r'%s'); import mv_config; "
            "print(any(m.startswith('emptyos') for m in sys.modules))"
            % (ROOT / "scripts" / "mv"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_require_explicit_path_missing_names_its_layer(tmp_path, capsys):
    cfg = {"mv_tools": {"models_dir": str(tmp_path / "gone")}}
    with pytest.raises(SystemExit) as exc:
        mv_config.require("models_dir", cfg=cfg)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "[mv_tools] models_dir" in err and "does not exist" in err


def test_require_wrong_comfy_root_names_the_root(tmp_path, capsys):
    cfg = {"mv_tools": {"comfy_root": str(tmp_path / "nope")}}
    with pytest.raises(SystemExit):
        mv_config.require("models_dir", cfg=cfg)
    assert "[mv_tools] comfy_root" in capsys.readouterr().err


def test_require_returns_existing_path(tmp_path):
    root = _portable(tmp_path)
    cfg = {"mv_tools": {"comfy_root": str(root)}}
    assert mv_config.require("models_dir", cfg=cfg) == root / "ComfyUI" / "models"


def test_toml_valid_file_is_read(tmp_path):
    (tmp_path / "emptyos.toml").write_text('[mv_tools]\nmodels_dir = "x"\n', encoding="utf-8")
    assert mv_config._toml(tmp_path)["mv_tools"]["models_dir"] == "x"


def test_toml_missing_file_is_empty(tmp_path):
    assert mv_config._toml(tmp_path) == {}


def test_toml_malformed_file_exits_2_not_silently_empty(tmp_path, capsys):
    (tmp_path / "emptyos.toml").write_text("[mv_tools\nbroken", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        mv_config._toml(tmp_path)
    assert exc.value.code == 2
    assert "not valid TOML" in capsys.readouterr().err
