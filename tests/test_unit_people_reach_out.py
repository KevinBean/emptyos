"""Unit tests for the People app's reach-out (stay-in-touch) candidate logic.

Daemon-free (`test_unit_` prefix) — `_reach_out_candidates`, `_get_overdue`,
and `_frequency_days` are pure functions of a people list, so we bind the real
methods onto a stub and exercise them without booting the kernel.

Covers the hub `reach-out` panel's two tiers:
  1. cadence-overdue (has contact_frequency, past 1.5×)
  2. gone-quiet (real relationship, no cadence, last contact > _COLD_DAYS)
plus the exclusions (recent, drains-energy, no declared relationship).
"""

import importlib.util
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "apps/public/standard/people"


def _load_people_app():
    pkg = types.ModuleType("eos_apps")
    pkg.__path__ = []
    sys.modules.setdefault("eos_apps", pkg)
    ppkg = types.ModuleType("eos_apps.people")
    ppkg.__path__ = [str(_ROOT)]
    sys.modules.setdefault("eos_apps.people", ppkg)
    for sub in ("simulate", "app"):
        name = f"eos_apps.people.{sub}"
        if name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(name, _ROOT / f"{sub}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules["eos_apps.people.app"].PeopleApp


def _ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture(scope="module")
def stub():
    App = _load_people_app()

    class _Stub:
        pass

    for m in ("_frequency_days", "_get_overdue", "_reach_out_candidates", "_days_since"):
        setattr(_Stub, m, getattr(App, m))
    _Stub._COLD_DAYS = App._COLD_DAYS
    return _Stub()


def _people():
    return [
        {"id": "alice", "name": "Alice", "contact_frequency": "monthly",
         "last_contact": _ago(80), "days_since": 80, "energy": "gives",
         "relationship": "friend", "overdue_ratio": 0},
        {"id": "bob", "name": "Bob", "contact_frequency": "weekly",
         "last_contact": _ago(3), "days_since": 3, "energy": "",
         "relationship": "colleague"},
        {"id": "cara", "name": "Cara", "contact_frequency": "quarterly",
         "last_contact": "", "days_since": None, "energy": "",
         "relationship": "mentor"},
        {"id": "dan", "name": "Dan", "contact_frequency": "",
         "last_contact": _ago(90), "days_since": 90, "energy": "gives",
         "relationship": "friend"},
        {"id": "eve", "name": "Eve", "contact_frequency": "",
         "last_contact": _ago(200), "days_since": 200, "energy": "drains",
         "relationship": "friend"},
        {"id": "fin", "name": "Fin", "contact_frequency": "",
         "last_contact": _ago(200), "days_since": 200, "energy": "",
         "relationship": ""},
        {"id": "gus", "name": "Gus", "contact_frequency": "",
         "last_contact": _ago(10), "days_since": 10, "energy": "gives",
         "relationship": "friend"},
    ]


def test_overdue_cadence_surfaces_first(stub):
    res = stub._reach_out_candidates(_people())
    ids = [c["id"] for c in res]
    assert ids[0] == "alice"


def test_recent_and_excluded_people_dropped(stub):
    ids = [c["id"] for c in stub._reach_out_candidates(_people())]
    # bob/gus contacted recently; eve drains; fin has no relationship declared
    for excluded in ("bob", "gus", "eve", "fin"):
        assert excluded not in ids


def test_never_logged_reason(stub):
    res = {c["id"]: c for c in stub._reach_out_candidates(_people())}
    assert res["cara"]["reason"].startswith("never logged")


def test_gone_quiet_without_cadence(stub):
    res = {c["id"]: c for c in stub._reach_out_candidates(_people())}
    assert res["dan"]["reason"] == "no contact in 90d"


def test_cadence_tier_precedes_cold_tier(stub):
    ids = [c["id"] for c in stub._reach_out_candidates(_people())]
    assert ids.index("dan") > ids.index("alice")
    assert ids.index("dan") > ids.index("cara")


def test_empty_when_nobody_due(stub):
    fresh = [{"id": "x", "name": "X", "contact_frequency": "monthly",
              "last_contact": _ago(2), "days_since": 2, "energy": "gives",
              "relationship": "friend"}]
    assert stub._reach_out_candidates(fresh) == []
