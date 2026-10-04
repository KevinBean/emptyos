"""BasePlugin.config must resolve dotted keys the way TOML actually stores them.

Regression pin for the 2026-07-27 ComfyUI review: ``[plugins.comfyui]`` carried
``feature.model-residency.enabled = true``, but ``BasePlugin.config`` did a flat
``dict.get`` over the *nested* table TOML produces, so the flag read ``False`` in
code while reading ``true`` in the file. The feature reported as enabled and was
dark.

**One flag, not two.** An earlier draft of this pin also named
``feature.gpu-arbiter.enabled`` — but that one is ``false`` in the shipped
config, so it read False for the wrong reason and the right answer. The third
and last ``self.config("feature.`` site in all of ``plugins/`` is
``feature.runtime-compatibility.enabled``, which is absent from the config
entirely. Getting the count right matters: a regression pin whose stated
motivation does not match the config it claims to describe sends the next reader
looking for a second bug that was never there.

These tests execute the lookup against a real ``tomllib`` parse rather than
asserting on source text, because the defect lived precisely in the gap between
what the file says and what the accessor returns.
"""

from __future__ import annotations

import tomllib

import pytest

from emptyos.sdk.base_plugin import BasePlugin


class _Plugin(BasePlugin):
    """Minimal concrete plugin — config() needs nothing but the section dict."""

    def __init__(self, section: dict):
        self._config = section


def _section(body: str) -> dict:
    return tomllib.loads(f"[plugins.demo]\n{body}\n")["plugins"]["demo"]


DOTTED = "feature.model-residency.enabled = true"
QUOTED = '"feature.model-residency.enabled" = true'
KEY = "feature.model-residency.enabled"


def test_unquoted_dotted_key_is_nested_by_toml_and_still_resolves():
    """The shape that shipped dark: TOML nests it, config() must still find it."""
    section = _section(DOTTED)
    # Prove the premise rather than assume it — this is what a flat get misses.
    assert section == {"feature": {"model-residency": {"enabled": True}}}
    assert KEY not in section
    assert _Plugin(section).config(KEY, False) is True


def test_quoted_and_unquoted_forms_agree():
    """Parity: both ways of writing the same flag yield the same value."""
    dotted = _Plugin(_section(DOTTED)).config(KEY, False)
    quoted = _Plugin(_section(QUOTED)).config(KEY, False)
    assert dotted == quoted is True


def test_absent_flag_still_returns_the_default():
    """The negative case — a dark flag must stay dark, not become truthy."""
    assert _Plugin(_section("host = 'http://x'")).config(KEY, False) is False
    assert _Plugin({}).config("feature.nope.enabled", False) is False


def test_partial_path_does_not_resolve():
    """A prefix that exists but does not reach a leaf returns the default."""
    section = _section(DOTTED)
    assert _Plugin(section).config("feature.model-residency.missing", "d") == "d"
    assert _Plugin(section).config("feature.other.enabled", "d") == "d"


def test_flat_keys_are_unchanged():
    """Every pre-existing non-dotted key must behave exactly as before."""
    section = _section('host = "http://localhost:8188"\nautostart = true\nn = 8.0')
    p = _Plugin(section)
    assert p.config("host") == "http://localhost:8188"
    assert p.config("autostart") is True
    assert p.config("n") == 8.0
    assert p.config("absent", "fallback") == "fallback"


def test_flat_key_wins_over_a_nested_path_of_the_same_name():
    """A literal flat key is the caller's explicit intent — it takes priority."""
    section = {"a.b": "flat", "a": {"b": "nested"}}
    assert _Plugin(section).config("a.b") == "flat"


@pytest.mark.parametrize("falsy", [False, 0, "", 0.0])
def test_falsy_stored_values_are_returned_not_defaulted(falsy):
    """A stored ``false`` must turn a feature OFF, not fall through to the default."""
    section = {"feature": {"x": {"enabled": falsy}}}
    assert _Plugin(section).config("feature.x.enabled", True) == falsy


def test_non_dict_midpath_is_not_an_error():
    """Walking into a scalar returns the default instead of raising."""
    section = {"feature": "not-a-table"}
    assert _Plugin(section).config("feature.x.enabled", "d") == "d"
