"""Auto-UI generator — every app gets a functional web interface.

Reads an app's web routes and generates usable HTML:
- GET returning object → key-value card
- GET returning array → table/list
- GET /api/stats → stat cards
- POST taking {text} → text input + submit
- POST taking JSON → smart form
- DELETE → button with confirmation

The UI is functional, not just API documentation.
"""

from __future__ import annotations

import html as _html
import json as _json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.kernel.app_loader import AppManifest


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
    return "other"


def _guess_fields(path: str) -> list[dict]:
    """Guess input fields from route path and common patterns."""
    p = path.lower()
    if "log" in p or "add" in p or "capture" in p:
        return [{"name": "text", "type": "text", "placeholder": "Type something..."}]
    if "generate" in p:
        if "lesson" in p:
            return [
                {"name": "topic", "type": "text", "placeholder": "e.g., ordering food at a restaurant"},
                {"name": "difficulty", "type": "select", "options": ["beginner", "intermediate", "advanced"]},
            ]
        if "sentence" in p or "shadow" in p:
            return [
                {"name": "difficulty", "type": "select", "options": ["beginner", "intermediate", "advanced"]},
                {"name": "count", "type": "number", "placeholder": "5", "default": "5"},
            ]
        return [{"name": "prompt", "type": "text", "placeholder": "Describe what to generate..."}]
    if "start" in p:
        if "session" in p:
            return [{"name": "scenario", "type": "text", "placeholder": "e.g., free, interview, daily"}]
        return [{"name": "text", "type": "text", "placeholder": "Start..."}]
    if "search" in p or "smart-search" in p or "find" in p:
        return [{"name": "query", "type": "text", "placeholder": "Search..."}]
    if "chat" in p or "message" in p:
        return [{"name": "text", "type": "text", "placeholder": "Type a message..."}]
    if "calculate" in p or "quick" in p:
        return [
            {"name": "cable_weight", "type": "number", "placeholder": "kg/m"},
            {"name": "length", "type": "number", "placeholder": "meters"},
            {"name": "bends", "type": "number", "placeholder": "number of bends", "default": "2"},
        ]
    if "score" in p:
        return [
            {"name": "target", "type": "text", "placeholder": "Target sentence"},
            {"name": "spoken", "type": "text", "placeholder": "What you said"},
        ]
    return [{"name": "text", "type": "text", "placeholder": "Input..."}]


def _render_field(field: dict, form_id: str) -> str:
    fid = f"{form_id}-{field['name']}"
    if field.get("type") == "select":
        opts = "".join(f'<option value="{o}">{o}</option>' for o in field.get("options", []))
        return f'<select id="{fid}" class="form-select">{opts}</select>'
    elif field.get("type") == "number":
        return f'<input id="{fid}" type="number" class="form-input" placeholder="{field.get("placeholder", "")}" value="{field.get("default", "")}">'
    else:
        return f'<input id="{fid}" type="text" class="form-input" placeholder="{field.get("placeholder", "")}">'


