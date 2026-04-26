# Blender Cable Routing Skill

Use this skill when creating 3D cable routing visualizations in Blender via MCP.

## When to Use

- Creating underground/above-ground cable installation models
- Visualizing cable routing through culverts, walls, transitions
- Generating cable route animations
- Producing technical drawings with annotations

## Quick Reference

### Cable Parameters (33kV Example)

```python
cable_od = 0.055          # 55mm outer diameter
cable_radius = cable_od / 2
cable_spacing = 0.150     # 150mm center-to-center
min_bend_radius = 1.5     # 1500mm (typically 20x OD)
```

### Coordinate System

| Axis | Use |
|------|-----|
| X | Lateral spacing (across cable formation) |
| Y | Along cable route (horizontal run) |
| Z | Vertical (elevation) |

---

## CRITICAL RULES

### 1. Cable Formation Through Turns

**All cables must turn around a COMMON center point.**

```
WRONG: Each cable turns around its own center → cables collapse together
RIGHT: All cables share common center, adjusted radii maintain spacing
```

**Formula for 90° right turn (-Y to +X):**
- Common center: `(R, Y_entry, Z)`
- Outside cable: `radius = R + offset` (larger)
- Center cable: `radius = R`
- Inside cable: `radius = R - offset` (smaller)

### 2. Transparency Hierarchy

| Element | Opacity | Reason |
|---------|---------|--------|
| Cables | 100% | Main focus |
| Ground | 30% | See cables underground |
| Walls | 40% | See penetrations |
| Culvert | 35% | See cables inside |
| Conduits | 50% | See cables inside |
| Radius circles | 40% | Reference only |

### 3. Ground-Wall Relationship

```
Lower ground ends AT wall front face
Upper ground starts AT wall back face
No gaps, no overlaps
```

### 4. Element Interactions

| From → To | Add |
|-----------|-----|
| Cable → Wall | Conduit sleeve |
| Cable → Culvert entry | Horizontal conduit |
| Cable → Culvert exit | Angled conduit (match slope) |
| Cable on slope | Ladder below, parallel |

---

## Rotation Quick Reference

| Object | Plane/Direction | Rotation (Euler) |
|--------|-----------------|------------------|
| Circle on YZ plane | Normal = X | `(0, π/2, 0)` |
| Circle on XZ plane | Normal = Y | `(π/2, 0, 0)` |
| Circle on XY plane | Normal = Z | `(0, 0, 0)` |
| Cylinder along X | | `(0, π/2, 0)` |
| Cylinder along Y | | `(π/2, 0, 0)` |
| Cylinder along Z | | `(0, 0, 0)` |

---

## Multi-View Annotation

| View | Rotation | Collection |
|------|----------|------------|
| SIDE | `(π/2, 0, 0)` | Labels_SIDE_VIEW |
| PLAN | `(0, 0, 0)` | Labels_PLAN_VIEW |
| FRONT | `(π/2, 0, π/2)` | Labels_FRONT_VIEW |
| ISO | `(π/2, 0, π/4)` | Labels_ISO_VIEW |

**Naming:** `Label_{ObjectName}_{ViewType}`

---

## Animation Timing (25s at 30fps)

| Phase | Frames | Duration |
|-------|--------|----------|
| Overview | 1-90 | 3s |
| Descend | 91-150 | 2s |
| Cable Tour | 151-750 | 20s |

---

## Render Settings

```python
# 720p test render
resolution_x = 1280
resolution_y = 720
engine = 'BLENDER_EEVEE'
samples = 64
fps = 30
```

### Direct MP4 Render (Blender 5.0+)

In Blender 5.0+, use `media_type` instead of `file_format` for video output:

```python
import bpy
import os

scene = bpy.context.scene

# Set media type to VIDEO (Blender 5.0 API change)
scene.render.image_settings.media_type = 'VIDEO'

# Configure FFmpeg settings for MP4
scene.render.ffmpeg.format = 'MPEG4'
scene.render.ffmpeg.codec = 'H264'
scene.render.ffmpeg.constant_rate_factor = 'HIGH'  # Quality: LOWEST, VERY_LOW, LOW, MEDIUM, HIGH, PERC_LOSSLESS, LOSSLESS
scene.render.ffmpeg.ffmpeg_preset = 'GOOD'
scene.render.ffmpeg.audio_codec = 'AAC'

# Set output path (must include .mp4 extension)
scene.render.filepath = os.path.expanduser("~/Desktop/output.mp4")

# Render animation
bpy.ops.render.render(animation=True)
```

**Note:** In Blender <5.0, use `scene.render.image_settings.file_format = 'FFMPEG'` instead.

### Command Line Render (PNG + FFmpeg)

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b file.blend -o ~/frames/frame_ -F PNG -a
ffmpeg -framerate 30 -i ~/frames/frame_%04d.png -c:v libx264 -pix_fmt yuv420p output.mp4
```

---

## Detailed Documentation

For complete rules and code examples, see:
- [[Blender Cable Routing Rules]] - Full technical reference
