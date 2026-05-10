"""Auto-UI generator — every app gets a functional web interface.

Reads an app's web routes and generates an HTML page that uses the shared
EmptyOS component bundle (eos-components.{css,js}, theme.css, eos-keys.{css,js}).
No inline CSS that duplicates the bundle, no hand-rolled list rendering.

Three signal shapes are detected:
  1. CRUD collection: paired (GET /api/<x>, [GET /api/<x>/{id}], [DELETE /api/<x>/{id}],
     [POST /api/<x>], [PUT /api/<x>/{id}]) — rendered via EOS_UI.entityList +
     EOS_UI.formModal for adds, with row-level eos-row-del + confirmDelete.
  2. Stats endpoint: GET /api/stats|status|summary — rendered via EOS_UI.statCards.
  3. One-shot action: standalone POST not paired with a GET — rendered as a
     formModal trigger card.

The auto-UI is the default for new apps (eos-new-app skill skips writing
pages/index.html); authors override by adding a custom pages/index.html, which
the platform serves in preference.
"""

from __future__ import annotations

import html as _html
import json as _json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.kernel.app_loader import AppManifest


# --- Route classification (unchanged from the prior implementation) ---


def _classify_route(method: str, path: str) -> str:
    """Classify a route into a UI pattern."""
    p = path.lower()
    if method == "GET":
        if "stats" in p or "status" in p or "summary" in p:
            return "stats"
        if "heatmap" in p:
            return "heatmap"
        if re.search(r"/\{[^}]+\}$", p):
            return "detail"
        if any(w in p for w in ("list", "entries", "items", "sessions", "history", "highlights")):
            return "list"
        return "data"
    elif method == "POST":
        if any(w in p for w in ("generate", "create", "start", "add", "log")):
            return "action"
        if "search" in p or "smart" in p:
            return "search"
        if "chat" in p or "message" in p or "converse" in p:
            return "chat"
        return "submit"
    elif method == "DELETE":
        return "delete"
    elif method == "PUT":
        return "update"
    return "other"


def _guess_fields(path: str) -> list[dict]:
    """Guess input fields from route path and common patterns.

    Same heuristic the prior implementation used for POST forms; reused here
    for the formModal `fields` shape. Returns a list of field dicts with
    `name`, `type` (text|number|select), optional `placeholder`, `options`.
    """
    p = path.lower()
    if "log" in p or "add" in p or "capture" in p:
        return [{"name": "text", "type": "text", "placeholder": "Type something..."}]
    if "generate" in p:
        if "lesson" in p:
            return [
                {"name": "topic", "type": "text", "placeholder": "e.g., ordering food at a restaurant"},
                {"name": "difficulty", "type": "select",
                 "options": ["beginner", "intermediate", "advanced"]},
            ]
        if "sentence" in p or "shadow" in p:
            return [
                {"name": "difficulty", "type": "select",
                 "options": ["beginner", "intermediate", "advanced"]},
                {"name": "count", "type": "number", "placeholder": "5", "default": "5"},
            ]
        return [{"name": "prompt", "type": "text", "placeholder": "Describe what to generate..."}]
    if "start" in p:
        if "session" in p:
            return [{"name": "scenario", "type": "text",
                     "placeholder": "e.g., free, interview, daily"}]
        return [{"name": "text", "type": "text", "placeholder": "Start..."}]
    if "search" in p or "find" in p:
        return [{"name": "query", "type": "text", "placeholder": "Search..."}]
    if "chat" in p or "message" in p:
        return [{"name": "text", "type": "text", "placeholder": "Type a message..."}]
    return [{"name": "text", "type": "text", "placeholder": "Input..."}]


def _fields_to_modal_fields(fields: list[dict]) -> list[dict]:
    """Convert _guess_fields shape -> EOS_UI.formModal field shape."""
    out = []
    for f in fields:
        out.append({
            "name": f["name"],
            "label": f.get("label") or f["name"].replace("_", " ").title(),
            "type": f.get("type", "text"),
            "placeholder": f.get("placeholder", ""),
            "options": f.get("options"),
            "default": f.get("default", ""),
        })
    return out


# --- Collection detection: the load-bearing new piece ---


_PARAM_RE = re.compile(r"\{[^}]+\}")


def _strip_id_suffix(path: str) -> str | None:
    """If path ends in /{...}, return the path without that segment, else None."""
    m = re.match(r"^(.*?)/\{[^}]+\}$", path)
    return m.group(1) if m else None


