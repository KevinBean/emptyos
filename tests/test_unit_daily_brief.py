"""Unit tests for daily-brief feed parsing — pure functions, no daemon.

Covers RSS 2.0, Atom, JSON Feed, HTML-stripping, and the entity-bomb hardening
(defusedxml refuses DTDs/entity expansion → fail-soft empty list).
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import types
from datetime import date, timedelta

import pytest

from helpers import app_path

# Resolve via the track-tree scanner — daily-brief moved labs/ -> standard/ and
# a hardcoded path silently breaks every test in this module.
_APP_DIR = app_path("daily-brief")
_APP = _APP_DIR / "app.py"

# Package name the live loader uses (emptyos/kernel/app_loader.py → eos_apps.<id>),
# so app.py's `from . import feeds` / `from .shared import ...` resolve.
_PKG = "eos_apps.daily-brief"


@pytest.fixture(scope="module")
def mod():
    # Mirror the runtime loader: register the parent + app package (with __path__)
    # so the daily-brief multi-module decomposition's relative imports resolve when
    # loading app.py standalone. See .claude/rules/multi-module-apps.md.
    if "eos_apps" not in sys.modules:
        parent = types.ModuleType("eos_apps")
        parent.__path__ = []
        sys.modules["eos_apps"] = parent
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_APP_DIR)]
        pkg.__package__ = _PKG
        sys.modules[_PKG] = pkg

    # Preload helpers in dependency order (shared has no relative deps).
    for sub in ("shared", "articles", "feeds", "sources", "markets", "snapshots"):
        name = f"{_PKG}.{sub}"
        if name in sys.modules:
            continue
        sub_spec = importlib.util.spec_from_file_location(name, _APP_DIR / f"{sub}.py")
        sub_mod = importlib.util.module_from_spec(sub_spec)
        sub_mod.__package__ = _PKG
        sys.modules[name] = sub_mod
        sub_spec.loader.exec_module(sub_mod)

    spec = importlib.util.spec_from_file_location(f"{_PKG}.app", _APP)
    m = importlib.util.module_from_spec(spec)
    m.__package__ = _PKG
    sys.modules[f"{_PKG}.app"] = m
    spec.loader.exec_module(m)
    return m


def test_parse_rss(mod):
    rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>
    <item><title>Grid battery online &amp; dispatching</title>
    <link>https://reneweconomy.com.au/x?a=1&amp;b=2</link>
    <description>&lt;p&gt;A 300MW &lt;b&gt;BESS&lt;/b&gt; started.&lt;/p&gt;</description></item>
    <item><title>Second</title><link>https://example.com/2</link></item></channel></rss>"""
    items = mod.DailyBriefApp._parse_feed(rss)
    assert len(items) == 2
    assert items[0]["url"] == "https://reneweconomy.com.au/x?a=1&b=2"
    assert "BESS" in items[0]["summary"] and "<" not in items[0]["summary"]


