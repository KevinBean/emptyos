"""CreateArtifact — the chat's "the answer is a thing" tool (B4).

The tool owns validation and the hand-off; viz owns storage. These fail if a
chat could choose where an artifact lands, if a refusal were swallowed into a
success, or if the panel's payload (display.artifact) stopped carrying the id
the panel needs.
"""

from __future__ import annotations

import asyncio

import pytest

from emptyos.sdk.agent_tools import build_registry
from emptyos.sdk.agent_tools.artifact import (
    CHAT_SHAPES,
    DEFAULT_SHAPE,
    MAX_HTML_BYTES,
    CreateArtifactTool,
)

HTML = "<!doctype html><html><body><h1>Q3</h1></body></html>"

#: Shaped like viz.save_artifact's real return. The `updated` timestamp is
#: deliberate: a boolean flag of that name would be shadowed by it, which is
#: exactly how a first save once reported itself as an update.
VIZ_OK = {"ok": True, "id": "ab12cd34", "revised": False,
          "updated": "2026-01-02T00:00:00Z", "created": "2026-01-01T00:00:00Z"}


class FakeApp:
    """Stands in for AgentApp: records the call_app it received."""

    def __init__(self, answer=None, raises=None):
        self.calls = []
        self._answer = answer if answer is not None else VIZ_OK
        self._raises = raises

    async def call_app(self, app_id, method, **kwargs):
        self.calls.append((app_id, method, kwargs))
        if self._raises:
            raise self._raises
        return self._answer


def run(tool, app, **kw):
    return asyncio.run(tool.run(app, **kw))


@pytest.fixture
def tool():
    return CreateArtifactTool()


def test_a_saved_artifact_reports_the_id_the_panel_opens(tool):
    app = FakeApp(dict(VIZ_OK))
    res = run(tool, app, title="Q3 budget", html=HTML, shape="chart")
    assert res.ok
    assert res.display["artifact"] == {
        "id": "ab12cd34", "title": "Q3 budget", "shape": "chart",
        "updated": False, "url": "/viz/api/html/ab12cd34",
    }
    # The id is in the model-facing text too: it is how the next turn revises it.
    assert "ab12cd34" in res.content


def test_the_page_goes_to_viz_and_the_chat_picks_no_path(tool):
    app = FakeApp()
    run(tool, app, title="T", html=HTML)
    (app_id, method, kwargs) = app.calls[0]
    assert (app_id, method) == ("viz", "save_artifact")
    assert kwargs["content"] == HTML and kwargs["title"] == "T"
    # Nothing in the call names a file, a folder or a vault path.
    assert set(kwargs) == {"content", "title", "shape", "source", "rid"}
    assert kwargs["source"] == "agent"


def test_revising_passes_the_id_through_so_the_old_render_is_kept(tool):
    app = FakeApp(dict(VIZ_OK, revised=True))
    res = run(tool, app, title="T", html=HTML, artifact_id="ab12cd34")
    assert app.calls[0][2]["rid"] == "ab12cd34"
    assert res.display["artifact"]["updated"] is True
    assert res.content.startswith("Updated")


def test_a_refusal_from_viz_is_a_failure_not_a_silent_success(tool):
    app = FakeApp({"ok": False, "error": "no such artifact"})
    res = run(tool, app, title="T", html=HTML, artifact_id="gone")
    assert not res.ok and "no such artifact" in res.content


def test_viz_missing_is_reported_rather_than_raised(tool):
    """The chat profile runs wherever the user's store gate left viz — an
    absent app must read as an answer, not crash the turn."""
    app = FakeApp(raises=RuntimeError("app 'viz' not found"))
    res = run(tool, app, title="T", html=HTML)
    assert not res.ok and "viz" in res.content


def test_an_unknown_shape_is_named_not_rewritten(tool):
    res = run(tool, FakeApp(), title="T", html=HTML, shape="3d-scene")
    assert not res.ok
    assert "3d-scene" in res.content and "chart" in res.content


