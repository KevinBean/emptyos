"""Unit tests for emptyos.sdk.utils helpers.

Pure functions — no daemon needed at the function level, but the autouse
``server_health`` fixture in conftest.py will skip the suite if the daemon
is down (project convention).
"""

from __future__ import annotations

import re
from datetime import date

from emptyos.sdk import today_iso
from emptyos.sdk.utils import today_iso as today_iso_direct


def test_today_iso_format():
    s = today_iso()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s), f"not ISO date: {s!r}"


def test_today_iso_matches_date_today():
    assert today_iso() == date.today().isoformat()


def test_today_iso_export_paths_agree():
    assert today_iso is today_iso_direct
