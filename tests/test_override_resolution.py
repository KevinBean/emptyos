"""Override resolution + provenance — unit tests."""

from __future__ import annotations

from engines.models import resolve_override, resolve_override_with_source
from engines.models.project import SYSTEM_DEFAULTS


def test_first_layer_wins():
    assert resolve_override(
        {"x": 10}, {"x": 20}, key="x"
    ) == 10


def test_skips_none_layers_and_missing_keys():
    assert resolve_override(
        None, {}, {"x": 7}, key="x"
    ) == 7


def test_falls_back_to_default_when_unset():
    assert resolve_override({}, key="x", default=99) == 99


def test_with_source_returns_layer_name():
    v, src = resolve_override_with_source(
        {"x": 1}, {"x": 2}, {"x": 3}, SYSTEM_DEFAULTS, key="x",
    )
    assert (v, src) == (1, "override")


def test_with_source_falls_through_to_project():
    v, src = resolve_override_with_source(
        None, {}, {"ambient_temperature_c": 25.0}, SYSTEM_DEFAULTS,
        key="ambient_temperature_c",
    )
    assert (v, src) == (25.0, "project")


def test_with_source_lands_on_system_default():
    v, src = resolve_override_with_source(
        None, {}, {}, SYSTEM_DEFAULTS, key="ambient_temperature_c",
    )
    assert v == SYSTEM_DEFAULTS["ambient_temperature_c"]
    assert src == "system"


def test_with_source_returns_default_when_no_layer_carries_key():
    v, src = resolve_override_with_source(
        None, {}, {}, {}, key="totally_unknown", default=42,
    )
    assert (v, src) == (42, "default")


def test_with_source_custom_layer_names():
    v, src = resolve_override_with_source(
        None, {"x": 5}, key="x", layer_names=("a", "b"),
    )
    assert (v, src) == (5, "b")


def test_zero_is_not_treated_as_unset():
    """`0` is a real value — must not fall through to the next layer."""
    v, src = resolve_override_with_source(
        {"x": 0}, {"x": 99}, key="x",
    )
    assert (v, src) == (0, "override")
