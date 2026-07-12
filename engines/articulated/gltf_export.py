"""glTF (.glb) export for ArticulatedObject — the PBR visual sidecar.

Tier 3 milestone M1. Writes a binary glTF file alongside the existing URDF.
URDF still owns kinematics (joint metadata, link parent/child); glTF owns
the visual representation (tessellated geometry + PBR materials + texture
references).

Per the Tier 3 plan (`robot-modeller-tier3-gltf-pbr.md`), this exporter:

- Walks `ArticulatedObject.parts`; each Part becomes one glTF node + mesh.
- Visual origin (`Part.origin`) becomes the node's transform.
- Joint metadata is NOT written here — viewer cross-references URDF for
  joints, finds the matching glTF node by name.
- Tessellation: Box → 12 tris; Cylinder → 32 segments + caps; Sphere → 32×16.
- Material → glTF `pbrMetallicRoughness` (M1: procedural only, no texture
  refs; M2 adds texture file references).

The exporter uses `pygltflib` (pure Python, no native deps) so it works in
the cadquery-3.12 venv without extra build steps.
"""

from __future__ import annotations

import struct
from pathlib import Path

# pygltflib lives in the cadquery-3.12 venv only — the daemon-side imports
# (e.g. scripts/generate_placeholder_textures.py) shouldn't pay this cost.
# Defer the import to function-call time so this module loads in 3.13 too.
from .types import (
    ArticulatedObject,
    Box,
    Cylinder,
    Material,
    Mesh,
    PbrMaterial,
    Part,
    Sphere,
    material_to_pbr,
)

# ─── Tessellation ────────────────────────────────────────────────────────


def _box_geometry(size: tuple[float, float, float]) -> tuple[list[float], list[float], list[float], list[int]]:
    """Box mesh: 24 vertices (4 per face × 6 faces). 36 indices.

    Returns (positions, normals, uvs, indices). Per-face normals via vertex
    duplication. Per-face UVs lay out as standard 0..1 cube-unwrap squares
    so each face shows the full texture once — corner 0→(0,0), 1→(1,0),
    2→(1,1), 3→(0,1) (CCW from outside the face).
    """
    sx, sy, sz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0

    # Define 6 faces (each with 4 corners + shared normal).
    # Order: +X, -X, +Y, -Y, +Z, -Z
    faces = [
        # (corners as (x, y, z), normal)
        ([(+sx, -sy, -sz), (+sx, +sy, -sz), (+sx, +sy, +sz), (+sx, -sy, +sz)], (1, 0, 0)),
        ([(-sx, +sy, -sz), (-sx, -sy, -sz), (-sx, -sy, +sz), (-sx, +sy, +sz)], (-1, 0, 0)),
        ([(+sx, +sy, -sz), (-sx, +sy, -sz), (-sx, +sy, +sz), (+sx, +sy, +sz)], (0, 1, 0)),
        ([(-sx, -sy, -sz), (+sx, -sy, -sz), (+sx, -sy, +sz), (-sx, -sy, +sz)], (0, -1, 0)),
        ([(-sx, -sy, +sz), (+sx, -sy, +sz), (+sx, +sy, +sz), (-sx, +sy, +sz)], (0, 0, 1)),
        ([(+sx, -sy, -sz), (-sx, -sy, -sz), (-sx, +sy, -sz), (+sx, +sy, -sz)], (0, 0, -1)),
    ]
    face_uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

    positions: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    indices: list[int] = []
    for corners, normal in faces:
        base = len(positions) // 3
        for c, uv in zip(corners, face_uvs):
            positions.extend(c)
            normals.extend(normal)
            uvs.extend(uv)
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    return positions, normals, uvs, indices


