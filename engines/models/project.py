"""Project-level settings + override-resolution helper.

The override chain (cable override → cable property → project default
→ system default) extracted from the JS Cable Reticulation Tool. Used
by `apps/cables` and any other app dealing with default→override
hierarchies.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProjectSettings(BaseModel):
    """Per-project defaults applied to all cables in the project."""

    project_id: str
    frequency_hz: float = 50.0
    soil_thermal_resistivity_kmw: float = 1.0
    ambient_temperature_c: float = 20.0
    conductor_max_temp_c: float = 90.0
    metadata: dict = Field(default_factory=dict)


SYSTEM_DEFAULTS: dict[str, Any] = {
    "frequency_hz": 50.0,
    "soil_thermal_resistivity_kmw": 1.0,
    "ambient_temperature_c": 20.0,
    "conductor_max_temp_c": 90.0,
    "burial_depth_m": 1.0,
}


def resolve_override(*layers: dict | BaseModel | None, key: str, default: Any = None) -> Any:
    """Resolve `key` through layered overrides; first non-None wins.

    Layer order: most-specific first, most-general last. Typical usage:

        resolve_override(
            cable.overrides,        # 🟠 cable override (free-form)
            cable.model_dump(),     # 🟢 cable property
            project.model_dump(),   # 🔵 project default
            SYSTEM_DEFAULTS,        # ⚪ system default
            key="ambient_temperature_c",
        )
    """
    value, _ = resolve_override_with_source(*layers, key=key, default=default)
    return value


def resolve_override_with_source(
    *layers: dict | BaseModel | None,
    key: str,
    default: Any = None,
    layer_names: tuple[str, ...] | None = None,
) -> tuple[Any, str]:
    """Like `resolve_override` but also returns the layer the value came
    from. Used by UIs that render override badges (🟠 override / 🟢 cable
    / 🔵 project / ⚪ system / · default).

    `layer_names` lets the caller name each positional layer; defaults to
    the cable-app convention `("override", "cable", "project", "system")`.
    Extra layers beyond the named tuple fall back to "layer_<index>".
    Returns `(default, "default")` when no layer carries the key.
    """
    names = layer_names or ("override", "cable", "project", "system")
    for i, layer in enumerate(layers):
        if layer is None:
            continue
        if isinstance(layer, BaseModel):
            layer = layer.model_dump()
        if not isinstance(layer, dict):
            continue
        v = layer.get(key)
        # Treat empty containers + empty strings as "not set". Vault YAML
        # round-trips bare `key:` lines as empty list, which would
        # otherwise mask a project default with a meaningless [].
        if v is None:
            continue
        if isinstance(v, (list, dict, str)) and len(v) == 0:
            continue
        source = names[i] if i < len(names) else f"layer_{i}"
        return v, source
    return default, "default"