def generate_app_page(manifest: "AppManifest", routes: list[dict]) -> str:
    """Generate a functional HTML page from manifest + routes."""
    app_id = _html.escape(manifest.id)
    app_name = _html.escape(manifest.name)
    description = _html.escape(manifest.description)
    prefix = manifest.provides.get("web", {}).get("prefix", "")
    events_emitted = manifest.events_emits
    capabilities = manifest.requires.get("capabilities", [])
    connectors = manifest.requires.get("connectors", [])

    # Classify routes
    gets_stats = []
    gets_lists = []
    gets_data = []
    posts = []
    deletes = []
    for r in routes:
        method = r["method"].upper()
        path = r["path"]
        kind = _classify_route(method, path)
        full = prefix + path
        if method == "GET":
            if kind == "stats":
                gets_stats.append((path, full))
            elif kind in ("list", "heatmap"):
                gets_lists.append((path, full, kind))
            elif kind != "detail":
                gets_data.append((path, full))
        elif method == "POST":
            posts.append((path, full, kind))
        elif method == "DELETE":
            deletes.append((path, full))

    # Build sections
    sections = ""

    # Stats cards — auto-load on page open
    if gets_stats:
        cards = ""
        for path, full in gets_stats:
            div_id = f"stat-{path.replace('/', '-')}"
            cards += f'<div class="card stat-card" id="{div_id}" data-url="{full}">Loading...</div>\n'
        sections += f"""
        <div class="section-title">Overview</div>
        <div class="stat-grid">{cards}</div>"""

    # Action forms — for POST endpoints
    if posts:
        forms = ""
        for path, full, kind in posts:
            if "{" in path:
                continue  # skip parameterized routes
            form_id = f"form{path.replace('/', '-')}"
            label = path.split("/")[-1].replace("-", " ").replace("_", " ").title()
            fields = _guess_fields(path)
            field_html = ""
            field_names = []
            for f in fields:
                field_html += f'<div class="form-group"><label>{f["name"]}</label>{_render_field(f, form_id)}</div>\n'
                field_names.append(f["name"])

            forms += f"""
            <div class="card action-card fade-in">
                <div class="action-label">{label}</div>
                {field_html}
                <button class="btn" onclick="submitForm('{full}', '{form_id}', {_json.dumps(field_names)})">
                    {label}
                </button>
                <div class="action-result" id="{form_id}-result"></div>
            </div>"""
        if forms:
            sections += f"""
            <div class="section-title">Actions</div>
            <div class="action-grid">{forms}</div>"""

    # List views — auto-load
    if gets_lists:
        for path, full, kind in gets_lists:
            div_id = f"list-{path.replace('/', '-')}"
            label = path.split("/")[-1].replace("-", " ").replace("_", " ").title()
            sections += f"""
            <div class="section-title">{label}</div>
            <div class="card" id="{div_id}" data-url="{full}" data-type="{kind}">Loading...</div>"""

    # Other GET endpoints
    if gets_data:
        cards = ""
        for path, full in gets_data:
            label = path.split("/")[-1].replace("-", " ").replace("_", " ").title()
            div_id = f"data-{path.replace('/', '-')}"
            cards += f'<a class="card data-link" onclick="loadData(\'{full}\', \'{div_id}\')">{label}</a>\n'
        sections += f"""
        <div class="section-title">Data</div>
        <div class="data-grid">{cards}</div>"""

    # Badges
    badges = ""
    for cap in capabilities:
        badges += f'<span class="badge badge-cap">{cap}</span> '
    for conn in connectors:
        badges += f'<span class="badge badge-conn">{conn}</span> '

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{app_name} - EmptyOS</title>
<link rel="stylesheet" href="/static/theme.css">
<style>
.stat-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; }}
.stat-card {{ text-align: center; padding: 16px 12px; }}
.stat-val {{ font-size: 1.6rem; font-weight: 700; color: var(--text-heading); }}
.stat-label {{ font-size: 0.75rem; color: var(--text-muted); margin-top: 2px; }}
.action-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }}
.action-card {{ padding: 16px; }}
.action-label {{ font-weight: 600; margin-bottom: 10px; color: var(--text-heading); }}
.action-result {{ margin-top: 8px; font-size: 0.85rem; color: var(--text-secondary); max-height: 200px; overflow-y: auto; white-space: pre-wrap; font-family: var(--mono); }}
.data-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 8px; }}
.data-link {{ cursor: pointer; text-align: center; padding: 12px; font-weight: 500; }}
.data-link:hover {{ border-color: var(--accent); }}
.form-group {{ margin-bottom: 8px; }}
.form-group label {{ display: block; font-size: 0.8rem; color: var(--text-muted); margin-bottom: 3px; }}
.form-input, .form-select {{ width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--text); font-size: 0.9rem; }}
.btn {{ padding: 8px 16px; background: var(--accent); color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 0.9rem; }}
.btn:hover {{ opacity: 0.9; }}
.list-item {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 0.9rem; }}
.output-box {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-top: 12px; max-height: 400px; overflow-y: auto; white-space: pre-wrap; font-family: var(--mono); font-size: 0.85rem; display: none; }}
</style>
</head>
<body>
<script src="/static/realtime.js"></script>
<script src="/static/eos.js"></script>
<script>EOS.nav('{app_id}');</script>

<div class="page">
    <div class="page-header">
        <h1>{app_name}</h1>
        <div class="desc">{description}</div>
        <div style="margin-top:8px">{badges}</div>
    </div>

    {sections}

    <div id="output" class="output-box"></div>

    <div class="section-title" style="margin-top:20px">Live</div>
    <div id="events" style="font-size:12px;color:var(--text-muted)">Listening...</div>
