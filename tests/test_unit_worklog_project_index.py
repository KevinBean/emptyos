"""Unit: worklog → projects deep-link id map (`_project_index`).

`reads.py` already called `projects.list_projects` to union live project names
into the picker vocabulary, and threw the `id` away — so a worklog entry had no
way back to the project it belongs to. `_project_index` carries the id in the
same pass, and `GET /worklog/api/projects` exposes it as `project_id`.

Two things these pin that are easy to break:

* **`_project_names` must keep returning 2-tuples.** `reporting.py` unpacks it
  as ``for n, _ in ...`` (a 3-tuple raises) and `project_names` re-exports it as
  a cross-app contract that `worklog-capture/digest.py` consumes.
* **The id map must cover names worklog history ALREADY knows.** The obvious
  implementation collects ids only inside the `name.lower() not in known_lower`
  branch, which silently links only projects that have never been logged
  against — i.e. exactly the wrong half.

Daemon-free: `_project_index` is a plain coroutine over `self._all_days` and
`self.try_call_app`, both stubbed here.
"""
import asyncio
import importlib.util
import pathlib
import sys
import types

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_APP_DIR = _ROOT / "apps/public/standard/worklog"

# reads.py does `from .parser import ...`, so it needs a real parent package —
# the multi-module-apps.md standalone-load recipe (`.app` is TYPE_CHECKING-only).
_pkg = types.ModuleType("apps")
_pkg.__path__ = [str(_ROOT / "apps")]
sys.modules.setdefault("apps", _pkg)
_wl_pkg = types.ModuleType("apps.worklog")
_wl_pkg.__path__ = [str(_APP_DIR)]
sys.modules.setdefault("apps.worklog", _wl_pkg)


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(f"apps.worklog.{name}", _APP_DIR / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"apps.worklog.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_load("parser", "parser.py")
reads = _load("reads", "reads.py")


class _FakeApp:
    """Minimal stand-in for WorklogApp — only what `_project_index` touches."""

    def __init__(self, days=None, projects=None, err=""):
        self._days = days or []
        self._projects = projects
        self._err = err

    async def _all_days(self, employer=""):
        return self._days

    async def try_call_app(self, app, method, **kwargs):
        assert (app, method) == ("projects", "list_projects")
        if self._err:
            return None, self._err
        return self._projects, ""

    # bound the same way app.py binds them
    _project_index = reads._project_index
    _project_names = reads._project_names


def _day(*groups):
    return {"parsed": {"projects": [
        {"project": name, "items": [{"text": f"item {i}", "status": "todo"}
                                    for i in range(count)]}
        for name, count in groups
    ]}}


def _project(pid, name, status="active", employer=""):
    return {"id": pid, "name": name, "status": status, "employer": employer}


def _run(app, employer=""):
    return asyncio.run(app._project_index(employer))


# ── the core claim ────────────────────────────────────────────────────────

def test_id_map_carries_project_id_from_the_projects_app():
    app = _FakeApp(days=[], projects=[_project("cable-rating", "Cable rating")])
    ranked, ids = _run(app)
    assert ids == {"cable rating": "cable-rating"}
    assert ranked == [("Cable rating", 0)]


def test_id_map_covers_a_project_already_present_in_worklog_history():
    """The half a naive implementation drops.

    "Cable rating" has been logged against for months, so it is already in
    `names` and never enters the `not in known_lower` branch. It is also the
    project most worth linking — collecting ids only for unseen names would
    link exclusively the projects nobody has worked on.
    """
    app = _FakeApp(
        days=[_day(("Cable rating", 3))],
        projects=[_project("cable-rating", "Cable rating")],
    )
    ranked, ids = _run(app)
    assert ids == {"cable rating": "cable-rating"}
    assert ranked == [("Cable rating", 3)]          # history count preserved


def test_history_only_labels_get_no_id():
    """"General" and site names have no project note — they must not link."""
    app = _FakeApp(
        days=[_day(("General", 9), ("Tungkillo", 2))],
        projects=[_project("cable-rating", "Cable rating")],
    )
    ranked, ids = _run(app)
    assert "general" not in ids and "tungkillo" not in ids
    assert dict(ranked)["General"] == 9


def test_lookup_key_is_lowercased_so_matching_is_case_insensitive():
    app = _FakeApp(days=[_day(("CABLE RATING", 1))],
                   projects=[_project("cable-rating", "Cable Rating")])
    _, ids = _run(app)
    assert ids["cable rating"] == "cable-rating"


# ── exclusions ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["archived", "completed", "shelved", "ARCHIVED"])
def test_dead_projects_contribute_neither_a_name_nor_an_id(status):
    app = _FakeApp(days=[], projects=[_project("old", "Old thing", status=status)])
    ranked, ids = _run(app)
    assert ids == {} and ranked == []


def test_employer_filter_excludes_other_employers_from_the_id_map():
    app = _FakeApp(days=[], projects=[
        _project("a", "Mine", employer="Acme Power"),
        _project("b", "Theirs", employer="Other Corp"),
    ])
    _, ids = _run(app, employer="Acme Power")
    assert ids == {"mine": "a"}


def test_project_without_an_id_is_named_but_not_linked():
    app = _FakeApp(days=[], projects=[_project("", "No id yet")])
    ranked, ids = _run(app)
    assert ids == {}
    assert ranked == [("No id yet", 0)]


def test_projects_app_unavailable_degrades_to_history_only():
    """`optional_apps` — an absent projects app must not blank the picker."""
    app = _FakeApp(days=[_day(("General", 4))], err="app not loaded")
    ranked, ids = _run(app)
    assert ids == {}
    assert ranked == [("General", 4)]


# ── the contract the wrapper must not break ───────────────────────────────

def test_project_names_still_returns_two_tuples():
    """reporting.py unpacks `for n, _ in ...`; a third element raises there."""
    app = _FakeApp(days=[_day(("General", 2))],
                   projects=[_project("cable-rating", "Cable rating")])
    ranked = asyncio.run(app._project_names(""))
    assert all(isinstance(row, tuple) and len(row) == 2 for row in ranked)
    assert [n for n, _ in ranked]           # the exact unpack reporting.py does


def test_ranking_is_by_item_count_descending():
    app = _FakeApp(days=[_day(("Small", 1), ("Big", 7))], projects=[])
    ranked, _ = _run(app)
    assert [n for n, _ in ranked] == ["Big", "Small"]
