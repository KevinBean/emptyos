"""Unit tests for the i18n / translate layer — pure, no daemon.

Covers the highest-risk logic: the cache-aware translate flow (fail-soft to
English, partial-cache, persistence) and the localization guard matrix that
keeps JSON/code think() calls in English so parsers never break.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from emptyos.sdk import i18n
from emptyos.sdk.base_app import BaseApp


@pytest.fixture(autouse=True)
def _reset_locks():
    # Each test runs its own asyncio.run() loop; the module-level per-lang
    # asyncio.Locks bind to the loop they're created in, so clear them between
    # tests or a Lock from a previous loop would raise "attached to a different
    # loop".
    i18n._cache_locks.clear()
    yield
    i18n._cache_locks.clear()


# --- fakes -----------------------------------------------------------------


class _Settings:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        return self._d.get(k, default)


def _app(settings_dict):
    a = BaseApp.__new__(BaseApp)
    s = _Settings(settings_dict)
    a.kernel = SimpleNamespace(
        services=SimpleNamespace(get_optional=lambda n: s if n == "settings" else None)
    )
    return a


class _Cap:
    def __init__(self, fn):
        self.fn = fn
        self.calls = 0
        self.last_kwargs = None

    async def execute(self, **kw):
        self.calls += 1
        self.last_kwargs = kw
        return SimpleNamespace(value=self.fn(**kw))


class _Kernel:
    def __init__(self, data_dir, fn):
        self.config = SimpleNamespace(data_dir=data_dir)
        self.translate_cap = _Cap(fn)

    def capability(self, name):
        if name == "translate":
            return self.translate_cap
        raise KeyError(name)


def _upper(**kw):
    return [t.upper() for t in kw["texts"]]


def _boom(**kw):
    raise RuntimeError("no provider")


# --- registry --------------------------------------------------------------


def test_language_registry():
    assert "en" in i18n.LANGUAGES and "zh" in i18n.LANGUAGES
    assert i18n.lang_name("zh") == "Simplified Chinese"
    assert i18n.lang_name("xx") == "xx"  # unknown → echoes the code
    assert i18n.nllb_code("zh") == "zho_Hans"
    assert i18n.nllb_code("xx") == ""  # unsupported → empty (provider raises)
    assert i18n.is_rtl("ar") is True
    assert i18n.is_rtl("en") is False


def test_cache_roundtrip(tmp_path):
    i18n.save_cache(tmp_path, "zh", {"Save": "保存"})
    assert i18n.load_cache(tmp_path, "zh") == {"Save": "保存"}
    assert i18n.load_cache(tmp_path, "ja") == {}  # missing file → empty


def test_cache_load_corrupt(tmp_path):
    p = i18n._cache_path(tmp_path, "zh")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert i18n.load_cache(tmp_path, "zh") == {}  # fail-soft, not raise


# --- translate_cached ------------------------------------------------------


def test_english_passthrough_never_calls_provider():
    k = _Kernel(data_dir="/nonexistent", fn=_boom)
    out = asyncio.run(i18n.translate_cached(k, "en", ["Add task", "Settings"]))
    assert out == {"Add task": "Add task", "Settings": "Settings"}
    assert k.translate_cap.calls == 0


def test_success_fills_and_persists_cache(tmp_path):
    k = _Kernel(data_dir=tmp_path, fn=_upper)
    out = asyncio.run(i18n.translate_cached(k, "zh", ["a", "b"]))
    assert out == {"a": "A", "b": "B"}
    assert k.translate_cap.calls == 1
    # persisted
    assert i18n.load_cache(tmp_path, "zh") == {"a": "A", "b": "B"}
    # second call is served from cache — provider not called again
    out2 = asyncio.run(i18n.translate_cached(k, "zh", ["a", "b"]))
    assert out2 == {"a": "A", "b": "B"}
    assert k.translate_cap.calls == 1


def test_partial_cache_only_sends_misses(tmp_path):
    i18n.save_cache(tmp_path, "zh", {"a": "A"})
    k = _Kernel(data_dir=tmp_path, fn=_upper)
    out = asyncio.run(i18n.translate_cached(k, "zh", ["a", "b"]))
    assert out == {"a": "A", "b": "B"}
    assert k.translate_cap.last_kwargs["texts"] == ["b"]  # only the miss


def test_failsoft_returns_english_and_does_not_cache(tmp_path):
    k = _Kernel(data_dir=tmp_path, fn=_boom)
    out = asyncio.run(i18n.translate_cached(k, "zh", ["a", "b"]))
    assert out == {"a": "a", "b": "b"}  # degrades to English
    assert i18n.load_cache(tmp_path, "zh") == {}  # nothing persisted


def test_dedup_and_blank_filtering(tmp_path):
    k = _Kernel(data_dir=tmp_path, fn=_upper)
    out = asyncio.run(i18n.translate_cached(k, "zh", ["a", "a", "", "  ", "b"]))
    assert set(out) == {"a", "b"}
    assert sorted(k.translate_cap.last_kwargs["texts"]) == ["a", "b"]


def test_malformed_provider_response_failsoft(tmp_path):
    # provider returns wrong-length list → treated as failure → English
    k = _Kernel(data_dir=tmp_path, fn=lambda **kw: ["only-one"])
    out = asyncio.run(i18n.translate_cached(k, "zh", ["a", "b"]))
    assert out == {"a": "a", "b": "b"}


# --- localization guard matrix (parser safety) -----------------------------


EN = {"ui.language": "en"}
ZH = {"ui.language": "zh"}
ZHL = {"ui.language": "zh", "ui.localize_think": "true"}


@pytest.mark.parametrize(
    "settings,localize,domain,task_shape,expected",
    [
        (EN, None, None, None, None),  # english → never
        (ZH, False, "text", None, None),  # explicit off
        (ZH, True, "code", "parse-json", "zh"),  # explicit on bypasses guards
        (ZH, None, "text", None, None),  # auto policy OFF
        (ZHL, None, "text", None, "zh"),  # auto ON + prose
        (ZHL, None, None, None, "zh"),  # auto ON + default domain
        (ZHL, None, "text", "parse-json", None),  # JSON shape → english
        (ZHL, None, "code", None, None),  # code domain → english
        (ZHL, None, "reason", None, "zh"),  # reason counts as prose
    ],
)
def test_localize_target_matrix(settings, localize, domain, task_shape, expected):
    assert _app(settings)._localize_target(localize, domain, task_shape) == expected


def test_apply_localization_adds_suffix():
    kw = {"system": "BASE"}
    _app(ZHL)._apply_localization(kw, None, "text", None)
    assert kw["system"].startswith("BASE")
    assert "Simplified Chinese" in kw["system"]


def test_apply_localization_noop_structured():
    kw = {"system": "BASE"}
    _app(ZHL)._apply_localization(kw, None, "text", "parse-json")
    assert kw["system"] == "BASE"


def test_apply_localization_noop_english():
    kw = {"system": "BASE"}
    _app(EN)._apply_localization(kw, True, "text", None)
    assert kw["system"] == "BASE"