def _detect_collections(routes: list[dict]) -> tuple[list[dict], set[int]]:
    """Find CRUD-triple groups in the route set.

    A collection is identified by GET <base> plus any of:
      - GET <base>/{id}     (detail; we don't render it, but we note it)
      - DELETE <base>/{id}  (per-row delete -> entityList wires it)
      - POST <base>         (add -> formModal)
      - PUT <base>/{id}     (update; not rendered today)
    """
    by_method: dict[str, list[tuple[int, dict]]] = {}
    for i, r in enumerate(routes):
        by_method.setdefault(r["method"].upper(), []).append((i, r))

    collections: list[dict] = []
    consumed: set[int] = set()

    for i, list_route in by_method.get("GET", []):
        path = list_route["path"]
        if _PARAM_RE.search(path):
            continue
        if not path.startswith("/api/"):
            continue
        kind = _classify_route("GET", path)
        if kind not in ("list", "data"):
            continue

        base = path
        full_base = list_route.get("full") or path
        coll = {
            "base": base,
            "list_path": path,
            "list_full": full_base,
            "detail_full": None,
            "delete_full": None,
            "post_full": None,
            "post_path": None,
            "post_idx": None,
        }
        list_consumed_now = set()

        for j, dr in by_method.get("GET", []):
            if j in consumed or j == i:
                continue
            stripped = _strip_id_suffix(dr["path"])
            if stripped == base:
                coll["detail_full"] = dr.get("full") or dr["path"]
                list_consumed_now.add(j)
                break

        for j, dr in by_method.get("DELETE", []):
            if j in consumed:
                continue
            stripped = _strip_id_suffix(dr["path"])
            if stripped == base:
                coll["delete_full"] = dr.get("full") or dr["path"]
                coll["delete_full"] = re.sub(r"/\{[^}]+\}$", "", coll["delete_full"])
                list_consumed_now.add(j)
                break

        for j, pr in by_method.get("POST", []):
            if j in consumed:
                continue
            if pr["path"] == base:
                coll["post_full"] = pr.get("full") or pr["path"]
                coll["post_path"] = pr["path"]
                coll["post_idx"] = j
                list_consumed_now.add(j)
                break

        for j, ur in by_method.get("PUT", []):
            if j in consumed:
                continue
            stripped = _strip_id_suffix(ur["path"])
            if stripped == base:
                list_consumed_now.add(j)
                break

        if any(coll[k] for k in ("detail_full", "delete_full", "post_full")):
            consumed.add(i)
            consumed.update(list_consumed_now)
            if coll["post_path"]:
                coll["add_fields"] = _fields_to_modal_fields(_guess_fields(coll["post_path"]))
            collections.append(coll)

    return collections, consumed


# --- HTML escaping helpers (used everywhere — keep paranoid) ---


def _e(s) -> str:
    """HTML-escape any value, treating None as ''."""
    return _html.escape(str(s) if s is not None else "")


def _label_from_path(path: str) -> str:
    """Derive a human-readable label from a path segment."""
    last = path.rstrip("/").split("/")[-1]
    last = re.sub(r"\{[^}]+\}", "", last)
    return last.replace("-", " ").replace("_", " ").strip().title() or "Action"


def _slug_from_path(path: str) -> str:
    """ASCII slug for use as an HTML id; safe to interpolate."""
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", path).strip("-") or "item"


def _safe_json(obj) -> str:
    """JSON-encode for embedding inside <script>...</script>.

    The browser's HTML parser closes the script block at any `</`. Standard
    idiom: replace `</` with `<\\/` so the JSON-stringified content can't
    accidentally close the surrounding script tag.
    """
    return _json.dumps(obj).replace("</", "<\\/")


# --- Page generation ---


