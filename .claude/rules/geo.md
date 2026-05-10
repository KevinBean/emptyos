# Geo Frontmatter & GeoJSON — Convention for All Geo-Aware Apps

EmptyOS apps that touch real-world coordinates (places, scan-map, cables, lines, earthing, lightning, routing, weather) follow one frontmatter convention so any app's notes can be queried, mapped, or imported into a `geo-cad` layer without hand-rolled adapters.

## The convention

### Single-point notes — flat keys (preferred for ergonomics)

```yaml
---
lat: -33.8688         # WGS84 latitude, decimal degrees
lon: 151.2093         # WGS84 longitude, decimal degrees
---
```

`lon` is the canonical key. `lng` is accepted on read by `EOS_MAP` for Leaflet familiarity but **don't write** it — write `lon` so VaultIndex queries are consistent.

### Multi-vertex geometry — `geo:` block (GeoJSON-shaped)

```yaml
---
geo:
  type: LineString          # GeoJSON geometry type
  coordinates: [[151.20, -33.86], [151.30, -33.91]]   # [lon, lat] order!
  crs: EPSG:4326            # default WGS84; omit if 4326
---
```

Geometry types: `Point`, `LineString`, `Polygon`, `MultiPoint`, `MultiLineString`, `MultiPolygon`. Always **`[lon, lat]`** per RFC 7946 — opposite of Leaflet's `[lat, lng]`. The rendering layer is responsible for the swap; vault storage stays GeoJSON-conformant.

### Layer membership (optional)

```yaml
---
layer: cables-22kv          # layer id in geo-cad app
---
```

Lets a feature note declare itself as belonging to a `geo-cad` layer. Geo-cad reads notes carrying both `geo:` and `layer:` and surfaces them in that layer's view.

## When to use each

| Note type | Use |
|---|---|
| Single POI (place, person's home, sensor) | flat `lat/lon` |
| Linear feature (cable run, road, river) | `geo: {type: LineString, ...}` |
| Bounded region (site plot, protection zone, parcel) | `geo: {type: Polygon, ...}` |
| Multiple points or shapes per note | `geo: {type: Multi…, ...}` |

## Storage in geo-cad

Layers are stored as paired files in the vault:

```
{vault}/30_Resources/EmptyOS/geo-cad/{project_id}/{layer_id}.md       # sidecar (frontmatter only)
{vault}/30_Resources/EmptyOS/geo-cad/{project_id}/{layer_id}.geojson  # RFC 7946 FeatureCollection
```

Sidecar frontmatter:
- `id`, `title`, `project_id`
- `tags: [geo-layer]`
- `crs` (default `EPSG:4326`)
- `attribute_schema` (JSON-encoded — flat-frontmatter constraint)
- `default_style` (JSON-encoded)
- `feature_count`, `source_app`, `created`, `updated`

Per-feature notes (flat `lat/lon` or `geo:` block) live wherever the owning app already puts them — not duplicated into the layer file. Geo-cad reads both: explicit `.geojson` features for layers it owns, and `vault_query(tags=[...], layer=...)` for features other apps own.

## Why this shape

- **`[lon, lat]` order in `geo:` block** matches RFC 7946 GeoJSON exactly — no client-side adapters needed when writing layers.
- **Flat `lat/lon` for single points** matches `apps/weather/`, `apps/geocode/` returns, and Nominatim's response shape — no migration needed.
- **`crs` defaults to EPSG:4326** because every existing tool (Leaflet, OSRM, Nominatim) speaks WGS84 natively. UTM/local grids are a v2 concern.
- **Frontmatter is flat-only** (vault parser limitation, see CLAUDE.md § Development Gotchas). A geometry block uses one nested level (`coordinates: [[..]]`) which the YAML parser handles via inline-array + inline-array, but anything deeper must be JSON-encoded into a string field. `attribute_schema` in geo-cad is JSON-encoded for this reason.

## Reading in apps

```python
fm = self.vault_get_properties(path)
# Flat point
lat = fm.get("lat"); lon = fm.get("lon")
# Geometry block — yaml parses inline arrays, so coordinates is already nested lists
geo = fm.get("geo") or {}
```

## Writing in apps

Don't reformat. Stick to the shape above. If the user wrote `lng` by hand, leave it on read but never write it back.

## Migration (when needed)

Apps storing abstract `x, y` (e.g. `apps/cables/` topology mode) keep their existing schema untouched. To switch a project to geo, set `mode: geo` in the project's frontmatter and use `lat/lon` on its node notes. The two modes coexist per-project — see `apps/cables/` geo mode docs.
