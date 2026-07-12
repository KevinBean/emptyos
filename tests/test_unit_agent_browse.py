"""Unit tests for the Browse agent tool — pure, no daemon, no real browser.

A fake app records every ``try_browse(action, **kwargs)`` call and returns a
canned provider-shaped dict, so we can pin: argument marshalling per action,
validation errors, the permission/plan-mode classifications, and the compact
content formatting — all without Playwright installed.
"""

from __future__ import annotations

import asyncio

from emptyos.sdk.agent_tools.browse import BrowseTool


class _FakeApp:
    """Records try_browse calls; returns a per-action canned result."""

    def __init__(self, result=None, ok: bool = True):
        self.calls: list[tuple[str, dict]] = []
        self._result = result if result is not None else {"ok": True}
        self._ok = ok

    async def try_browse(self, action: str, **kwargs):
        self.calls.append((action, kwargs))
        return (self._ok, self._result)


def _run(coro):
    return asyncio.run(coro)


# ── plan-mode (is_readonly) ──────────────────────────────────────────
def test_is_readonly_split():
    t = BrowseTool()
    for a in ("navigate", "screenshot", "snapshot", "wait_for"):
        assert t.is_readonly({"action": a}) is True, a
    for a in ("click", "fill", "eval", "close"):
        assert t.is_readonly({"action": a}) is False, a


# ── permission classification ────────────────────────────────────────
def test_permission_observation_verbs_auto():
    t = BrowseTool()
    for a in ("screenshot", "snapshot", "wait_for", "close"):
        assert t.permission_for({"action": a}) == "auto", a


def test_permission_navigate_localhost_vs_public():
    t = BrowseTool()
    assert t.permission_for({"action": "navigate", "url": "http://127.0.0.1:9000/"}) == "auto"
    assert t.permission_for({"action": "navigate", "url": "http://localhost:9000/x"}) == "auto"
    assert t.permission_for({"action": "navigate", "url": "https://example.com/"}) == "ask"


def test_permission_act_verbs_always_ask():
    t = BrowseTool()
    for a in ("click", "fill", "eval"):
        assert t.permission_for({"action": a}) == "ask", a


# ── validation ───────────────────────────────────────────────────────
def test_unknown_action_errors_without_calling_browse():
    app = _FakeApp()
    out = _run(BrowseTool().run(app, action="teleport"))
    assert not out.ok and "unknown action" in out.content
    assert app.calls == []  # never reached the capability


def test_navigate_requires_url_and_http_scheme():
    app = _FakeApp()
    miss = _run(BrowseTool().run(app, action="navigate"))
    assert not miss.ok and "requires 'url'" in miss.content
    bad = _run(BrowseTool().run(app, action="navigate", url="file:///etc/passwd"))
    assert not bad.ok and "scheme" in bad.content
    assert app.calls == []


def test_click_requires_selector():
    app = _FakeApp()
    out = _run(BrowseTool().run(app, action="click"))
    assert not out.ok and "requires 'selector'" in out.content
    assert app.calls == []


def test_eval_requires_expression():
    app = _FakeApp()
    out = _run(BrowseTool().run(app, action="eval"))
    assert not out.ok and "requires 'expression'" in out.content


# ── argument marshalling to the capability ───────────────────────────
def test_navigate_marshals_url_and_default_context():
    app = _FakeApp({"url": "http://x/", "title": "Home"})
    out = _run(BrowseTool().run(app, action="navigate", url="http://x/"))
    assert out.ok and "navigated" in out.content and "Home" in out.content
    action, kw = app.calls[0]
    assert action == "navigate"
    assert kw == {"context_id": "agent", "url": "http://x/"}


def test_fill_marshals_value_and_custom_context():
    app = _FakeApp({"ok": True})
    _run(BrowseTool().run(app, action="fill", selector="#q", value="milk", context_id="s1"))
    action, kw = app.calls[0]
    assert action == "fill"
    assert kw == {"context_id": "s1", "selector": "#q", "value": "milk"}


def test_wait_for_passes_state_only_when_given():
    app = _FakeApp({"ok": True})
    _run(BrowseTool().run(app, action="wait_for", selector="#x"))
    _run(BrowseTool().run(app, action="wait_for", selector="#x", state="hidden"))
    assert "state" not in app.calls[0][1]
    assert app.calls[1][1]["state"] == "hidden"


def test_screenshot_passes_full_page_and_selector():
    app = _FakeApp({"path": "/tmp/shot.png"})
    out = _run(BrowseTool().run(app, action="screenshot", full_page=True, selector="#panel"))
    assert out.ok and "/tmp/shot.png" in out.content
    kw = app.calls[0][1]
    assert kw["full_page"] is True and kw["selector"] == "#panel"


# ── observation formatting ───────────────────────────────────────────
def test_snapshot_truncates_and_reports_lengths():
    long_text = "x" * 9000
    app = _FakeApp({"text": long_text, "html": "<p>x</p>", "url": "http://x/", "title": "T"})
    out = _run(BrowseTool().run(app, action="snapshot"))
    assert out.ok
    assert "truncated from 9000 chars" in out.content
    assert out.display["text_chars"] == 9000
    assert out.display["html_chars"] == len("<p>x</p>")


def test_eval_renders_value():
    app = _FakeApp({"value": {"count": 3}})
    out = _run(BrowseTool().run(app, action="eval", expression="({count:3})"))
    assert out.ok and '"count": 3' in out.content


# ── error passthrough ────────────────────────────────────────────────
def test_provider_missing_gives_install_hint():
    app = _FakeApp("No available provider for capability 'browse'", ok=False)
    out = _run(BrowseTool().run(app, action="snapshot"))
    assert not out.ok
    assert "playwright install chromium" in out.content
