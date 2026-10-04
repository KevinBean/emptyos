"""Auto-UI generator tests — verify the rewrite emits a usable page.

These tests don't need the daemon. They invoke generate_app_page() directly
with synthetic manifest + route shapes and assert on the emitted HTML.

The generator is at emptyos/web/auto_ui.py. Auto-UI is the default for new
apps that don't ship pages/index.html (per CLAUDE.md Rule 7). These tests
are the mechanical guarantee that the generator stays bundle-driven and
secure as it evolves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from emptyos.web.auto_ui import (
    _classify_route,
    _detect_collections,
    _fields_to_modal_fields,
    _guess_fields,
    generate_app_page,
)


@dataclass
class FakeManifest:
    """Stand-in for AppManifest with the fields auto_ui reads."""

    id: str = "demo"
    name: str = "Demo"
    description: str = "A demo app"
    provides: dict = field(default_factory=lambda: {"web": {"prefix": "/demo"}})
    requires: dict = field(default_factory=lambda: {"capabilities": []})
    events_emits: list = field(default_factory=list)


def _crud_routes() -> list[dict]:
    return [
        {"method": "GET", "path": "/api/items"},
        {"method": "POST", "path": "/api/items"},
        {"method": "GET", "path": "/api/items/{id}"},
        {"method": "PUT", "path": "/api/items/{id}"},
        {"method": "DELETE", "path": "/api/items/{id}"},
        {"method": "GET", "path": "/api/stats"},
    ]


# ── Bundle linkage ──────────────────────────────────────────────


def test_bundle_loaded():
    html = generate_app_page(FakeManifest(), _crud_routes())
    assert "/static/eos-components.js" in html
    assert "/static/eos-components.css" in html
    assert "/static/eos-keys.js" in html
    assert "/static/theme.css" in html


def test_no_legacy_btn_class():
    html = generate_app_page(FakeManifest(), _crud_routes())
    assert 'class="btn"' not in html
    assert 'class="btn-primary"' not in html


def test_no_inline_stat_grid_css():
    html = generate_app_page(FakeManifest(), _crud_routes())
    # Auto-UI used to ship a hand-rolled .stat-grid rule. The rewrite uses
    # EOS_UI.statCards instead — the inline class definition is gone.
    assert ".stat-grid {" not in html
    assert ".stat-card {" not in html


# ── EmptyOS chrome integration ──────────────────────────────────


def test_data_app_id_attribute():
    html = generate_app_page(FakeManifest(id="widget"), _crud_routes())
    assert 'data-app-id="widget"' in html


# ── CRUD detection + rendering ──────────────────────────────────


def test_crud_triple_detected():
    cols, consumed = _detect_collections([
        {"method": "GET", "path": "/api/items", "full": "/demo/api/items"},
        {"method": "GET", "path": "/api/items/{id}", "full": "/demo/api/items/{id}"},
        {"method": "DELETE", "path": "/api/items/{id}", "full": "/demo/api/items/{id}"},
        {"method": "POST", "path": "/api/items", "full": "/demo/api/items"},
    ])
    assert len(cols) == 1
    coll = cols[0]
    assert coll["base"] == "/api/items"
    assert coll["delete_full"] == "/demo/api/items"   # /{id} stripped
    assert coll["post_full"] == "/demo/api/items"
    assert len(consumed) == 4


def test_collection_renders_entity_list_call():
    html = generate_app_page(FakeManifest(), _crud_routes())
    assert "EOS_UI.entityList(" in html


def test_collection_payload_contains_delete_url():
    html = generate_app_page(FakeManifest(), _crud_routes())
    assert '"deleteUrl": "/demo/api/items"' in html


def test_collection_renders_add_button():
    html = generate_app_page(FakeManifest(), _crud_routes())
    assert "+ Add" in html
    assert "autoUiAdd_" in html


def test_bare_list_is_not_a_collection():
    """A GET /api/items with no DELETE/POST/detail companion is just a list."""
    cols, _ = _detect_collections([
        {"method": "GET", "path": "/api/items", "full": "/demo/api/items"},
    ])
    assert cols == []


# ── Stats ──────────────────────────────────────────────────────


def test_stats_uses_helper():
    html = generate_app_page(FakeManifest(), [
        {"method": "GET", "path": "/api/stats"},
    ])
    assert "EOS_UI.statCards(" in html
    assert 'data-stats-url="/demo/api/stats"' in html


def test_empty_routes_renders_safely():
    html = generate_app_page(FakeManifest(), [])
    assert "<html" in html and "</html>" in html
    assert "no auto-renderable routes" in html


# ── Escaping (XSS posture) ──────────────────────────────────────


def test_path_escaped_no_script_breakout():
    """Route paths must not allow script-tag breakout.

    `</script>` literal inside a JSON-payload string embedded in <script>
    would close the tag early. The safe storage form is `<\\/script>`.
    """
    routes = [{"method": "POST", "path": "/api/<script>alert(1)</script>"}]
    html = generate_app_page(FakeManifest(), routes)
    assert "alert(1)</script>" not in html
    assert "alert(1)<\\/script>" in html


def test_app_name_escaped():
    html = generate_app_page(FakeManifest(name="<img src=x onerror=alert(1)>"), [])
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_description_escaped():
    html = generate_app_page(FakeManifest(description='" onload="alert(1)'), [])
    assert 'onload="alert(1)' not in html


# ── Field heuristic preserved ───────────────────────────────────


def test_guess_fields_unchanged_for_log():
    fields = _guess_fields("/api/log")
    assert fields[0]["name"] == "text"


def test_fields_to_modal_fields_normalizes_label():
    out = _fields_to_modal_fields([{"name": "user_name", "type": "text"}])
    assert out[0]["label"] == "User Name"
    assert out[0]["type"] == "text"


# ── Standalone POST (action-only app) ───────────────────────────


def test_standalone_post_renders_action_card():
    html = generate_app_page(FakeManifest(), [
        {"method": "POST", "path": "/api/generate"},
    ])
    assert "autoUiAction_" in html
    assert "Run" in html
    assert "EOS_UI.formModal" in html


# ── Drift on the rewrite's output (meta-test) ───────────────────


def test_generated_html_drift_free():
    """The rewrite's output should not emit legacy classes or raw dialogs."""
    import re
    html = generate_app_page(FakeManifest(), _crud_routes())

    bad_btn = re.compile(r'class\s*=\s*"[^"]*(?<![\w-])btn-(primary|secondary|danger|ghost|sm|lg)\b')
    assert not bad_btn.search(html), "auto-UI emits legacy btn-* class"

    raw_dialog = re.compile(r"(?<![.\w])(alert|confirm|prompt)\s*\(")
    for m in raw_dialog.finditer(html):
        prefix = html[max(0, m.start() - 8):m.start()]
        assert prefix.endswith("EOS_UI.") or "." in prefix, \
            f"auto-UI calls raw {m.group(1)}( at {m.start()}"


# ── Classify route smoke ────────────────────────────────────────


@pytest.mark.parametrize("method,path,expected", [
    ("GET", "/api/stats", "stats"),
    ("GET", "/api/items", "list"),
    ("GET", "/api/items/{id}", "detail"),
    ("POST", "/api/add", "action"),
    ("DELETE", "/api/items/{id}", "delete"),
    ("PUT", "/api/items/{id}", "update"),
])
def test_classify_route(method, path, expected):
    assert _classify_route(method, path) == expected