def generate_app_page(manifest: "AppManifest", routes: list[dict]) -> str:
    """Generate a functional HTML page from manifest + routes."""
    app_id = manifest.id
    app_name = manifest.name
    description = manifest.description or ""
    prefix = manifest.provides.get("web", {}).get("prefix", "")
    events_emitted = manifest.events_emits or []
    capabilities = manifest.requires.get("capabilities", []) or []
    connectors = manifest.requires.get("connectors", []) or []

    enriched = []
    for r in routes:
        rr = dict(r)
        rr["full"] = prefix + r["path"]
        enriched.append(rr)

    collections, consumed = _detect_collections(enriched)

    stats_routes: list[tuple[str, str]] = []
    standalone_posts: list[dict] = []
    misc_gets: list[tuple[str, str]] = []

    for i, r in enumerate(enriched):
        if i in consumed:
            continue
        method = r["method"].upper()
        path = r["path"]
        full = r["full"]
        kind = _classify_route(method, path)
        if method == "GET":
            if kind == "stats":
                stats_routes.append((path, full))
            elif kind == "detail":
                continue
            else:
                misc_gets.append((path, full))
        elif method == "POST":
            if "{" in path:
                continue
            standalone_posts.append({
                "path": path, "full": full,
                "label": _label_from_path(path),
                "fields": _fields_to_modal_fields(_guess_fields(path)),
                "slug": _slug_from_path(path),
            })

    sections_parts: list[str] = []

    for path, full in stats_routes:
        slug = _slug_from_path(path)
        target = f"stat-{slug}"
        sections_parts.append(
            f'<div class="eos-section-title">Overview</div>'
            f'<div id="{target}" data-stats-url="{_e(full)}">'
            f'<div class="eos-loading"><span class="eos-spinner"></span></div>'
            f'</div>'
        )

    for coll in collections:
        slug = _slug_from_path(coll["base"])
        list_id = f"list-{slug}"
        title = _label_from_path(coll["base"])
        actions_html = ""
        coll_data = {
            "url": coll["list_full"],
            "deleteUrl": coll["delete_full"] or None,
            "detailUrl": coll["detail_full"] or None,
        }
        if coll.get("post_full") and coll.get("add_fields"):
            # Use bracket access — slug may contain hyphens, which are not
            # valid in JS dot-syntax identifiers (`autoUiAdd_api-items()` is
            # a syntax error; `window['autoUiAdd_api-items']()` is fine).
            actions_html = (
                f'<button class="eos-btn eos-btn-sm eos-btn-primary" '
                f"onclick=\"window['autoUiAdd_{slug}']()\">+ Add</button>"
            )
        sections_parts.append(
            f'<div class="eos-section-title eos-section-head">'
            f'  <span>{_e(title)}</span>'
            f'  {actions_html}'
            f'</div>'
            f'<div id="{list_id}" data-collection="{_e(_safe_json(coll_data))}"></div>'
        )

    if standalone_posts:
        cards = []
        for post in standalone_posts:
            cards.append(
                f'<div class="eos-entity-card">'
                f'  <div class="eec-head">'
                f'    <div style="flex:1;min-width:0">'
                f'      <div class="eec-title">{_e(post["label"])}</div>'
                f'    </div>'
                f'  </div>'
                f'  <div class="eec-actions">'
                f'    <button class="eos-btn eos-btn-sm" '
                f"onclick=\"window['autoUiAction_{post['slug']}']()\">Run</button>"
                f'  </div>'
                f'</div>'
            )
        sections_parts.append(
            f'<div class="eos-section-title">Actions</div>'
            f'<div class="eos-action-grid">{"".join(cards)}</div>'
        )

    if misc_gets:
        cards = []
        for path, full in misc_gets:
            label = _label_from_path(path)
            cards.append(
                f'<div class="eos-entity-card" onclick="autoUiData(\'{_e(full)}\')">'
                f'  <div class="eec-head"><div class="eec-title">{_e(label)}</div></div>'
                f'</div>'
            )
        sections_parts.append(
            f'<div class="eos-section-title">Data</div>'
            f'<div class="eos-action-grid">{"".join(cards)}</div>'
        )

    sections = "\n".join(sections_parts) or (
        '<div class="eos-empty-state">'
        '<p class="eos-empty-state-message">'
        'This app exposes no auto-renderable routes yet.</p></div>'
    )

    badge_parts = []
    for cap in capabilities:
        badge_parts.append(f'<span class="eos-badge eos-badge-neutral">{_e(cap)}</span>')
    for conn in connectors:
        badge_parts.append(f'<span class="eos-badge eos-badge-neutral">{_e(conn)}</span>')
    badges_html = " ".join(badge_parts)

    collections_payload = _safe_json([
        {
            "slug": _slug_from_path(c["base"]),
            "listId": f"list-{_slug_from_path(c['base'])}",
            "url": c["list_full"],
            "deleteUrl": c["delete_full"],
            "detailUrl": c["detail_full"],
            "postUrl": c["post_full"],
            "addFields": c.get("add_fields") or [],
            "label": _label_from_path(c["base"]),
        }
        for c in collections
    ])
    actions_payload = _safe_json([
        {"slug": p["slug"], "url": p["full"], "fields": p["fields"], "label": p["label"]}
        for p in standalone_posts
    ])
    events_payload = _safe_json(events_emitted)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{_e(app_name)} — EmptyOS</title>
