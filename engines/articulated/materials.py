"""Bundled material library for PBR shading.

Each library entry is a named material — either **procedural** (no image
textures, just base color + metallic + roughness numbers) or **textured**
(image maps for base color, normal, roughness; populated in M2). The
robot-modeller's Builder LLM picks materials by name; the glTF exporter looks
the name up here to fill in PBR parameters + texture references.

M1 scope: procedural-only. `glass`, `chrome`, plus the same `Material(name=,
rgba=)` fallback path that's existed since v0.1. Textured entries land in
M2 when the curated CC0 bundle is sourced.

Lookup is name-keyed and case-insensitive at the API surface (caller writes
`PbrMaterial(name="oak")`; we normalise here). Missing names fall through
to PbrMaterial's defaults — never raises — so an LLM that hallucinates a
material name still produces a compilable model (just visually generic).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ─── Library entries ────────────────────────────────────────────────────


# `procedural=True` entries have no texture maps — just base color + PBR
# numbers. Renderers (three.js MeshStandardMaterial, Blender Principled
# BSDF) consume the same values.
#
# M2 will add `procedural=False` entries with `base_color_map`, `normal_map`,
# `roughness_map` paths into `engines/articulated/textures/<name>/`.


TEXTURE_LIBRARY: dict[str, dict[str, Any]] = {
    # ── Procedural (no texture files) ──
    "glass": {
        "description": "Clear glass — procedural translucent. Use for windows, lamp shades, vases.",
        "procedural": True,
        "base_color": [0.95, 0.97, 1.0, 0.25],
        "default_metallic": 0.0,
        "default_roughness": 0.05,
    },
    "chrome": {
        "description": "Polished chrome — bright, highly metallic, low roughness. Use for fittings, modern fixtures.",
        "procedural": True,
        "base_color": [0.90, 0.92, 0.95, 1.0],
        "default_metallic": 1.0,
        "default_roughness": 0.10,
    },

    # ── Textured (M2 placeholder solid-color images; replace with real PBR maps) ──
    # `tint` is only consumed by `scripts/generate_placeholder_textures.py`.
    # Real CC0 textures swap into `<name>/baseColor.jpg` without touching this manifest.
    # `default_roughness` + `default_metallic` are the PBR knobs that
    # carry the look (texture file adds surface detail, not material identity).

    # — Woods (matte, non-metallic, warm) — real CC0 PBR maps from ambientCG —
    "oak": {
        "description": "Warm mid-brown oak, visible grain. Furniture, floors.",
        "procedural": False,
        "base_color_map": "oak/baseColor.jpg",
        "normal_map": "oak/normal.jpg",
        "roughness_map": "oak/roughness.jpg",
        "metallic_roughness_map": "oak/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.55, 0.40, 0.25, 1.0],
    },
    "walnut": {
        "description": "Deep dark-brown walnut, fine grain. Premium furniture.",
        "procedural": False,
        "base_color_map": "walnut/baseColor.jpg",
        "normal_map": "walnut/normal.jpg",
        "roughness_map": "walnut/roughness.jpg",
        "metallic_roughness_map": "walnut/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.28, 0.18, 0.12, 1.0],
    },
    "pine": {
        "description": "Pale yellow-brown pine, knotted. Casual furniture, shelving.",
        "procedural": False,
        "base_color_map": "pine/baseColor.jpg",
        "normal_map": "pine/normal.jpg",
        "roughness_map": "pine/roughness.jpg",
        "metallic_roughness_map": "pine/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.78, 0.62, 0.40, 1.0],
    },

    # — Fabrics (matte, very rough, no metallic) —
    "linen": {
        "description": "Off-white linen weave. Mattresses, cushions, drapes.",
        "procedural": False,
        "base_color_map": "linen/baseColor.jpg",
        "normal_map": "linen/normal.jpg",
        "roughness_map": "linen/roughness.jpg",
        "metallic_roughness_map": "linen/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.84, 0.80, 0.72, 1.0],
    },
    "cotton": {
        "description": "Cream cotton, soft. Bedding, pillows.",
        "procedural": False,
        "base_color_map": "cotton/baseColor.jpg",
        "normal_map": "cotton/normal.jpg",
        "roughness_map": "cotton/roughness.jpg",
        "metallic_roughness_map": "cotton/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.90, 0.86, 0.78, 1.0],
    },
    "wool": {
        "description": "Pale grey wool, slight nap. Blankets, rugs.",
        "procedural": False,
        "base_color_map": "wool/baseColor.jpg",
        "normal_map": "wool/normal.jpg",
        "roughness_map": "wool/roughness.jpg",
        "metallic_roughness_map": "wool/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.72, 0.70, 0.66, 1.0],
    },
    "leather": {
        "description": "Dark reddish-brown leather, slight sheen. Upholstery, straps.",
        "procedural": False,
        "base_color_map": "leather/baseColor.jpg",
        "normal_map": "leather/normal.jpg",
        "roughness_map": "leather/roughness.jpg",
        "metallic_roughness_map": "leather/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.36, 0.22, 0.14, 1.0],
    },

    # — Metals (highly metallic, real maps drive the variation) —
    "brushed-steel": {
        "description": "Brushed stainless steel, medium grey, semi-rough. Appliances, fittings.",
        "procedural": False,
        "base_color_map": "brushed-steel/baseColor.jpg",
        "normal_map": "brushed-steel/normal.jpg",
        "roughness_map": "brushed-steel/roughness.jpg",
        "metallic_roughness_map": "brushed-steel/metallicRoughness.jpg",
        "default_metallic": 1.0, "default_roughness": 1.0,
        "tint": [0.72, 0.74, 0.78, 1.0],
    },
    "brass": {
        "description": "Polished brass, warm yellow-gold. Fittings, lamp bases, handles.",
        "procedural": False,
        "base_color_map": "brass/baseColor.jpg",
        "normal_map": "brass/normal.jpg",
        "roughness_map": "brass/roughness.jpg",
        "metallic_roughness_map": "brass/metallicRoughness.jpg",
        "default_metallic": 1.0, "default_roughness": 1.0,
        "tint": [0.71, 0.58, 0.26, 1.0],
    },
    "copper": {
        "description": "Polished copper, warm orange-brown. Plumbing, decorative.",
        "procedural": False,
        "base_color_map": "copper/baseColor.jpg",
        "normal_map": "copper/normal.jpg",
        "roughness_map": "copper/roughness.jpg",
        "metallic_roughness_map": "copper/metallicRoughness.jpg",
        "default_metallic": 1.0, "default_roughness": 1.0,
        "tint": [0.72, 0.45, 0.20, 1.0],
    },

    # — Finishes & paints —
    "matte-paint": {
        "description": "Matte interior paint, off-white. Walls, ceilings.",
        "procedural": False,
        "base_color_map": "matte-paint/baseColor.jpg",
        "normal_map": "matte-paint/normal.jpg",
        "roughness_map": "matte-paint/roughness.jpg",
        "metallic_roughness_map": "matte-paint/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.92, 0.90, 0.86, 1.0],
    },
    "glossy-paint": {
        "description": "Glossy interior paint, white. Skirting, trims, kitchen.",
        "procedural": False,
        "base_color_map": "glossy-paint/baseColor.jpg",
        "normal_map": "glossy-paint/normal.jpg",
        "roughness_map": "glossy-paint/roughness.jpg",
        "metallic_roughness_map": "glossy-paint/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.96, 0.95, 0.93, 1.0],
    },

    # — Stones —
    "marble": {
        "description": "Polished marble, pale, faint veining. Counters, lamp bases.",
        "procedural": False,
        "base_color_map": "marble/baseColor.jpg",
        "normal_map": "marble/normal.jpg",
        "roughness_map": "marble/roughness.jpg",
        "metallic_roughness_map": "marble/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.94, 0.92, 0.88, 1.0],
    },
    "concrete": {
        "description": "Medium grey concrete, rough. Industrial floors, modern walls.",
        "procedural": False,
        "base_color_map": "concrete/baseColor.jpg",
        "normal_map": "concrete/normal.jpg",
        "roughness_map": "concrete/roughness.jpg",
        "metallic_roughness_map": "concrete/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.62, 0.62, 0.60, 1.0],
    },

    # — Synthetic —
    "matte-plastic": {
        "description": "Matte off-white plastic. Lamp shades, household items.",
        "procedural": False,
        "base_color_map": "matte-plastic/baseColor.jpg",
        "normal_map": "matte-plastic/normal.jpg",
        "roughness_map": "matte-plastic/roughness.jpg",
        "metallic_roughness_map": "matte-plastic/metallicRoughness.jpg",
        "default_metallic": 0.0, "default_roughness": 1.0,
        "tint": [0.88, 0.86, 0.82, 1.0],
    },
}


# Convenience accessors — caller code should use these, not the raw dict.


def get_material(name: str) -> dict[str, Any] | None:
    """Look up a named material. Returns None for unknown names (callers
    use PbrMaterial's defaults). Case-insensitive."""
    if not name:
        return None
    return TEXTURE_LIBRARY.get(name.strip().lower())


def is_procedural(name: str) -> bool:
    """True if the named material has no texture maps (just PBR numbers)."""
    entry = get_material(name)
    if entry is None:
        return True  # unknown → treat as procedural with defaults
    return bool(entry.get("procedural", False))


def material_names() -> list[str]:
    """Sorted list of all library material names — for prompt + validator."""
    return sorted(TEXTURE_LIBRARY.keys())


# ─── Texture file resolution (M2+ — stubbed for M1) ─────────────────────


_TEXTURES_DIR = Path(__file__).parent / "textures"


def texture_path(name: str, map_kind: str) -> Path | None:
    """Resolve a texture file path for a textured library entry.

    `map_kind` is one of `base_color`, `normal`, `roughness`. Returns None
    when the entry is procedural, the manifest doesn't reference that map
    kind, or the file isn't present on disk. M2 ships placeholder
    `baseColor.jpg` files generated by `scripts/generate_placeholder_textures.py`;
    `normal_map` + `roughness_map` aren't referenced yet — those land when
    real PBR textures replace the placeholders.
    """
    entry = get_material(name)
    if entry is None or entry.get("procedural", True):
        return None
    rel = entry.get(f"{map_kind}_map")
    if not rel:
        return None
    full = _TEXTURES_DIR / rel
    return full if full.exists() else None


def textures_root() -> Path:
    """Absolute path to the textures directory. Used by the generator + gltf_export."""
    return _TEXTURES_DIR