</div>

<script>
var output = document.getElementById('output');

// Auto-load stats
document.querySelectorAll('.stat-card').forEach(async function(el) {{
    try {{
        var data = await EOS.api(el.dataset.url);
        if (typeof data === 'object' && !Array.isArray(data)) {{
            var keys = Object.keys(data);
            if (keys.length <= 6) {{
                // Render as stat pairs
                el.innerHTML = keys.map(function(k) {{
                    var v = data[k];
                    if (typeof v === 'object') return '';
                    return '<div><div class="stat-val">' + v + '</div><div class="stat-label">' + k.replace(/_/g, ' ') + '</div></div>';
                }}).join('');
            }} else {{
                el.innerHTML = '<pre style="font-size:12px;text-align:left">' + JSON.stringify(data, null, 2).slice(0, 500) + '</pre>';
            }}
        }} else {{
            el.textContent = JSON.stringify(data).slice(0, 100);
        }}
    }} catch(e) {{
        el.textContent = 'Error loading';
        el.style.color = 'var(--text-muted)';
    }}
}});

// Auto-load lists
document.querySelectorAll('[data-type="list"], [data-type="heatmap"]').forEach(async function(el) {{
    try {{
        var data = await EOS.api(el.dataset.url);
        if (Array.isArray(data)) {{
            if (data.length === 0) {{
                el.innerHTML = '<div style="color:var(--text-muted);padding:8px">No items yet</div>';
                return;
            }}
            // Render first 20 items
            var PATH_KEYS = ['file', 'path', 'source_file', 'note_path'];
            var keys = Object.keys(data[0]).filter(function(k) {{ return typeof data[0][k] !== 'object'; }}).slice(0, 4);
            var rows = data.slice(0, 20).map(function(item) {{
                return '<div class="list-item">' + keys.map(function(k) {{
                    var v = String(item[k] || '');
                    if (PATH_KEYS.indexOf(k) !== -1 && v.endsWith('.md')) return '<span>' + EOS.noteActions(v) + '</span>';
                    return '<span>' + v.slice(0, 40) + '</span>';
                }}).join('') + '</div>';
            }}).join('');
            el.innerHTML = '<div style="font-size:12px;color:var(--text-muted);margin-bottom:4px">' + data.length + ' items</div>' + rows;
        }} else {{
            el.innerHTML = '<pre style="font-size:12px">' + JSON.stringify(data, null, 2).slice(0, 800) + '</pre>';
        }}
    }} catch(e) {{
        el.innerHTML = '<div style="color:var(--text-muted)">Error loading</div>';
    }}
}});

// Submit form
async function submitForm(url, formId, fields) {{
    var body = {{}};
    fields.forEach(function(f) {{
        var el = document.getElementById(formId + '-' + f);
        if (el) body[f] = el.type === 'number' ? Number(el.value) : el.value;
    }});
    var resultEl = document.getElementById(formId + '-result');
    resultEl.textContent = 'Sending...';
    try {{
        var data = await EOS.api(url, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(body),
        }});
        resultEl.textContent = JSON.stringify(data, null, 2);
    }} catch(e) {{
        resultEl.textContent = 'Error: ' + e.message;
    }}
}}

// Load data into output box
async function loadData(url, id) {{
    output.style.display = 'block';
    output.textContent = 'Loading...';
    try {{
        var data = await EOS.api(url);
        output.textContent = JSON.stringify(data, null, 2);
    }} catch(e) {{
        output.textContent = 'Error: ' + e.message;
    }}
}}

// Live events
var eventsDiv = document.getElementById('events');
var appEvents = {_json.dumps(events_emitted)};
if (appEvents.length > 0) {{
    appEvents.forEach(function(evt) {{
        EOS.on(evt, function(data, event) {{
            var item = document.createElement('div');
            item.style.cssText = 'padding:3px 0;border-bottom:1px solid var(--border)';
            item.textContent = event.timestamp.slice(11,19) + ' ' + event.type + ' ' + JSON.stringify(data).slice(0,60);
            eventsDiv.prepend(item);
            if (eventsDiv.children.length > 15) eventsDiv.lastChild.remove();
        }});
    }});
    eventsDiv.textContent = 'Listening: ' + appEvents.join(', ');
}} else {{
    eventsDiv.textContent = 'No events declared';
}}
</script>
</body>
</html>"""