def _cylinder_geometry(radius: float, length: float, segments: int = 32) -> tuple[list[float], list[float], list[float], list[int]]:
    """Cylinder along the Z axis, centred at origin. Two caps + side wall.

    Returns (positions, normals, uvs, indices). Side wall has per-vertex
    radial normals + cylindrical UVs (u wraps once around θ; v = 0 bottom
    → 1 top). Caps have flat ±Z normals + disc UVs (centre=0.5,0.5; ring
    placed on the unit circle in UV space).
    """
    import math
    h2 = length / 2.0
    positions: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    indices: list[int] = []

    side_base = 0
    for i in range(segments + 1):
        a = (i / segments) * 2.0 * math.pi
        cx, cy = math.cos(a), math.sin(a)
        u = i / segments
        positions.extend([radius * cx, radius * cy, -h2])
        normals.extend([cx, cy, 0])
        uvs.extend([u, 0.0])
        positions.extend([radius * cx, radius * cy, h2])
        normals.extend([cx, cy, 0])
        uvs.extend([u, 1.0])
    for i in range(segments):
        b = side_base + i * 2
        indices.extend([b, b + 1, b + 3, b, b + 3, b + 2])

    bot_center = len(positions) // 3
    positions.extend([0.0, 0.0, -h2])
    normals.extend([0, 0, -1])
    uvs.extend([0.5, 0.5])
    bot_ring = bot_center + 1
    for i in range(segments):
        a = (i / segments) * 2.0 * math.pi
        cx, cy = math.cos(a), math.sin(a)
        positions.extend([radius * cx, radius * cy, -h2])
        normals.extend([0, 0, -1])
        uvs.extend([0.5 + 0.5 * cx, 0.5 + 0.5 * cy])
    for i in range(segments):
        nxt = (i + 1) % segments
        indices.extend([bot_center, bot_ring + nxt, bot_ring + i])

    top_center = len(positions) // 3
    positions.extend([0.0, 0.0, h2])
    normals.extend([0, 0, 1])
    uvs.extend([0.5, 0.5])
    top_ring = top_center + 1
    for i in range(segments):
        a = (i / segments) * 2.0 * math.pi
        cx, cy = math.cos(a), math.sin(a)
        positions.extend([radius * cx, radius * cy, h2])
        normals.extend([0, 0, 1])
        uvs.extend([0.5 + 0.5 * cx, 0.5 + 0.5 * cy])
    for i in range(segments):
        nxt = (i + 1) % segments
        indices.extend([top_center, top_ring + i, top_ring + nxt])

    return positions, normals, uvs, indices


def _sphere_geometry(radius: float, lat_segments: int = 16, lon_segments: int = 32) -> tuple[list[float], list[float], list[float], list[int]]:
    """UV sphere. Smooth shading via per-vertex radial normals.

    Returns (positions, normals, uvs, indices). Standard spherical UVs:
    u = longitude / 2π (0 at +X seam → 1 at the seam again);
    v = latitude / π (0 at +Z pole → 1 at -Z pole).
    """
    import math
    positions: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    indices: list[int] = []
    for lat in range(lat_segments + 1):
        theta = (lat / lat_segments) * math.pi
        sin_t, cos_t = math.sin(theta), math.cos(theta)
        v = lat / lat_segments
        for lon in range(lon_segments + 1):
            phi = (lon / lon_segments) * 2.0 * math.pi
            sin_p, cos_p = math.sin(phi), math.cos(phi)
            x = sin_t * cos_p
            y = sin_t * sin_p
            z = cos_t
            positions.extend([radius * x, radius * y, radius * z])
            normals.extend([x, y, z])
            uvs.extend([lon / lon_segments, v])
    for lat in range(lat_segments):
        for lon in range(lon_segments):
            a = lat * (lon_segments + 1) + lon
            b = a + lon_segments + 1
            indices.extend([a, b, a + 1, b, b + 1, a + 1])
    return positions, normals, uvs, indices


