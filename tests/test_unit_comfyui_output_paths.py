"""Unit tests for the comfyui plugin's dated-subfolder output paths.

Kernel-free: loads plugins/comfyui/plugin.py via importlib and exercises the
pure path helpers on a stub instance (no daemon, no ComfyUI needed).

Pins the 2026-07 change that moved ComfyUI output from one flat directory
(which reached 6k files / 6.3 GiB) into ``<prefix>/<YYYY-MM>/`` subfolders.
Two failure modes are pinned here because both are silent:

1. An unrecognised date token survives verbatim into the subfolder name.
   ``%date:yyyy-MM%`` (the form in some ComfyUI docs / custom nodes) is NOT
   implemented by ``folder_paths.compute_vars`` in the shipped build, and the
   colon it contains is illegal in a Windows path — so it fails every save
   rather than degrading.
2. ``/view`` matches on basename with ``subfolder`` as a separate query param.
   Sending the joined ``subfolder/name.png`` as ``filename`` 404s, which would
   break every download the moment outputs live in subfolders.
"""
import importlib.util
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "comfyui" / "plugin.py"

#: Tokens ComfyUI's folder_paths.compute_vars actually substitutes.
SUPPORTED_TOKENS = {
    "%width%", "%height%", "%year%", "%month%",
    "%day%", "%hour%", "%minute%", "%second%",
}


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("eos_comfy_paths_test", PLUGIN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def plugin(mod):
    # skip __init__ (which wants a kernel); these helpers are pure/static.
    return mod.ComfyUIPlugin.__new__(mod.ComfyUIPlugin)


# --- _dated_prefix ------------------------------------------------------


def test_dated_prefix_nests_under_stem(plugin):
    assert plugin._dated_prefix("eos") == "eos/%year%-%month%/eos"


def test_dated_prefix_uses_only_supported_tokens(plugin):
    """The regression that would otherwise ship silently.

    Any %token% not in compute_vars survives into the folder name; %date:...%
    additionally carries a colon, which cannot exist in a Windows path.
    """
    import re

    for stem in ("eos", "eos-edit"):
        prefix = plugin._dated_prefix(stem)
        assert ":" not in prefix, f"colon in prefix is an illegal Windows path: {prefix}"
        for token in re.findall(r"%[^%]*%", prefix):
            assert token in SUPPORTED_TOKENS, f"unsupported ComfyUI token {token}"


def test_dated_prefix_basename_still_carries_stem(plugin):
    """ComfyUI derives the filename from the prefix basename, so the stem must
    survive there or generated files lose their identifying name."""
    assert plugin._dated_prefix("eos-edit").rsplit("/", 1)[-1] == "eos-edit"


# --- _item_path ---------------------------------------------------------


def test_item_path_joins_subfolder(plugin):
    item = {"filename": "eos_00001_.png", "subfolder": "eos/2026-07"}
    assert plugin._item_path(item) == "eos/2026-07/eos_00001_.png"


def test_item_path_normalises_windows_separator(plugin):
    """ComfyUI reports the subfolder with the OS separator on Windows."""
    item = {"filename": "eos_00001_.png", "subfolder": r"eos\2026-07"}
    out = plugin._item_path(item)
    assert out == "eos/2026-07/eos_00001_.png"
    assert "\\" not in out


@pytest.mark.parametrize("item,expected", [
    ({"filename": "a.png", "subfolder": ""}, "a.png"),   # legacy flat output
    ({"filename": "a.png"}, "a.png"),                    # subfolder key absent
    ({"filename": "", "subfolder": "eos/2026-07"}, ""),  # no filename
    (None, ""),                                          # poll returned nothing
])
def test_item_path_edge_cases(plugin, item, expected):
    assert plugin._item_path(item) == expected


# --- _view_params -------------------------------------------------------


def test_view_params_splits_subfolder(plugin):
    assert plugin._view_params("eos/2026-07/eos_00001_.png") == {
        "filename": "eos_00001_.png",
        "subfolder": "eos/2026-07",
    }


def test_view_params_bare_filename_unchanged(plugin):
    """Files generated before this change carry no subfolder; they must keep
    fetching exactly as they did."""
    assert plugin._view_params("eos_00001_.png") == {"filename": "eos_00001_.png"}


def test_view_params_nested_subfolder(plugin):
    assert plugin._view_params("a/b/c/x.png") == {
        "filename": "x.png",
        "subfolder": "a/b/c",
    }


# --- round trip ---------------------------------------------------------


@pytest.mark.parametrize("stem", ["eos", "eos-edit"])
def test_save_to_view_round_trip(plugin, stem):
    """A file saved under the dated prefix must resolve back to the same path
    through the string the plugin hands its callers."""
    prefix = plugin._dated_prefix(stem)
    # emulate folder_paths.compute_vars + dirname/basename split
    subfolder = prefix.rsplit("/", 1)[0].replace("%year%-%month%", "2026-07")
    saved = f"{prefix.rsplit('/', 1)[-1]}_00001_.png"

    returned = plugin._item_path(
        {"filename": saved, "subfolder": subfolder, "type": "output"}
    )
    params = plugin._view_params(returned)

    assert params["subfolder"] == f"{stem}/2026-07"
    assert params["filename"] == saved
    # /view rejects a filename that is a path or escapes the output dir
    assert "/" not in params["filename"]
    assert ".." not in returned and not returned.startswith("/")
