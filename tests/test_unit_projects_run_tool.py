"""projects `run-tool`: request-handler tools run, everything else is refused in-band.

`POST /projects/api/projects/{id}/run-tool` calls a method another app declared
under `[provides.project-tools]`.

- The tool gets the SDK `FakeRequest`, whose `body()` lets it decode through
  `BaseApp.read_json` / `safe_json` (the old private shim had only `.json()`).
- A plain method is refused, never called with `**params`: a helper's
  arguments are whatever the caller sends (git's `log_at(repo_path)` takes any
  directory). git now declares its route handlers, which carry the repo
  allowlist and parse `count` before it reaches argv.
- Every bad input is answered before the tool runs. A bad `task_line` used to
  500 *after* the tool had executed and saved its result.
"""

import asyncio
import inspect
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

from helpers import load_app_module

from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.utils import FakeRequest

load_app_module("projects", "app")
_ops = sys.modules["apps.projects.operations"]
GitApp = load_app_module("git", "app").GitApp
ROOT = Path(__file__).resolve().parent.parent
KNOWN = {"demo", "apartment decoration", "装修"}   # projects take ids from folder names


def _git(tmp_path):
    """A real GitApp with the subprocess replaced by a recorder."""
    app = GitApp.__new__(GitApp)
    app.argv = []

    async def _git_at(repo_path, *args):
        app.argv.append((repo_path, args))
        return ("abc123 subject\n", "", 0)

    app._git_at = _git_at
    app._project_dir = lambda: str(tmp_path / "emptyos")
    app._vault_dir = lambda: str(tmp_path / "vault")
    return app


class _GitLike:
    def __init__(self):
        self.calls = []

    async def log_at(self, repo_path: str, count: int = 10) -> str:
        self.calls.append((repo_path, count))
        return "ran"


class _RouteLike:
    def __init__(self):
        self.calls = 0

    async def api_calc(self, request):
        self.calls += 1
        data = await BaseApp.read_json(request)
        return {"echo": data, "path": request.path_params.get("length"),
                "query": request.query_params.get("length")}


def _projects(tmp_path, *, loaded=("plain",), tools=None, git=None):
    apps = {"plain": _GitLike(), "calc": _RouteLike(), "git": git}
    instances = {k: v for k, v in apps.items() if k in loaded}   # the rest lazy-load
    emitted, lookups = [], []
    note = tmp_path / "demo.md"
    note.write_text("# Demo\n\n## Tasks\n- [ ] size the cable\n", encoding="utf-8")

    async def call_app(app_id, method, **kw):
        return await getattr(apps[app_id], method)(**kw)

    async def load(app_id):
        return apps.get(app_id)

    async def emit(name, data):
        emitted.append((name, data))

    class _Lock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    tools = tools or {"plain": {"tools": [{"method": "log_at"}]},
                      "calc": {"tools": [{"method": "api_calc"}, {"method": "api_gone"}]},
                      "git": {"tools": [{"method": "api_log"}, {"method": "api_status"}]}}
    return SimpleNamespace(
        apps=apps, emit=emit, emitted=emitted, call_app=call_app, data_dir=str(tmp_path),
        note=note, safe_json=BaseApp.safe_json, write_lock=lambda key: _Lock(),
        _find_project_file=lambda pid: lookups.append(pid) or (note if pid in KNOWN else None),
        lookups=lookups,
        kernel=SimpleNamespace(apps=SimpleNamespace(
            get_providers=lambda kind: tools, instances=instances, load=load)),
    )


class _Req:
    """A request whose body may be anything, including not-JSON."""

    def __init__(self, body, project="demo"):
        self.path_params = {"id": project}
        self._raw = body if isinstance(body, bytes) else json.dumps(body).encode()

    async def body(self):
        return self._raw


def _run(me, body, project="demo"):
    return asyncio.run(_ops.api_run_tool(me, _Req(body, project)))


# ── the two tool shapes ──────────────────────────────────────────────────