def _mesh_geometry(mesh: Mesh) -> tuple[list[float], list[float], list[float], list[int]]:
    """Tessellate a Mesh's CadQuery shape with smoothed per-vertex normals
    and a planar Z-projection UV.

    Returns (positions, normals, uvs, indices). Normals: walk every triangle,
    accumulate face normals into each shared vertex slot, normalise. Curved
    surfaces (loft / sweep / fillet) render smoothly because adjacent
    triangles contribute to the same vertex. UVs: project to the XY plane
    and rescale into [0,1] via the shape's xy bounding box. Works well for
    horizontal surfaces (floors, tabletops); stretches on vertical surfaces
    but is deterministic and always non-zero — better than the previous
    no-TEXCOORD path which collapsed every texture to a single pixel.
    """
    import math
    shape = mesh.get_shape()
    # Resolve Workplane to Shape if needed — CadQuery's `.tessellate()` lives
    # on Shape; Workplane proxies via `.val()`.
    if hasattr(shape, "val") and not hasattr(shape, "tessellate"):
        shape = shape.val()
    vertices, triangles = shape.tessellate(mesh.tolerance)

    positions: list[float] = []
    for v in vertices:
        positions.extend([float(v.x), float(v.y), float(v.z)])

    n = len(vertices)
    accum = [[0.0, 0.0, 0.0] for _ in range(n)]
    flat_indices: list[int] = []
    for tri in triangles:
        i0, i1, i2 = int(tri[0]), int(tri[1]), int(tri[2])
        flat_indices.extend([i0, i1, i2])
        v0, v1, v2 = vertices[i0], vertices[i1], vertices[i2]
        ax = float(v1.x) - float(v0.x); ay = float(v1.y) - float(v0.y); az = float(v1.z) - float(v0.z)
        bx = float(v2.x) - float(v0.x); by = float(v2.y) - float(v0.y); bz = float(v2.z) - float(v0.z)
        nx = ay * bz - az * by
        ny = az * bx - ax * bz
        nz = ax * by - ay * bx
        for idx in (i0, i1, i2):
            accum[idx][0] += nx
            accum[idx][1] += ny
            accum[idx][2] += nz

    normals: list[float] = []
    for nx, ny, nz in accum:
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln > 0:
            normals.extend([nx / ln, ny / ln, nz / ln])
        else:
            normals.extend([0.0, 0.0, 1.0])

    # Planar Z UVs. Compute XY bbox; rescale each vertex into [0,1].
    # Degenerate axes (a flat shape along Y, say) collapse to constant 0.5
    # for that axis — the texture shows once, undistorted on the other axis.
    if vertices:
        min_x = min(float(v.x) for v in vertices)
        max_x = max(float(v.x) for v in vertices)
        min_y = min(float(v.y) for v in vertices)
        max_y = max(float(v.y) for v in vertices)
    else:
        min_x = max_x = min_y = max_y = 0.0
    span_x = max_x - min_x
    span_y = max_y - min_y
    uvs: list[float] = []
    for v in vertices:
        u = (float(v.x) - min_x) / span_x if span_x > 1e-9 else 0.5
        w = (float(v.y) - min_y) / span_y if span_y > 1e-9 else 0.5
        uvs.extend([u, w])

    return positions, normals, uvs, flat_indices


def _tessellate_geometry(geom) -> tuple[list[float], list[float], list[float], list[int]]:
    """Dispatch on geometry type. Returns (positions, normals, uvs, indices)."""
    if isinstance(geom, Box):
        return _box_geometry(geom.size)
    if isinstance(geom, Cylinder):
        return _cylinder_geometry(geom.radius, geom.length)
    if isinstance(geom, Sphere):
        return _sphere_geometry(geom.radius)
    if isinstance(geom, Mesh):
        return _mesh_geometry(geom)
    raise ValueError(f"Unknown geometry type: {type(geom).__name__}")


# ─── Buffer packing ──────────────────────────────────────────────────────


