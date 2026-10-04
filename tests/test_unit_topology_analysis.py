"""Pure regression tests for topology analysis helpers."""

from __future__ import annotations

from types import SimpleNamespace

from emptyos.web.server import _app_dependency_cycles


def _kernel(**architecture):
    manifests = {
        app_id: SimpleNamespace(raw={"architecture": values})
        for app_id, values in architecture.items()
    }
    return SimpleNamespace(apps=SimpleNamespace(manifests=manifests))


def _nodes(*app_ids):
    return {f"app:{app_id}": {"type": "app"} for app_id in app_ids}


def _edge(source, target, edge_type="calls_app"):
    return {"source": f"app:{source}", "target": f"app:{target}", "type": edge_type}


def test_cycle_detector_excludes_downstream_dependents():
    kernel = _kernel(a={}, b={}, consumer={})
    cycles = _app_dependency_cycles(
        kernel,
        _nodes("a", "b", "consumer"),
        [_edge("a", "b"), _edge("b", "a"), _edge("consumer", "a")],
    )
    assert len(cycles) == 1
    assert cycles[0]["nodes"] == ["app:a", "app:b"]
    assert cycles[0]["intentional"] is False


def test_cycle_detector_marks_shared_architecture_label_intentional():
    label = {"dependency_cycle": "test-fix-verify-loop"}
    kernel = _kernel(fix=label, dogfood=label)
    cycles = _app_dependency_cycles(
        kernel,
        _nodes("fix", "dogfood"),
        [_edge("fix", "dogfood"), _edge("dogfood", "fix")],
    )
    assert cycles == [
        {
            "nodes": ["app:dogfood", "app:fix"],
            "description": "2 apps in dependency cycle",
            "intentional": True,
            "label": "test-fix-verify-loop",
        }
    ]


def test_cycle_detector_ignores_optional_edges():
    kernel = _kernel(a={}, b={})
    cycles = _app_dependency_cycles(
        kernel,
        _nodes("a", "b"),
        [_edge("a", "b"), _edge("b", "a", "optional_calls_app")],
    )
    assert cycles == []
