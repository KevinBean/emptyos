"""requirements: editable citations and the project list.

Daemon-free. `_coerce_references` is the write-boundary normaliser for the
newly settable `references` field; `api_items` must offer a project that has a
client document but no requirement yet, or that project's compliance matrix
cannot be reached from the page.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from helpers import load_app_module

req = load_app_module("requirements", "app")


def test_references_from_a_comma_string_are_trimmed_and_deduplicated():
    assert req._coerce_references(" A §1 , B ,, A §1") == ["A §1", "B"]


def test_a_citation_with_a_comma_survives_the_editor_round_trip():
    """The detail editor sends one citation per line; a comma inside one
    ("…:2017, Table 3") must not split it."""
    assert req._coerce_references("AS/NZS 3008.1.1:2017, Table 3\nIEC 60287-1-1 §2") == \
        ["AS/NZS 3008.1.1:2017, Table 3", "IEC 60287-1-1 §2"]
    assert req._coerce_references(["AS/NZS 3008.1.1:2017, Table 3", ""]) == ["AS/NZS 3008.1.1:2017, Table 3"]


def test_references_from_a_list_keep_only_scalars():
    assert req._coerce_references(["A", " B ", {"x": 1}, 3]) == ["A", "B", "3"]


def test_references_of_another_shape_are_dropped():
    assert req._coerce_references(None) == [] and req._coerce_references(7) == []


def test_references_is_a_settable_field():
    assert "references" in req.RequirementsApp.SETTABLE_FIELDS


def test_set_field_stores_references_as_a_list():
    app = req.RequirementsApp.__new__(req.RequirementsApp)
    stored = {}
    app._find = lambda rid: {"path": "p.md", "properties": {}}

    class _Lock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    app.note_lock = lambda path: _Lock()
    app.vault_update = lambda path, props: stored.update(props)

    async def emit(*a, **k):
        pass

    app.emit = emit
    out = asyncio.run(app.set_field("x", "references", "A §1, B"))
    assert out == {"ok": True}
    assert stored["references"] == ["A §1", "B"]


def test_items_lists_a_project_that_only_has_a_source_document():
    app = req.RequirementsApp.__new__(req.RequirementsApp)

    async def list_all():
        return [{"project": "has-reqs", "status": "proposed"}]

    def vault_query(tags=None, **kw):
        if tags == [req.SOURCE_TAG]:
            return [{"properties": {"project": " sources-only "}}, {"properties": {}},
                    {"properties": {"project": "has-reqs"}}]
        return []

    app.list_all = list_all
    app.vault_query = vault_query
    request = SimpleNamespace(query_params={})
    out = asyncio.run(req.RequirementsApp.api_items(app, request))
    assert out["projects"] == ["has-reqs", "sources-only"]
