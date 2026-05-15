# Shared Frontend — Components, Maps, Geo, Shortcuts

**Design language — read this before touching a page.** `docs/FRONTEND-DESIGN-LANGUAGE.md` is the visual + interaction DNA: token usage, spacing/radius/type scales, AI-surface treatment, motion discipline, forbidden patterns. Audits run via `/eos-ui-audit-and-consolidate`.

## EOS_UI components

`emptyos/web/static/eos-components.css` + `.js` — shared UI (toast, hero, donut, tabs, modal, entry list, ring, heatmap). Use `EOS_UI.modal()`, `EOS_UI.formModal()`, `EOS_UI.statCards()`, `EOS_UI.confirm()`, `EOS_UI.entityCard()`, `EOS_UI.emptyState()`, `EOS_UI.errorState()`, `EOS_UI.provenance()`, `EOS_UI.searchBar({mount, onSelectApp?, onFallback?, focusKey?})` (inline app+vault search) in new pages.

- Status/priority/age badges: `.eos-badge` + `.eos-badge-status-*` / `-priority-*` / `-age-*` (no more hand-rolled status pill CSS per app).
- AI provenance chip: `.eos-badge-provenance` + `-local`/`-cloud`/`-user` variants — pair with `BaseApp.last_provenance()` to return provenance from the API after a `self.think()` call.
- Floating pill FABs: `.eos-fab-pill` + `-icon` + `-label` (used by capture, assistant, voice, hands-free).

## Other shared bundles

- `emptyos/web/static/eos-keys.js` + `.css` — keyboard shortcuts (command palette, go-to nav, help)
- `emptyos/web/static/eos-map.js` + `.css` — Leaflet wrapper (`EOS_MAP.create(container, {center, zoom, tiles:'osm'|'aerial'|'both'|'both-aerial'})` → `.setMarkers(items, {latFor, lngFor, popupFor, iconFor, onClick})`, `.setPolylines(lines, {styleFor})`, `.fitBounds()`, `.invalidateSize()`). `iconFor` returns `{className, size, html?}`; `html` lets you render arbitrary content inside the marker (e.g. trip-stop numbers). Leaflet loads on demand via CDN; drop to raw Leaflet via `.L()` / `.map()`. Consumers: `apps/personal/places/`, `apps/personal/scan-map/`.
- `emptyos/web/static/eos-geocad.js` + `.css` — Leaflet-Geoman drawing layer for georeferenced editing. Two surfaces: `EOS_GEOCAD.mount(el, {layerId, basemap, readonly, onFeatureChange})` creates its own map; `EOS_GEOCAD.attach(eosMap, {layerId, ...})` adds drawing controls to an existing `EOS_MAP`. GeoJSON I/O via `loadFromServer()` / `saveToServer()`. Lazy-loads Leaflet-Geoman from CDN on first use. Owned by `apps/geo-cad/`, which stores layers as paired `.md` sidecar + `.geojson` (RFC 7946 FeatureCollection) under `30_Resources/EmptyOS/geo-cad/<project_id>/`. Consumer apps call `self.call_app("geo-cad", "add_layer"|"add_feature", ...)`. Vault frontmatter convention for individual-feature notes (flat `lat/lon` for points, `geo: {type, coordinates, crs}` for geometries) is in `.claude/rules/geo.md`.
- `emptyos/web/clustering.py` — auto-clustering for home screen
- `emptyos/web/auto_ui.py` — auto-generated UI for apps without `pages/`

## Geo stack

`apps/geocode/` (address ↔ lat/lon via OSM Nominatim) + `apps/routing/` (multi-stop routes via OSRM) + `EOS_MAP`. Both geo apps cache + throttle; both configurable via `[apps.<id>]` `user_agent` + `base_url`. Frontend helpers: `EOS.geocode(address, limit)`, `EOS.reverseGeocode(lat, lon)`, `EOS.getRoute(points, profile)`, `EOS.fmtDistance(m)`, `EOS.fmtDuration(s)`. (Named `getRoute`, not `route` — `EOS.route` is the client-side SPA router.) **Public-mode gate:** both apps expose `GET /api/status`; when `network.mode = "public"` *and* still using the demo URL, `.enabled = false` with a human `reason`. Handlers short-circuit, and UIs consuming the gate (e.g. `apps/personal/places/`) hide the geocode/trip buttons. Self-hosters pointing `base_url` at their own OSRM/Nominatim stay enabled in all modes.

## Vault link rendering

**Vault paths are always clickable** — use `EOS.noteActions(path)`, never plain `esc(path)`. Renders view + edit + open-external links.

## Keyboard Shortcuts

- `Ctrl+K` / `Cmd+K` — command palette
- `g` + letter — go-to nav (g-t=Tasks, g-j=Journal, g-e=Expense, g-s=Search, g-a=Assistant)
- `?` or `Ctrl+/` — shortcut help
- `/` — focus search input
- `Esc` — close overlays
- Data-driven: `GET /api/shortcuts`; editable in settings "Shortcuts" tab