def test_a_route_tool_reads_its_body_through_read_json(tmp_path):
    me = _projects(tmp_path)   # calc is not loaded yet — exercises the lazy load
    res = _run(me, {"app": "calc", "method": "api_calc",
                    "params": {"length": 120, "note": "→ 中"}})
    assert res.get("result") == {"echo": {"length": 120, "note": "→ 中"},
                                 "path": 120, "query": 120}, res
    saved = list((tmp_path / "calcs" / "demo").glob("*-calc-api_calc.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text(encoding="utf-8"))["path"] == 120
    assert [n for n, _ in me.emitted] == ["projects:calc_attached"]


def test_a_plain_method_tool_is_refused_and_never_called(tmp_path):
    me = _projects(tmp_path)
    res = _run(me, {"app": "plain", "method": "log_at",
                    "params": {"repo_path": "C:/elsewhere", "count": "-output=C:/x"}})
    assert "not a request handler" in res.get("error", ""), res
    assert me.apps["plain"].calls == [] and me.emitted == []


def test_a_declared_but_missing_method_is_an_in_band_error(tmp_path):
    res = _run(_projects(tmp_path), {"app": "calc", "method": "api_gone", "params": {}})
    assert res == {"error": "Tool method 'api_gone' not found on app 'calc'"}


def test_task_line_attaches_the_result_under_that_task(tmp_path):
    me = _projects(tmp_path)
    res = _run(me, {"app": "calc", "method": "api_calc", "params": {}, "task_line": 3})
    lines = me.note.read_text(encoding="utf-8").split("\n")
    assert lines[3] == "- [ ] size the cable"
    assert lines[4] == f"  - calc: calc/api_calc \u2192 {res['saved']}"


# ── every bad input is refused before the tool runs ──────────────────────


def test_bad_inputs_are_refused_in_band_and_the_tool_never_runs(tmp_path):
    good = {"app": "calc", "method": "api_calc", "params": {}}
    cases = [
        (b"not json", "app and method required"),
        ([1, 2], "app and method required"),
        ({**good, "app": ["calc"]}, "app and method required"),
        ({**good, "method": 7}, "app and method required"),
        ({**good, "params": ["x"]}, "params must be an object"),
        ({**good, "params": "x"}, "params must be an object"),
        ({**good, "task_line": "3"}, "task_line must be a non-negative integer"),
        ({**good, "task_line": True}, "task_line must be a non-negative integer"),
        ({**good, "task_line": -1}, "task_line must be a non-negative integer"),
    ]
    for body, want in cases:
        me = _projects(tmp_path)
        res = _run(me, body)
        assert res == {"error": want}, (body, res)
        assert me.apps["calc"].calls == 0 and me.emitted == [], body
    assert not (tmp_path / "calcs").exists()


def test_malformed_tool_declarations_are_skipped_not_a_crash(tmp_path):
    body = {"app": "calc", "method": "api_calc", "params": {}}
    for tools in ({"calc": {"tools": [{"label": "no method"}, "api_calc", None]}},
                  {"calc": None},
                  {"calc": {"tools": None}}):
        res = _run(_projects(tmp_path, tools=tools), body)
        assert res == {"error": "Tool method 'api_calc' not declared by app 'calc'"}, tools


def test_a_task_line_past_the_note_is_refused_before_the_tool_runs(tmp_path):
    me = _projects(tmp_path)
    res = _run(me, {"app": "calc", "method": "api_calc", "params": {}, "task_line": 9999})
    assert res == {"error": "task_line 9999 is past the end of the project note"}
    assert me.apps["calc"].calls == 0 and me.emitted == []


def test_project_ids_with_spaces_or_cjk_run(tmp_path):
    # Projects take ids from folder names; an allowlist refused real ones.
    for project in ("apartment decoration", "装修"):
        me = _projects(tmp_path)
        res = _run(me, {"app": "calc", "method": "api_calc", "params": {}}, project=project)
        assert res.get("ok") is True, (project, res)
        assert (tmp_path / "calcs" / project).is_dir()


def test_an_unsafe_or_unknown_project_is_refused_before_anything_is_written(tmp_path):
    good = {"app": "calc", "method": "api_calc", "params": {}}
    for project in ("..", ".", "a/b", "..\\x", ".hidden", "trail.", "a\x01b", " "):
        me = _projects(tmp_path)
        res = _run(me, good, project=project)
        assert "project id" in res.get("error", ""), (project, res)
        assert me.lookups == [], project   # refused before the lookup can walk out
    me = _projects(tmp_path)
    assert _run(me, good, project="no-such") == {"error": "Project not found"}
    assert me.apps["calc"].calls == 0
    assert not (tmp_path / "calcs").exists()


# ── git's declared tools ─────────────────────────────────────────────────


def test_git_declares_only_request_handlers_as_project_tools():
    raw = tomllib.loads((ROOT / "apps/public/standard/git/manifest.toml").read_text(encoding="utf-8"))
    methods = [t["method"] for t in raw["provides"]["project-tools"]["tools"]]
    assert methods
    for m in methods:
        fn = getattr(GitApp, m)
        assert getattr(fn, "_eos_web", None), f"{m} is not a @web_route handler"
        assert "request" in inspect.signature(fn).parameters, m


def test_git_log_via_run_tool_keeps_injection_out_of_argv(tmp_path):
    git = _git(tmp_path)
    me = _projects(tmp_path, git=git)
    res = _run(me, {"app": "git", "method": "api_log",
                    "params": {"repo": "C:/anywhere", "count": "-output=C:/x"}})
    assert res == {"error": "Tool execution failed: count must be an integer (clamped to 1-500)"}, res
    assert git.argv == [] and me.emitted == []
    assert not (tmp_path / "calcs").exists()   # a failed tool is not a saved result

    res = _run(me, {"app": "git", "method": "api_log", "params": {"repo": "vault", "count": 5}})
    assert res.get("result") == {"log": "abc123 subject\n"}, res
    assert git.argv == [(str(tmp_path / "vault"), ("log", "--oneline", "-5"))]


def test_git_repo_outside_the_allowlist_falls_back_to_the_project_repo(tmp_path):
    git = _git(tmp_path)
    res = _run(_projects(tmp_path, git=git),
               {"app": "git", "method": "api_status", "params": {"repo": "C:/anywhere"}})
    assert "result" in res, res
    assert {path for path, _ in git.argv} == {str(tmp_path / "emptyos")}


def test_git_log_at_clamps_count_into_range(tmp_path):
    git = _git(tmp_path)
    for given, flag in ((-5, "-1"), (0, "-1"), (10**6, "-500"), ("7", "-7")):
        git.argv.clear()
        asyncio.run(git.log_at("R", given))
        assert git.argv == [("R", ("log", "--oneline", flag))], given
    try:
        asyncio.run(git.log_at("R", "-output=C:/x"))
    except ValueError:
        pass
    else:
        raise AssertionError("a non-integer count must not reach argv")


def test_every_git_count_route_answers_a_bad_count_in_band(tmp_path):
    # api_log, api_summary and api_log_detail each put count into argv.
    for route in ("api_log", "api_summary", "api_log_detail"):
        for bad in ("lots", "-output=C:/x", 1e999, [3]):
            git = _git(tmp_path)
            res = asyncio.run(getattr(git, route)(FakeRequest(query_params={"count": bad})))
            assert res == {"error": "count must be an integer (clamped to 1-500)"}, (route, bad)
            assert git.argv == [], (route, bad)


def test_git_count_routes_clamp_and_default(tmp_path):
    for route, given, flag in (("api_log", None, "-10"), ("api_log_detail", 10**6, "-500"),
                               ("api_log_detail", "0", "-1"), ("api_log_detail", None, "-20")):
        git = _git(tmp_path)
        qp = {} if given is None else {"count": given}
        asyncio.run(getattr(git, route)(FakeRequest(query_params=qp)))
        assert flag in git.argv[0][1], (route, given, git.argv)


def test_git_vault_repo_falls_back_when_no_vault_is_configured(tmp_path):
    git = _git(tmp_path)
    git._vault_dir = lambda: None
    asyncio.run(git.api_status(FakeRequest(query_params={"repo": "vault"})))
    assert {path for path, _ in git.argv} == {str(tmp_path / "emptyos")}
