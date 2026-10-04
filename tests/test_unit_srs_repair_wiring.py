"""The two loaders actually call `repair_legacy_schedule` — daemon-free.

`tests/test_sdk_srs.py` proves the repair function is correct. It cannot prove
anything calls it, and that is the half that fails silently: drop either call
site and every test still passes, while a pre-FSRS entry booked past the
horizon quietly goes back to never coming due. The symptom is an item that
simply never appears — there is nothing to notice.

Both modules are loaded straight off disk rather than through the app loader:
their only relative import (`from .app import ...`) sits under `TYPE_CHECKING`,
so it never executes, and both loaders are module-level functions taking `self`
per the multi-module-app convention — so a stub with one path method is a real
call, not a mock of one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from emptyos.sdk.srs import MAX_INTERVAL_DAYS

REPO = Path(__file__).resolve().parent.parent
TODAY = date.today()
RUNAWAY = (TODAY + timedelta(days=14064)).isoformat()   # rep 9 under the old scheduler
HORIZON = (TODAY + timedelta(days=MAX_INTERVAL_DAYS)).isoformat()
LEGAL = (TODAY + timedelta(days=200)).isoformat()


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Stub:
    """Just enough app for a loader: the one path method it reaches for."""

    def __init__(self, path: Path, attr: str):
        setattr(self, attr, lambda: path)


# (module path, module alias, loader function, path-method the loader calls)
LOADERS = [
    ("apps/public/standard/learn/srs.py", "_learn_srs", "_load_srs", "_srs_path"),
    ("apps/public/englishos/dictionary/pronounce.py",
     "_dict_pronounce", "_load_weak_phones", "_weak_phones_path"),
]
IDS = ["learn", "dictionary"]


@pytest.mark.parametrize(("rel", "alias", "loader", "path_attr"), LOADERS, ids=IDS)
def test_loader_repairs_a_runaway_booking(tmp_path, rel, alias, loader, path_attr):
    store = tmp_path / "store.json"
    store.write_text(json.dumps({
        "stranded": {"ease": 4.9, "review_count": 9, "next_review": RUNAWAY},
        "legal": {"ease": 2.5, "review_count": 2, "next_review": LEGAL},
    }), encoding="utf-8")

    mod = _load(rel, alias)
    data = getattr(mod, loader)(_Stub(store, path_attr))

    assert data["stranded"]["next_review"] == HORIZON, (
        f"{alias}.{loader} no longer repairs pre-FSRS runaway bookings — "
        "the repair call was dropped or bypassed"
    )
    assert data["legal"]["next_review"] == LEGAL, "a legal booking must not move"


@pytest.mark.parametrize(("rel", "alias", "loader", "path_attr"), LOADERS, ids=IDS)
def test_loader_survives_a_missing_store(tmp_path, rel, alias, loader, path_attr):
    """The repair must not turn a first-run empty deck into an exception."""
    mod = _load(rel, alias)
    assert getattr(mod, loader)(_Stub(tmp_path / "absent.json", path_attr)) == {}