def test_the_empty_and_oversized_cases_are_refused_before_viz(tool):
    app = FakeApp()
    assert not run(tool, app, title="", html=HTML).ok
    assert not run(tool, app, title="T", html="   ").ok
    big = run(tool, app, title="T", html="<!doctype html>" + "x" * MAX_HTML_BYTES)
    assert not big.ok and str(MAX_HTML_BYTES) in big.content
    assert app.calls == []          # nothing reached viz


def test_the_ceiling_stays_under_vizs_and_counts_bytes(tool):
    """Above viz's own cap the check inverts its purpose: the model gets viz's
    refusal ("regenerate, don't iterate") naming verbs this tool lacks."""
    assert MAX_HTML_BYTES <= 256 * 1024
    # A CJK page is ~3x its character count in bytes; a character-counting
    # ceiling would let it through and viz would be the one to refuse.
    payload = "<!doctype html><body>" + "字" * ((MAX_HTML_BYTES // 3) + 10)
    assert len(payload) < MAX_HTML_BYTES < len(payload.encode("utf-8"))
    res = run(tool, FakeApp(), title="T", html=payload)
    assert not res.ok and "over the" in res.content


def test_it_is_never_readonly_so_plan_mode_holds(tool):
    # Plan mode gates on is_readonly: an artifact is a new vault note.
    assert tool.is_readonly({}) is False and tool.readonly is False


def test_the_permission_line_says_new_versus_revise(tool):
    assert "save a new artifact" in tool.permission_summary({"title": "T", "html": HTML})
    assert "revise artifact ab12cd34" in tool.permission_summary({"artifact_id": "ab12cd34"})


def test_every_shape_the_tool_offers_is_one_viz_can_store(tool):
    """The drift that actually bites: the tool advertises a shape in its schema,
    the model obeys, and viz refuses it with "unknown shape". Comparing the enum
    to CHAT_SHAPES cannot catch that — the schema is BUILT from CHAT_SHAPES, so
    that assertion can never go red."""
    import tomllib
    from pathlib import Path

    viz_shared = Path(__file__).resolve().parent.parent / "apps/public/standard/viz/shared.py"
    src = viz_shared.read_text(encoding="utf-8")
    # PRESETS is a dict literal of shape -> prompt; read its keys without
    # importing viz (which pulls the SDK and a kernel-shaped app).
    import ast

    tree = ast.parse(src)
    presets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "PRESETS" for t in node.targets
        ):
            presets = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    assert presets, "could not read viz PRESETS — the shape source moved"
    unknown = sorted(set(CHAT_SHAPES) - presets)
    assert not unknown, f"tool offers shapes viz cannot store: {unknown}"
    assert DEFAULT_SHAPE in presets


def test_the_registry_offers_it(tool):
    assert "CreateArtifact" in build_registry()


def test_a_no_op_save_tells_the_model_to_stop_rather_than_saying_updated(tool):
    """The loop this closes was real: 25 byte-identical calls in one turn, each
    answered "Updated artifact …", which reads as progress. A no-op has to say
    so and say what to do instead."""
    app = FakeApp(dict(VIZ_OK, unchanged=True, title="Stored title"))
    res = run(tool, app, title="T", html=HTML, artifact_id="ab12cd34")
    assert res.ok
    assert "No change" in res.content
    assert "do not call" in res.content.lower()
    assert "Updated" not in res.content
    # The panel still gets its payload — the artifact exists and is showable.
    assert res.display["artifact"]["id"] == "ab12cd34"
    assert res.display["artifact"]["updated"] is False


def test_a_real_save_is_terminal_too(tool):
    """Even a successful save has to end the loop: the previous wording ended
    with "pass artifact_id=… to revise it", which reads as an instruction."""
    res = run(tool, FakeApp(), title="T", html=HTML)
    assert "do NOT call CreateArtifact again" in res.content
    assert "only if the user asks for a change" in res.content.lower()
