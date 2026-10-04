"""Unit pins for `plugins/footage/plugin.py`.

The plugin ships in `standard` since editions M8 (2026-09-28), so every public
install and desktop product loads it, almost always with no key. What these
pin is what a public install relies on: no key means no provider and no
directory; a keyed provider is always cloud (consent-gated); the cache is
under `data_dir`; and a failed download never leaves a file the cache would
later trust. Kernel-free: a fake kernel carries only what `connect()` reads.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("footage_plugin_under_test",
                                               REPO / "plugins" / "footage" / "plugin.py")
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)


class _Cap:
    def __init__(self):
        self.providers = []

    def add_provider(self, provider, priority=0):
        self.providers.append(provider)


def _plugin(tmp: Path, config: dict | None = None, cap: _Cap | None = None):
    cap = cap if cap is not None else _Cap()
    kernel = SimpleNamespace(
        capabilities=SimpleNamespace(get=lambda name: cap if name == "footage" else None),
        config=SimpleNamespace(data_dir=tmp / "data", path=tmp / "cfg" / "emptyos.toml"),
    )
    p = fp.FootagePlugin(kernel, {})
    p._config = dict(config or {})
    return p, cap


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)


def test_no_key_registers_nothing_and_creates_no_directory(tmp_path, no_keys):
    p, cap = _plugin(tmp_path)
    asyncio.run(p.connect())
    assert cap.providers == []
    assert not (tmp_path / "data").exists(), "a keyless public install must not create data/footage"


def test_a_key_registers_a_cloud_provider_caching_under_data_dir(tmp_path, no_keys, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    p, cap = _plugin(tmp_path)
    asyncio.run(p.connect())
    assert [type(x).__name__ for x in cap.providers] == ["PexelsFootageProvider"]
    prov = cap.providers[0]
    assert prov.is_cloud is True
    assert prov._cache == tmp_path / "data" / "footage" / "cache"
    assert prov._cache.is_dir()
    assert not (tmp_path / "cfg").exists(), "the cache must not sit beside the config file"


@pytest.mark.parametrize("cls", ["PexelsFootageProvider", "PixabayFootageProvider"])
def test_both_providers_declare_service_trust(cls, tmp_path):
    prov = getattr(fp, cls)("k", tmp_path, 15, 60)
    assert prov.trust == "service"
    # The declaration, not the address, is what makes it cloud: pointed at a
    # host that looks local, it must still be consent-gated.
    prov.host = "http://127.0.0.1:8080"
    assert prov.is_cloud is True


@pytest.mark.parametrize("value", ["abc", None, 0, -5, "", [1]])
def test_a_malformed_config_number_falls_back_instead_of_crashing(tmp_path, no_keys, monkeypatch, value):
    monkeypatch.setenv("PIXABAY_API_KEY", "k")
    p, cap = _plugin(tmp_path, {"timeout": value, "per_page": value})
    asyncio.run(p.connect())
    assert cap.providers[0]._timeout == 60
    assert cap.providers[0]._per_page == 15


# ── download ───────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, body=b"clip", fail=False):
        self.status, self._body, self._fail = status, body, fail

    async def read(self):
        if self._fail:
            raise ConnectionResetError("peer reset mid-body")
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, resp):
        self._resp = resp
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self._resp


def _provider(tmp: Path):
    cache = tmp / "cache"
    cache.mkdir()
    return fp.PexelsFootageProvider("k", cache, 15, 60), cache


def test_download_writes_the_clip_and_leaves_no_temp_file(tmp_path):
    prov, cache = _provider(tmp_path)
    path = asyncio.run(prov._download(_Session(_Resp(body=b"mp4-bytes")), "https://x/v.mp4?sig=1"))
    assert Path(path).read_bytes() == b"mp4-bytes"
    assert [f.name for f in cache.iterdir()] == [Path(path).name]


def test_a_failed_download_leaves_nothing_the_cache_would_trust(tmp_path):
    prov, cache = _provider(tmp_path)
    assert asyncio.run(prov._download(_Session(_Resp(fail=True)), "https://x/v.mp4")) == ""
    assert list(cache.iterdir()) == []


def test_a_failed_write_leaves_no_partial_file(tmp_path, monkeypatch):
    prov, cache = _provider(tmp_path)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(fp.os, "replace", boom)
    assert asyncio.run(prov._download(_Session(_Resp()), "https://x/v.mp4")) == ""
    assert list(cache.iterdir()) == [], "neither the clip nor its .part may survive"


def test_an_empty_body_is_not_cached(tmp_path):
    prov, cache = _provider(tmp_path)
    assert asyncio.run(prov._download(_Session(_Resp(body=b"")), "https://x/v.mp4")) == ""
    assert list(cache.iterdir()) == []


def test_a_cached_clip_is_served_without_a_request(tmp_path):
    prov, cache = _provider(tmp_path)
    first = asyncio.run(prov._download(_Session(_Resp()), "https://x/v.mp4?a=1"))
    session = _Session(_Resp(body=b"other"))
    again = asyncio.run(prov._download(session, "https://x/v.mp4?a=2"))
    assert again == first and session.calls == 0  # query string is not part of the key


# ── pick ───────────────────────────────────────────────────────────────────

def test_execute_picks_the_first_clip_meeting_the_duration_floor(tmp_path, monkeypatch):
    prov, _ = _provider(tmp_path)
    cands = [{"url": "u-short", "duration": 3.0, "width": 1, "height": 1},
             {"url": "u-long", "duration": 12.0, "width": 2, "height": 2}]

    async def search(session, query, orientation):
        return cands

    async def download(session, url):
        return f"/cache/{url}"

    monkeypatch.setattr(prov, "_search", search)
    monkeypatch.setattr(prov, "_download", download)
    got = asyncio.run(prov.execute(query="rain", min_duration=10))
    assert got["url"] == "u-long" and got["path"] == "/cache/u-long"
    # Nothing meets the floor: fall back to the best-first candidate.
    got = asyncio.run(prov.execute(query="rain", min_duration=60))
    assert got["url"] == "u-short"


def test_an_error_response_is_not_cached(tmp_path):
    # A 403/404 body written as vid-<md5>.mp4 would be served forever.
    prov, cache = _provider(tmp_path)
    got = asyncio.run(prov._download(_Session(_Resp(status=404, body=b"<html>nope")), "https://x/v.mp4"))
    assert got == "" and list(cache.iterdir()) == []


def test_each_write_uses_its_own_temp_name(tmp_path, monkeypatch):
    # A shared `.part` lets two concurrent writers interleave into one file.
    seen = []
    real = fp.os.replace

    def record(src, dst):
        seen.append(Path(src).name)
        real(src, dst)

    monkeypatch.setattr(fp.os, "replace", record)
    dest = tmp_path / "vid-x.mp4"
    fp._write_atomic(dest, b"a")
    fp._write_atomic(dest, b"b")
    assert len(set(seen)) == 2 and all(n.endswith(".part") and n != dest.name for n in seen)


def test_the_write_runs_off_the_event_loop(tmp_path, monkeypatch):
    calls = []
    real = fp.asyncio.to_thread

    async def record(fn, *args, **kwargs):
        calls.append(fn)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(fp.asyncio, "to_thread", record)
    prov, _ = _provider(tmp_path)
    assert asyncio.run(prov._download(_Session(_Resp()), "https://x/v.mp4"))
    assert calls == [fp._write_atomic]


def test_a_locked_destination_still_serves_the_complete_clip(tmp_path, monkeypatch):
    # Windows refuses os.replace onto a file another process holds open. Here a
    # concurrent fetch finished first: the complete clip is the answer.
    prov, cache = _provider(tmp_path)

    def other_writer_won(dest, data):
        dest.write_bytes(b"complete clip")
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(fp, "_write_atomic", other_writer_won)
    got = asyncio.run(prov._download(_Session(_Resp(body=b"mine")), "https://x/v.mp4"))
    assert got and Path(got).read_bytes() == b"complete clip"


def test_a_denied_write_with_no_clip_reports_nothing(tmp_path, monkeypatch):
    prov, cache = _provider(tmp_path)

    def denied(dest, data):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(fp, "_write_atomic", denied)
    assert asyncio.run(prov._download(_Session(_Resp()), "https://x/v.mp4")) == ""


def test_a_non_string_env_name_does_not_stop_the_plugin(tmp_path, no_keys):
    p, cap = _plugin(tmp_path, {"pexels_api_key_env": 1, "pixabay_api_key_env": None})
    asyncio.run(p.connect())  # must not raise
    assert cap.providers == []