def test_parse_atom(mod):
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>V</title>
    <entry><title>Atom headline</title>
    <link rel="alternate" href="https://theverge.com/a"/>
    <summary>Some &amp; summary</summary></entry></feed>"""
    items = mod.DailyBriefApp._parse_feed(atom)
    assert items and items[0]["url"] == "https://theverge.com/a"
    assert items[0]["title"] == "Atom headline"


def test_parse_json_feed(mod):
    js = '{"items":[{"title":"JSON item","url":"https://x.com/j","summary":"hi"},{"bad":1}]}'
    items = mod.DailyBriefApp._parse_json(js)
    assert len(items) == 1 and items[0]["title"] == "JSON item"


def test_malformed_returns_empty(mod):
    assert mod.DailyBriefApp._parse_feed("not xml at all") == []
    assert mod.DailyBriefApp._parse_json("{not json") == []


def test_entity_bomb_is_refused(mod):
    """A billion-laughs / XXE-shaped feed must never expand — fail soft to []."""
    bomb = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "AAAA">'
            '<!ENTITY b "&a;&a;&a;">]><rss><channel><item>'
            '<title>&b;</title><link>http://x</link></item></channel></rss>')
    assert mod.DailyBriefApp._parse_feed(bomb) == []


def test_strip_html(mod):
    assert mod._strip_html("<p>hello &amp; <b>world</b></p>") == "hello & world"
    assert mod._strip_html("") == ""


def test_md_esc_link_label(mod):
    # brackets in a feed title would break a markdown link label
    assert "[" not in mod._md_esc("Title [with] brackets")


# ── Market-pulse indicators ─────────────────────────────────────────────

def test_sma(mod):
    assert mod._sma([1, 2, 3, 4], 2) == 3.5
    assert mod._sma([1, 2], 5) is None


def test_rsi_extremes(mod):
    rising = [float(i) for i in range(1, 40)]      # strictly up → all gains
    falling = [float(i) for i in range(40, 1, -1)]  # strictly down → all losses
    assert mod._rsi(rising) == 100.0
    assert mod._rsi(falling) == 0.0
    assert mod._rsi([1.0, 2.0]) is None             # too short


def test_macd_hist_needs_history(mod):
    assert mod._macd_hist([1.0, 2.0, 3.0]) is None
    val = mod._macd_hist([float(i) for i in range(1, 40)])
    assert isinstance(val, float)


@pytest.mark.parametrize("dim", ["work", "interests", "growth"])
def test_personalize_prompt_spans_whole_person(mod, dim):
    """The tailor must cover work + personal interests + growth, not job only."""
    sys = mod.PERSONALIZE_SYSTEM
    assert dim in sys
    # and it asks for the three-dimension JSON shape
    assert '"dimensions"' in sys


def test_personalize_prompt_distrusts_raw_tag_volume(mod):
    """Must tell the model that big tag counts (bulk imports) are weak signal,
    not the strongest — that was the original 'just echo the top tags' bug."""
    sys = mod.PERSONALIZE_SYSTEM.lower()
    assert "noisy" in sys
    assert "bulk" in sys or "import" in sys
    # authored names are the lead signal, not counts
    assert "strongest" in sys


def test_compute_indicators_shape(mod):
    closes = [100.0 + (i % 7) - (i % 3) for i in range(60)]
    ind = mod._compute_indicators(closes)
    assert set(ind) >= {"last", "change_pct", "sma20", "sma50", "rsi", "macd_hist", "trend"}
    assert 0.0 <= ind["rsi"] <= 100.0
    assert ind["trend"] in ("up", "down", "flat")
    # drops non-numeric / Nones, still needs >=2 points
    assert mod._compute_indicators([None, "x"]) is None


def test_command_center_snapshot_aggregates_soft_sources(mod, tmp_path):
    app = mod.DailyBriefApp.__new__(mod.DailyBriefApp)
    app.data_dir = tmp_path / "data" / "apps" / "daily-brief"
    app.data_dir.mkdir(parents=True)
    app._status = lambda: {"last_ok": True, "last_date": date.today().isoformat(), "item_count": 3}

    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()

    async def fake_call_app(app_id, method, **kwargs):
        if (app_id, method) == ("task", "list_all"):
            return [
                {"id": "a", "text": "Overdue item", "due": yesterday, "done": False, "focus_score": 9},
                {"id": "b", "text": "Done item", "done": True},
                {"id": "c", "text": "Undated item", "done": False, "focus_score": 1},
            ]
        if (app_id, method) == ("projects", "list_all"):
            return [
                {"id": "p1", "name": "Project One", "status": "active", "days_until_deadline": 3, "stale_days": 2, "open_tasks": 1},
                {"id": "p2", "name": "Project Two", "status": "completed", "open_tasks": 0},
            ]
        if (app_id, method) == ("journal", "get_summary"):
            return {"entries": 2, "streak": 4, "mood": "good"}
        if (app_id, method) == ("people", "panel_reach_out"):
            return [{"title": "Ada", "subtitle": "overdue", "href": "/people/#ada"}]
        if (app_id, method) == ("people", "birthdays"):
            return [{"id": "grace", "name": "Grace", "days_until": 2}]
        raise RuntimeError(f"unexpected {app_id}.{method}")

    app.call_app = fake_call_app

    snap = asyncio.run(app._command_center_snapshot())

    assert snap["tasks"]["open_count"] == 2
    assert snap["tasks"]["overdue_count"] == 1
    assert snap["tasks"]["focus"][0]["text"] == "Overdue item"
    assert snap["projects"]["active_count"] == 1
    assert snap["projects"]["attention"][0]["id"] == "p1"
    assert snap["journal"] == {"available": True, "entries": 2, "streak": 4, "mood": "good"}
    assert snap["people"]["followups"][0]["title"] == "Ada"
    assert snap["audit"]["available"] is True
    assert snap["audit"]["ok"] is True


def test_command_center_sections_degrade_when_source_missing(mod, tmp_path):
    app = mod.DailyBriefApp.__new__(mod.DailyBriefApp)
    app.data_dir = tmp_path / "data" / "apps" / "daily-brief"
    app.data_dir.mkdir(parents=True)
    app._status = lambda: {}

    async def missing(*args, **kwargs):
        raise RuntimeError("not installed")

    app.call_app = missing

    snap = asyncio.run(app._command_center_snapshot())

    assert snap["tasks"]["available"] is False
    assert snap["projects"]["available"] is False
    assert snap["journal"]["available"] is False
    assert snap["people"]["available"] is False
    assert snap["audit"]["available"] is True


# ── Feed sources / subscriptions (UI-managed) ───────────────────────────


class _Req:
    """Minimal stand-in for a Starlette request in unit tests."""

    def __init__(self, body=None, path_params=None):
        self._body = body or {}
        self.path_params = path_params or {}

    async def json(self):
        return self._body


def _src_app(mod, tmp_path):
    app = mod.DailyBriefApp.__new__(mod.DailyBriefApp)
    app.data_dir = tmp_path / "data" / "apps" / "daily-brief"
    app.data_dir.mkdir(parents=True)
    # no config override → DEFAULT_SOURCES are the built-ins
    app.app_config = lambda key, default=None: default
    app.log_activity = lambda *a, **k: None

    async def _emit(*a, **k):
        return None

    app.emit = _emit
    return app


def test_builtin_sources_shape(mod, tmp_path):
    app = _src_app(mod, tmp_path)
    b = app._builtin_sources()
    assert b and all({"id", "name", "url", "kind", "category"} <= set(s) for s in b)


def test_sources_state_starts_empty(mod, tmp_path):
    app = _src_app(mod, tmp_path)
    assert app._sources_state() == {"custom": [], "disabled": []}


def test_add_source_validates_and_persists(mod, tmp_path):
    app = _src_app(mod, tmp_path)

    async def fake_validate(url, kind):
        return [{"title": "Item", "url": "https://blog.example.com/a", "summary": ""}]

    app._validate_source = fake_validate
    r = asyncio.run(app.api_sources_add(_Req({"url": "https://blog.example.com/feed/"})))
    assert r["ok"] and r["item_count"] == 1
    managed = app._all_sources_managed()
    hit = [s for s in managed if s["url"] == "https://blog.example.com/feed/"]
    assert hit and hit[0]["origin"] == "custom" and hit[0]["enabled"] is True
    # and it flows into the resolved fetch list
    assert any(s["url"] == "https://blog.example.com/feed/" for s in app._sources())


def test_add_source_rejects_non_feed(mod, tmp_path):
    app = _src_app(mod, tmp_path)

    async def empty(url, kind):
        return []

    app._validate_source = empty
    r = asyncio.run(app.api_sources_add(_Req({"url": "https://example.com/nope"})))
    assert not r["ok"] and "no feed items" in r["error"]


def test_add_source_rejects_bad_url(mod, tmp_path):
    app = _src_app(mod, tmp_path)
    r = asyncio.run(app.api_sources_add(_Req({"url": "not-a-url"})))
    assert not r["ok"]


@pytest.mark.parametrize("bad", [
    "http://127.0.0.1:9000/feed",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://10.0.0.5/internal.xml",               # RFC1918
    "http://localhost/feed",
])
def test_add_source_blocks_ssrf_targets(mod, tmp_path, bad):
    """SSRF guard refuses internal/loopback targets BEFORE any fetch."""
    app = _src_app(mod, tmp_path)

    async def boom(url, kind):
        raise AssertionError("must not fetch a blocked host")

    app._validate_source = boom  # would raise if the guard let it through
    r = asyncio.run(app.api_sources_add(_Req({"url": bad})))
    assert not r["ok"] and "public host" in r["error"]


def test_add_source_dedupes(mod, tmp_path):
    app = _src_app(mod, tmp_path)

    async def ok(url, kind):
        return [{"title": "x", "url": "u", "summary": ""}]

    app._validate_source = ok
    u = "https://blog.example.com/feed/"
    assert asyncio.run(app.api_sources_add(_Req({"url": u})))["ok"]
    r2 = asyncio.run(app.api_sources_add(_Req({"url": u})))
    assert not r2["ok"] and r2.get("dup")


def test_toggle_drops_source_from_fetch(mod, tmp_path):
    app = _src_app(mod, tmp_path)
    b0 = app._builtin_sources()[0]
    r = asyncio.run(app.api_sources_toggle(_Req(path_params={"id": b0["id"]})))
    assert r["ok"] and r["enabled"] is False
    assert all(s["id"] != b0["id"] for s in app._sources())
    r2 = asyncio.run(app.api_sources_toggle(_Req(path_params={"id": b0["id"]})))
    assert r2["enabled"] is True
    assert any(s["id"] == b0["id"] for s in app._sources())


def test_remove_custom_but_not_builtin(mod, tmp_path):
    app = _src_app(mod, tmp_path)

    async def ok(url, kind):
        return [{"title": "x", "url": "u", "summary": ""}]

    app._validate_source = ok
    add = asyncio.run(app.api_sources_add(_Req({"url": "https://blog.example.com/feed/"})))
    sid = add["id"]
    b0 = app._builtin_sources()[0]
    r_bad = asyncio.run(app.api_sources_remove(_Req(path_params={"id": b0["id"]})))
    assert not r_bad["ok"]
    r_ok = asyncio.run(app.api_sources_remove(_Req(path_params={"id": sid})))
    assert r_ok["ok"]
    assert all(s["id"] != sid for s in app._all_sources_managed())
