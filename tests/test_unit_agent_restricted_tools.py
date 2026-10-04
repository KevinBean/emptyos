"""The chat profile's narrowed tools (emptyos/sdk/agent_tools/restricted.py).

Hiding Bash from a chat's schema bounds nothing if the tools it IS offered
reach just as far. Each class here closes one of those: Fetch could POST to the
daemon's own tool-running route, CallApp could call any app method down to
outbound send, and Read could open emptyos.toml. These pin each boundary from
the refusing side — a test that only drives the happy path stays green when
the guard is deleted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from emptyos.sdk.agent_tools import restricted as R
from emptyos.sdk.agent_tools.call_app import CallAppTool
from emptyos.sdk.verb_registry import VerbEntry


def call(coro):
    return asyncio.run(coro)


# ── WebFetch ────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status, headers=None, body=b""):
        self.status, self.headers, self._body = status, headers or {}, body

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    """aiohttp.ClientSession stand-in serving a scripted {url: response} map."""

    def __init__(self, routes):
        self.routes, self.got = routes, []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url, **kw):
        assert kw.get("allow_redirects") is False  # every hop must come back to us
        self.got.append(url)
        return self.routes[url]


@pytest.fixture
def web(monkeypatch):
    """Public = anything on *.example; the real guard is is_public_web_url."""
    import aiohttp

    from emptyos.sdk import web_search

    monkeypatch.setattr(web_search, "is_public_web_url", lambda u: ".example" in u)

    def install(routes):
        s = _Session(routes)
        monkeypatch.setattr(aiohttp, "ClientSession", s)
        return s

    return install


def test_fetch_refuses_any_method_but_get(web):
    s = web({})
    for method in ("POST", "put", "DELETE"):
        r = call(R.WebFetchTool().run(None, url="https://a.example/", method=method))
        assert not r.ok and "only GET" in r.content
    assert s.got == []


def test_fetch_refuses_the_daemon_itself(web):
    s = web({})
    r = call(R.WebFetchTool().run(None, url="http://127.0.0.1:9000/agent/api/mcp/tools/call"))
    assert not r.ok and "not a public web address" in r.content and s.got == []


def test_a_redirect_into_the_private_network_is_refused_at_the_hop(web):
    s = web({"https://a.example/": _Resp(302, {"Location": "http://127.0.0.1:9000/secret"})})
    r = call(R.WebFetchTool().run(None, url="https://a.example/"))
    assert not r.ok and "127.0.0.1" in r.content
    assert s.got == ["https://a.example/"]  # the private hop was never requested


def test_relative_redirects_resolve_and_the_chain_is_capped(web):
    ok = web({
        "https://a.example/x": _Resp(301, {"Location": "/y"}),
        "https://a.example/y": _Resp(200, {"Content-Type": "text/plain"}, b"hello"),
    })
    r = call(R.WebFetchTool().run(None, url="https://a.example/x"))
    assert r.ok and "hello" in r.content and ok.got[-1] == "https://a.example/y"

    loop = {f"https://a.example/{i}": _Resp(302, {"Location": f"/{i + 1}"}) for i in range(20)}
    s = web(loop)
    r = call(R.WebFetchTool().run(None, url="https://a.example/0"))
    assert not r.ok and "redirects" in r.content
    assert len(s.got) == R.MAX_REDIRECTS + 1


# ── ScopedRead ──────────────────────────────────────────────────────────


def _kernel(tmp_path, *, allowed=(), vault_in_repo=False):
    repo = tmp_path / "repo"
    vault = (repo / "vault") if vault_in_repo else (tmp_path / "vault")
    for d in (repo, vault):
        d.mkdir(parents=True, exist_ok=True)
    (repo / "emptyos.toml").write_text('auth_token = "SECRET"', encoding="utf-8")
    (vault / "note.md").write_text("vault note", encoding="utf-8")
    section = {"allowed_roots": list(allowed)}
    config = SimpleNamespace(
        notes_path=str(vault),
        path=str(repo / "emptyos.toml"),
        get_section=lambda name: section if name == "capabilities.search.files" else {},
        get=lambda k, d=None: d,
    )
    return SimpleNamespace(config=config), repo, vault


def test_read_opens_the_vault_by_relative_path(tmp_path):
    kernel, _, _ = _kernel(tmp_path)
    r = call(R.ScopedReadTool().run(SimpleNamespace(kernel=kernel), path="note.md"))
    assert r.ok and "vault note" in r.content


def test_read_refuses_the_repo_and_its_credentials(tmp_path):
    kernel, repo, _ = _kernel(tmp_path)
    app = SimpleNamespace(kernel=kernel)
    for path in (str(repo / "emptyos.toml"), "../repo/emptyos.toml"):
        r = call(R.ScopedReadTool().run(app, path=path))
        assert not r.ok and "outside the folders" in r.content and "SECRET" not in r.content


def test_a_root_that_contains_the_repo_is_dropped_not_honoured(tmp_path):
    other = tmp_path / "docs"
    other.mkdir()
    kernel, repo, vault = _kernel(tmp_path, allowed=[str(tmp_path), "repo", "vault", str(other)])
    roots = R.chat_read_roots(kernel)
    assert Path(tmp_path).resolve() not in roots  # would hand the repo back
    assert repo.resolve() not in roots
    assert roots == [vault.resolve(), other.resolve()]


def test_a_vault_kept_inside_the_repo_stays_readable(tmp_path):
    kernel, repo, vault = _kernel(tmp_path, vault_in_repo=True)
    assert R.chat_read_roots(kernel) == [vault.resolve()]
    app = SimpleNamespace(kernel=kernel)
    assert call(R.ScopedReadTool().run(app, path="note.md")).ok
    assert not call(R.ScopedReadTool().run(app, path=str(repo / "emptyos.toml"))).ok


# ── VerbCallApp ─────────────────────────────────────────────────────────


def _verb(app_id, method, eligibility="stable", surfaces=("agent",), **kw):
    return VerbEntry(verb=f"{app_id}.{method}", app_id=app_id, method=method,
                     eligibility=eligibility, surfaces=tuple(surfaces), **kw)


VERBS = [
    _verb("task", "add", args={"text": "string"}, summary="Capture a task"),
    _verb("journal", "today", eligibility="gated", surfaces=("assistant",)),
    _verb("mail", "send", eligibility="never"),               # outbound: never
    _verb("aura", "voice_open", surfaces=("voice",)),         # voice-only
]


@pytest.fixture
def called(monkeypatch):
    seen = []

    async def fake_run(self, app, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(ok=True, content="done")

    monkeypatch.setattr(CallAppTool, "run", fake_run)
    return seen


def _app():
    return SimpleNamespace(kernel=SimpleNamespace(apps=SimpleNamespace(get_verbs=lambda: VERBS)))


def test_no_arguments_lists_exactly_the_callable_actions(called):
    r = call(R.VerbCallAppTool().run(_app()))
    assert r.ok and r.display["actions"] == ["journal.today", "task.add"]
    assert "task.add(text: string) — Capture a task" in r.content
    assert called == []


def test_an_undeclared_outbound_or_voice_only_method_is_refused(called):
    for app_id, method in (("task", "delete_all"), ("mail", "send"), ("aura", "voice_open"), ("vault", "write")):
        r = call(R.VerbCallAppTool().run(_app(), app_id=app_id, method=method, arguments={}))
        assert not r.ok and "not an action available here" in r.content
    assert called == []


def test_the_consent_prompt_says_a_listing_is_a_listing():
    t = R.VerbCallAppTool()
    assert t.permission_summary({"app_id": "task"}) == "CallApp: list the available actions of task"
    assert t.permission_summary({}) == "CallApp: list the available actions"
    assert "task.add" in t.permission_summary({"app_id": "task", "method": "add", "arguments": {"text": "x"}})


def test_a_declared_verb_reaches_the_real_call(called):
    r = call(R.VerbCallAppTool().run(_app(), app_id="task", method="add", arguments={"text": "x"}))
    assert r.ok and called == [{"app_id": "task", "method": "add", "arguments": {"text": "x"}}]