def _pack_floats(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def _pack_uints(values: list[int]) -> bytes:
    return struct.pack(f"<{len(values)}I", *values)


def _bbox(values: list[float], stride: int) -> tuple[list[float], list[float]]:
    """Min/max per axis across a flat float array with given stride."""
    if not values:
        return [0.0] * stride, [0.0] * stride
    mins = list(values[:stride])
    maxs = list(values[:stride])
    for i in range(0, len(values), stride):
        for j in range(stride):
            v = values[i + j]
            if v < mins[j]: mins[j] = v
            if v > maxs[j]: maxs[j] = v
    return mins, maxs


# ─── Material conversion ────────────────────────────────────────────────


def _build_gltf_material(part_material, texture_state):
    """Build a glTF Material from a Part.material (PbrMaterial | Material | None).

    M2 + real textures: when `part_material` carries a `texture` ref AND
    the bundled files exist on disk, embed up to THREE images into the glb:
    - baseColor (sRGB) → `pbrMetallicRoughness.baseColorTexture`
    - normal (linear) → `material.normalTexture`
    - metallicRoughness (linear; G=roughness, B=metallic) → `pbrMetallicRoughness.metallicRoughnessTexture`
    Each map is optional — missing files don't break the export (uniform
    factors take over for that channel).
    """
    from pygltflib import (  # type: ignore
        Material as GltfMaterial,
        NormalMaterialTexture,
        PbrMetallicRoughness,
        TextureInfo,
    )
    pbr = material_to_pbr(part_material)
    if pbr is None:
        return GltfMaterial(
            name="default",
            pbrMetallicRoughness=PbrMetallicRoughness(
                baseColorFactor=[0.7, 0.7, 0.7, 1.0],
                metallicFactor=0.0,
                roughnessFactor=0.5,
            ),
        )

    bc = list(pbr.base_color) if pbr.base_color else [0.7, 0.7, 0.7, 1.0]
    pbr_block = PbrMetallicRoughness(
        baseColorFactor=bc,
        metallicFactor=float(pbr.metallic),
        roughnessFactor=float(pbr.roughness),
    )

    mat = GltfMaterial(name=pbr.name, pbrMetallicRoughness=pbr_block)

    # Embed up to three texture maps, cached per (name, map_kind) so multiple
    # parts using the same material share Images + Textures.
    if pbr.texture is not None:
        bc_idx = texture_state.get_or_create(pbr.texture.name, "base_color")
        if bc_idx is not None:
            pbr_block.baseColorTexture = TextureInfo(index=bc_idx)

        n_idx = texture_state.get_or_create(pbr.texture.name, "normal")
        if n_idx is not None:
            mat.normalTexture = NormalMaterialTexture(index=n_idx, scale=1.0)

        mr_idx = texture_state.get_or_create(pbr.texture.name, "metallic_roughness")
        if mr_idx is not None:
            pbr_block.metallicRoughnessTexture = TextureInfo(index=mr_idx)

    if bc[3] < 1.0:
        mat.alphaMode = "BLEND"
    return mat


class _TextureState:
    """Per-export texture cache. Loads texture image bytes once per name,
    appends to the glb's buffer + images + textures arrays, returns the
    glTF texture index for caller's `baseColorTexture.index`.

    Returns None when the named texture isn't installed (the manifest entry
    referenced a file that doesn't exist) — caller falls back to the
    metallic/roughness numbers alone. M2 placeholders ship via
    `scripts/generate_placeholder_textures.py`; real PBR textures swap into
    the same paths later.
    """

    def __init__(self, blob: bytearray, buffer_views: list, images: list,
                 textures: list, samplers: list,
                 *, texture_base_url: str | None = None):
        from pygltflib import Sampler  # type: ignore
        self._blob = blob
        self._buffer_views = buffer_views
        self._images = images
        self._textures = textures
        self._samplers = samplers
        self._texture_base_url = (texture_base_url.rstrip("/") if texture_base_url else None)
        # Cache keyed by (material_name, map_kind) — same material referenced
        # across multiple parts shares Images + Textures.
        self._cache: dict[tuple[str, str], int] = {}
        # One repeating sampler shared by every texture in this glb.
        # 10497 = REPEAT, 9729 = LINEAR, 9987 = LINEAR_MIPMAP_LINEAR.
        self._samplers.append(Sampler(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497))
        self._sampler_idx = len(self._samplers) - 1

    def get_or_create(self, name: str, map_kind: str = "base_color") -> int | None:
        """Return glTF texture index for (name, map_kind).

        Two modes:
        - **External URI** (`texture_base_url` set): writes Image.uri pointing
          at the daemon's texture endpoint. ~0 bytes added to the glb's
          binary blob. Browser + Blender fetch the texture over HTTP and
          cache it across records.
        - **Embedded** (no base URL): reads file bytes into a buffer view
          inside the glb. Portable (single-file glb) but ~3 MB per glb.

        Cached per (name, map_kind) — material reuse across parts shares
        Image + Texture entries within ONE glb.

        Returns None when the bundled file doesn't exist on disk in
        embed mode. In URI mode, returns the index even without verifying
        the file (the daemon endpoint resolves at fetch time).
        """
        cache_key = (name, map_kind)
        if cache_key in self._cache:
            return self._cache[cache_key]
        from pygltflib import BufferView, Image as GltfImage, Texture  # type: ignore

        if self._texture_base_url:
            # External URI mode — no bytes embedded. Trust the daemon
            # endpoint to serve `<base>/<material>/<map_kind>`. We don't
            # check disk presence here; a 404 at fetch time is the
            # downstream concern.
            uri = f"{self._texture_base_url}/{name}/{map_kind}"
            img_idx = len(self._images)
            self._images.append(GltfImage(uri=uri))
            tex_idx = len(self._textures)
            self._textures.append(Texture(sampler=self._sampler_idx, source=img_idx))
            self._cache[cache_key] = tex_idx
            return tex_idx

        # Embed mode: read bytes + append to binary blob + reference
        # via a bufferView. Larger glb, fully portable.
        from .materials import texture_path
        img_path = texture_path(name, map_kind)
        if img_path is None or not img_path.exists():
            return None
        data = img_path.read_bytes()
        offset = len(self._blob)
        self._blob.extend(data)
        while len(self._blob) % 4:
            self._blob.append(0)
        bv_idx = len(self._buffer_views)
        self._buffer_views.append(BufferView(
            buffer=0, byteOffset=offset, byteLength=len(data),
        ))
        mime = "image/jpeg"
        suffix = img_path.suffix.lower()
        if suffix == ".png":
            mime = "image/png"
        elif suffix in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        img_idx = len(self._images)
        self._images.append(GltfImage(bufferView=bv_idx, mimeType=mime))
        tex_idx = len(self._textures)
        self._textures.append(Texture(sampler=self._sampler_idx, source=img_idx))
        self._cache[cache_key] = tex_idx
        return tex_idx


# ─── Main export ────────────────────────────────────────────────────────


def export_glb(model: ArticulatedObject, out_path: str | Path,
               *, texture_base_url: str | None = None) -> Path:
    """Write `model` as a binary glTF file at `out_path`.

    Each Part becomes one node + mesh. Visual origin → node transform.
    No joint info — that lives in the parallel URDF. The viewer reads
    both files and cross-references by node name.

    `texture_base_url`: when provided, texture images are referenced by
    external URI (`<base>/<material>/<map_kind>`) instead of embedded into
    the glb's binary blob. Saves ~3 MB → ~50 KB per glb. Daemon serves
    the texture endpoint as a public route. When None: bytes are embedded
    (portable .glb but bigger).

    Returns the resolved output path.
    """
    # Lazy pygltflib import — keeps `engines.articulated` loadable in the
    # daemon's 3.13 Python where pygltflib isn't installed.
    from pygltflib import (
        ARRAY_BUFFER,
        ELEMENT_ARRAY_BUFFER,
        FLOAT,
        GLTF2,
        UNSIGNED_INT,
        Accessor,
        Asset,
        Attributes,
        Buffer,
        BufferView,
        Mesh,
        Node,
        Primitive,
        Scene,
    )

    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    gltf = GLTF2()
    gltf.asset = Asset(generator="emptyos.engines.articulated", version="2.0")

    # One big binary blob — we'll concatenate all part data, then create
    # bufferViews + accessors slicing into it.
    blob = bytearray()
    buffer_views: list = []
    accessors: list = []
    meshes: list = []
    materials: list = []
    nodes: list = []
    images: list = []
    textures: list = []
    samplers: list = []

    # Texture cache — appends to images/textures/samplers/blob as it goes.
    # `texture_base_url` (when provided) switches to external-URI mode so
    # the glb is tiny and the daemon serves textures over HTTP.
    tex_state = _TextureState(blob, buffer_views, images, textures, samplers,
                              texture_base_url=texture_base_url)

    # Compute each part's LINK frame world position by walking the joint
    # graph from the root. URDF semantics: link world = chain of joint
    # origins from root down to this link. Part.origin.xyz is the VISUAL
    # offset within the link frame. Final visual world position =
    # link_world + part.origin.xyz.
    #
    # We bake these into each node's translation so the glb is a self-
    # contained flat scene (no hierarchy). Joint sliders (M6) still need
    # to find these nodes by name and re-parent them under joint groups
    # to drive articulation; that's a viewer-side concern.
    #
    # Position-only chain — ignores joint rpy rotations. For the typical
    # robot-modeller object (axis-aligned furniture, Fixed joints with rpy=0)
    # this is correct. Full SE(3) composition (joints with rotation) is
    # a future refinement once a real consumer needs it.
    link_world: dict[str, tuple[float, float, float]] = {}
    try:
        root_part = model.root_part()
        link_world[root_part.name] = (0.0, 0.0, 0.0)
    except Exception:
        # No root or multi-root — already a compile-time error elsewhere;
        # fall back to link@origin for every part so we still produce a glb.
        root_part = None
    parent_joint = {j.child: j for j in model.joints}
    def _resolve(name: str) -> tuple[float, float, float]:
        if name in link_world:
            return link_world[name]
        j = parent_joint.get(name)
        if j is None:
            link_world[name] = (0.0, 0.0, 0.0)
            return link_world[name]
        px, py, pz = _resolve(j.parent)
        jx, jy, jz = j.origin.xyz
        link_world[name] = (px + jx, py + jy, pz + jz)
        return link_world[name]
    for p in model.parts:
        _resolve(p.name)

    # Per-part: tessellate, pack, create accessors, create mesh, create node.
    for part in model.parts:
        positions, normals, uvs, indices = _tessellate_geometry(part.geometry)

        # Pack positions.
        pos_bytes = _pack_floats(positions)
        pos_offset = len(blob)
        blob.extend(pos_bytes)
        # Pad to 4-byte alignment between bufferViews.
        while len(blob) % 4:
            blob.append(0)
        bv_pos = len(buffer_views)
        buffer_views.append(BufferView(
            buffer=0, byteOffset=pos_offset, byteLength=len(pos_bytes),
            target=ARRAY_BUFFER,
        ))
        pos_min, pos_max = _bbox(positions, 3)
        ac_pos = len(accessors)
        accessors.append(Accessor(
            bufferView=bv_pos, componentType=FLOAT, count=len(positions) // 3,
            type="VEC3", min=pos_min, max=pos_max,
        ))

        # Normals.
        nrm_bytes = _pack_floats(normals)
        nrm_offset = len(blob)
        blob.extend(nrm_bytes)
        while len(blob) % 4:
            blob.append(0)
        bv_nrm = len(buffer_views)
        buffer_views.append(BufferView(
            buffer=0, byteOffset=nrm_offset, byteLength=len(nrm_bytes),
            target=ARRAY_BUFFER,
        ))
        ac_nrm = len(accessors)
        accessors.append(Accessor(
            bufferView=bv_nrm, componentType=FLOAT, count=len(normals) // 3,
            type="VEC3",
        ))

        # UVs (TEXCOORD_0). Without this attribute the importer samples
        # textures at UV=(0,0) for every vertex — the entire mesh collapses
        # to the top-left pixel of each texture map. Per-geometry-type UV
        # builder above produces sensible 0..1 projections.
        uv_bytes = _pack_floats(uvs)
        uv_offset = len(blob)
        blob.extend(uv_bytes)
        while len(blob) % 4:
            blob.append(0)
        bv_uv = len(buffer_views)
        buffer_views.append(BufferView(
            buffer=0, byteOffset=uv_offset, byteLength=len(uv_bytes),
            target=ARRAY_BUFFER,
        ))
        ac_uv = len(accessors)
        accessors.append(Accessor(
            bufferView=bv_uv, componentType=FLOAT, count=len(uvs) // 2,
            type="VEC2",
        ))

        # Indices.
        idx_bytes = _pack_uints(indices)
        idx_offset = len(blob)
        blob.extend(idx_bytes)
        while len(blob) % 4:
            blob.append(0)
        bv_idx = len(buffer_views)
        buffer_views.append(BufferView(
            buffer=0, byteOffset=idx_offset, byteLength=len(idx_bytes),
            target=ELEMENT_ARRAY_BUFFER,
        ))
        ac_idx = len(accessors)
        accessors.append(Accessor(
            bufferView=bv_idx, componentType=UNSIGNED_INT, count=len(indices),
            type="SCALAR",
        ))

        # Material.
        mat_idx = len(materials)
        materials.append(_build_gltf_material(part.material, tex_state))

        # Mesh — one primitive per part.
        meshes.append(Mesh(
            name=part.name,
            primitives=[Primitive(
                attributes=Attributes(POSITION=ac_pos, NORMAL=ac_nrm, TEXCOORD_0=ac_uv),
                indices=ac_idx,
                material=mat_idx,
            )],
        ))

        # Node — name matches part.name (for viewer cross-reference with URDF).
        # Translation = link world position (from joint chain) + visual offset
        # (part.origin.xyz). Rotation = part.origin.rpy converted to quaternion.
        # Joint rotations are not propagated yet; M6's joint-slider wiring
        # will re-introduce hierarchy as needed.
        from math import cos, sin
        rx, ry, rz = part.origin.rpy
        cr, sr_ = cos(rx / 2), sin(rx / 2)
        cp, sp = cos(ry / 2), sin(ry / 2)
        cy, sy = cos(rz / 2), sin(rz / 2)
        qx = sr_ * cp * cy - cr * sp * sy
        qy = cr * sp * cy + sr_ * cp * sy
        qz = cr * cp * sy - sr_ * sp * cy
        qw = cr * cp * cy + sr_ * sp * sy
        lw = link_world.get(part.name, (0.0, 0.0, 0.0))
        vx, vy, vz = part.origin.xyz
        world_translation = [lw[0] + vx, lw[1] + vy, lw[2] + vz]
        node = Node(
            name=part.name,
            mesh=len(meshes) - 1,
            translation=world_translation,
            rotation=[qx, qy, qz, qw],
        )
        nodes.append(node)

    # Scene — flat list of all part nodes. We don't reproduce URDF's joint
    # tree here; the viewer handles articulation via the URDF sidecar.
    gltf.nodes = nodes
    gltf.meshes = meshes
    gltf.materials = materials
    gltf.accessors = accessors
    gltf.bufferViews = buffer_views
    gltf.buffers = [Buffer(byteLength=len(blob))]
    # M2: texture-related glb structures. Only emit when non-empty —
    # untextured records (no PbrMaterial.texture) stay minimal.
    if images:
        gltf.images = images
    if textures:
        gltf.textures = textures
    if samplers:
        gltf.samplers = samplers
    gltf.scenes = [Scene(name=model.name, nodes=list(range(len(nodes))))]
    gltf.scene = 0

    # Pack the binary blob + save as .glb (binary glTF — single file).
    gltf.set_binary_blob(bytes(blob))
    gltf.save_binary(str(out_path))
    return out_path