<link rel="stylesheet" href="/static/theme.css">
<link rel="stylesheet" href="/static/eos-components.css">
<link rel="stylesheet" href="/static/eos-keys.css">
<style>
.eos-section-title {{
    font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 1.5px;
    color: var(--text-muted);
    margin: 24px 0 12px;
}}
.eos-section-head {{ display: flex; align-items: center; justify-content: space-between; }}
.eos-action-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 10px;
}}
#auto-ui-output {{
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px; margin-top: 16px;
    max-height: 400px; overflow: auto;
    white-space: pre-wrap;
    font-family: var(--mono); font-size: 12px;
    display: none;
}}
.eos-events-strip {{
    font-size: 11px; color: var(--text-muted);
    margin-top: 24px; padding-top: 12px;
    border-top: 1px solid var(--border);
}}
.eos-events-strip > div {{ padding: 2px 0; }}
</style>
</head>
<body data-app-id="{_e(app_id)}">
<script src="/static/realtime.js"></script>
<script src="/static/eos.js"></script>
<script src="/static/eos-components.js"></script>
<script src="/static/eos-keys.js"></script>
<script>EOS.nav('{_e(app_id)}');</script>

<div class="page">
    <div class="page-header">
        <h1>{_e(app_name)}</h1>
        <div class="desc">{_e(description)}</div>
        <div style="margin-top:8px">{badges_html}</div>
    </div>

    {sections}

    <div id="auto-ui-output"></div>

    <div class="eos-events-strip">
        <div id="auto-ui-events">{("Listening: " + ", ".join(_e(e) for e in events_emitted)) if events_emitted else "No events declared"}</div>
    </div>
</div>

<script>
(function() {{
    var COLLECTIONS = {collections_payload};
    var ACTIONS = {actions_payload};
    var EVENTS = {events_payload};

    document.querySelectorAll('[data-stats-url]').forEach(function(el) {{
        var url = el.dataset.statsUrl;
        EOS.api(url).then(function(data) {{
            if (typeof data !== 'object' || Array.isArray(data)) {{
                el.innerHTML = '<pre style="font-size:12px">' +
                    EOS_UI.esc(JSON.stringify(data, null, 2).slice(0, 600)) + '</pre>';
                return;
            }}
            var items = Object.keys(data).map(function(k) {{
                var v = data[k];
                if (typeof v === 'object') return null;
                return {{label: k.replace(/_/g, ' '), value: v}};
            }}).filter(Boolean);
            EOS_UI.statCards(el.id, items);
        }}).catch(function(err) {{
            el.innerHTML = EOS_UI.errorState({{message: 'Stats failed: ' + err.message}});
        }});
    }});

    var lists = {{}};
    COLLECTIONS.forEach(function(c) {{
        lists[c.slug] = EOS_UI.entityList({{
            url: c.url,
            mountId: c.listId,
            deleteUrl: c.deleteUrl || null,
            emptyMessage: 'No ' + c.label.toLowerCase() + ' yet.',
        }});
        if (c.postUrl && c.addFields && c.addFields.length) {{
            window['autoUiAdd_' + c.slug] = function() {{
                EOS_UI.formModal('Add ' + c.label, c.addFields, function(values) {{
                    EOS.api(c.postUrl, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify(values),
                    }}).then(function(data) {{
                        if (data && data.error) {{ EOS_UI.toast(data.error, false); return; }}
                        EOS_UI.toast('Added');
                        lists[c.slug].reload();
                    }}).catch(function(err) {{
                        EOS_UI.toast('Add failed: ' + err.message, false);
                    }});
                }});
            }};
        }}
    }});

    ACTIONS.forEach(function(a) {{
        window['autoUiAction_' + a.slug] = function() {{
            EOS_UI.formModal(a.label, a.fields, function(values) {{
                EOS.api(a.url, {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify(values),
                }}).then(function(data) {{
                    var out = document.getElementById('auto-ui-output');
                    out.style.display = 'block';
                    out.textContent = JSON.stringify(data, null, 2);
                    EOS_UI.toast('Done');
                }}).catch(function(err) {{
                    EOS_UI.toast(a.label + ' failed: ' + err.message, false);
                }});
            }});
        }};
    }});

    window.autoUiData = function(url) {{
        var out = document.getElementById('auto-ui-output');
        out.style.display = 'block';
        out.textContent = 'Loading…';
        EOS.api(url).then(function(data) {{
            out.textContent = JSON.stringify(data, null, 2);
        }}).catch(function(err) {{
            out.textContent = 'Error: ' + err.message;
        }});
    }};

    var eventsDiv = document.getElementById('auto-ui-events');
    EVENTS.forEach(function(evt) {{
        EOS.on(evt, function(data, event) {{
            var item = document.createElement('div');
            var ts = (event && event.timestamp) ? event.timestamp.slice(11, 19) : '';
            item.textContent = ts + ' ' + event.type + ' ' +
                JSON.stringify(data).slice(0, 60);
            eventsDiv.prepend(item);
            while (eventsDiv.children.length > 15) eventsDiv.lastChild.remove();
        }});
    }});
}})();
</script>
</body>
</html>"""
