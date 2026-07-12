"""ICS TEXT escaping for bookme's anonymous booking form.

`POST /bookme/api/public/book` is auth-exempt (`[provides.web] public_routes`),
so `name` and `email` are attacker-controlled and only length-checked. They land
in a CRLF-delimited .ics that the calendar OWNER downloads and imports, so an
unescaped newline injects arbitrary calendar properties (RFC 5545 §3.3.11).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[1] / "apps/public/standard/bookme/app.py"


def _load():
    spec = importlib.util.spec_from_file_location("bookme_under_test", _APP)
    if spec is None or spec.loader is None:  # pragma: no cover
        # Runs at import time, so a bare pytest.skip() would raise
        # "Using pytest.skip outside of a test" instead of skipping.
        pytest.skip("bookme app.py not loadable", allow_module_level=True)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bookme = _load()


def _booking(name: str, email: str = "a@b.invalid") -> dict:
    return {
        "id": "bk-0123456789",
        "event_name": "Intro call",
        "name": name,
        "email": email,
        "start": "2026-07-11T09:00",
        "end": "2026-07-11T09:30",
    }


def test_benign_booking_builds_a_wellformed_ics():
    ics = bookme._build_ics(_booking("Jordan Avery"))
    assert "DESCRIPTION:Booked by Jordan Avery (a@b.invalid)" in ics
    assert ics.count("BEGIN:VEVENT") == 1 and ics.count("END:VEVENT") == 1


def test_crlf_in_name_cannot_inject_a_calendar_property():
    ics = bookme._build_ics(_booking("Bob\r\nATTENDEE:mailto:evil@example.invalid"))
    lines = ics.split("\r\n")
    assert not any(ln.startswith("ATTENDEE") for ln in lines), lines
    # One property per physical line: no line may be the injected payload.
    assert sum(1 for ln in lines if ln.startswith("DESCRIPTION")) == 1


def test_bare_newline_in_name_cannot_add_an_event():
    ics = bookme._build_ics(_booking("Bob\nEND:VEVENT\nBEGIN:VEVENT\nSUMMARY:Pwned"))
    lines = ics.split("\r\n")
    # Oracle must be line-anchored: the escaped payload legitimately contains
    # the *substring* "BEGIN:VEVENT" inside DESCRIPTION. What matters is that no
    # physical line IS that property — that is what a parser acts on.
    assert sum(1 for ln in lines if ln == "BEGIN:VEVENT") == 1, lines
    assert sum(1 for ln in lines if ln == "END:VEVENT") == 1, lines
    assert not any(ln.startswith("SUMMARY:Pwned") for ln in lines), lines


def test_rfc5545_special_chars_are_escaped():
    out = bookme._ics_text("a;b,c\\d")
    assert out == "a\\;b\\,c\\\\d"


def test_control_characters_are_dropped():
    assert "\x00" not in bookme._ics_text("a\x00b")
