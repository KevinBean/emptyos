"""Unit tests for apps/company/lenses.py — members → lens-list conversion.

Pure unit tests — no daemon. The helper is the testable seam of the
``_run_in_room`` multi-lens digest path: testing it covers the lens
shape the live SDK call receives. The surrounding call (try/except
+ ``record["digest"]`` mutation) is mechanical glue.
"""

from __future__ import annotations

import sys

# company moved under public/standard/ in the 2026-05-30 reorg — app_path
# resolves it wherever it lives rather than hardcoding apps/company.
from helpers import app_path

APP_DIR = app_path("company")
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from lenses import members_to_lenses  # noqa: E402


def test_empty_members_returns_empty_list():
    assert members_to_lenses([]) == []
    assert members_to_lenses(None) == []  # type: ignore[arg-type]


def test_member_with_only_name():
    out = members_to_lenses([{"name": "Maya"}])
    assert out == [{"name": "Maya", "focus": "this member's perspective"}]


def test_member_with_role_only_uses_role_as_focus():
    out = members_to_lenses([{"name": "Maya", "role": "Engineer"}])
    assert out == [{"name": "Maya", "focus": "Engineer"}]


def test_member_with_role_and_system_prompt_joins_with_em_dash():
    out = members_to_lenses([{
        "name": "Maya", "role": "Engineer", "system_prompt": "Be sharp.",
    }])
    assert out == [{"name": "Maya", "focus": "Engineer — Be sharp."}]


def test_long_system_prompt_is_truncated_to_240_chars():
    long = "x" * 500
    out = members_to_lenses([{
        "name": "A", "role": "R", "system_prompt": long,
    }])
    focus = out[0]["focus"]
    # role + " — " + 240 of x
    assert focus == "R — " + "x" * 240
    assert len(focus) == len("R — ") + 240


def test_name_fallback_chain_uses_role_when_no_name():
    out = members_to_lenses([{"role": "Engineer"}])
    assert out == [{"name": "Engineer", "focus": "Engineer"}]


def test_name_fallback_chain_uses_id_when_no_name_or_role():
    out = members_to_lenses([{"id": "m-42", "system_prompt": "Be brief."}])
    assert out == [{"name": "m-42", "focus": "Be brief."}]


def test_drops_member_with_no_resolvable_name():
    out = members_to_lenses([
        {"system_prompt": "anonymous"},   # no name/role/id → dropped
        {"name": "Maya"},                 # kept
    ])
    assert out == [{"name": "Maya", "focus": "this member's perspective"}]


def test_skips_non_dict_entries():
    out = members_to_lenses([
        "stringy",                        # type: ignore[list-item]
        {"name": "Maya"},
        42,                                # type: ignore[list-item]
        None,                              # type: ignore[list-item]
    ])
    assert out == [{"name": "Maya", "focus": "this member's perspective"}]


def test_preserves_member_order():
    out = members_to_lenses([
        {"name": "A", "role": "alpha"},
        {"name": "B", "role": "beta"},
        {"name": "C", "role": "gamma"},
    ])
    assert [L["name"] for L in out] == ["A", "B", "C"]


def test_whitespace_in_fields_is_stripped():
    out = members_to_lenses([
        {"name": "  Maya  ", "role": "  Engineer  ", "system_prompt": "  ok  "},
    ])
    assert out == [{"name": "Maya", "focus": "Engineer — ok"}]


def test_none_valued_fields_do_not_crash():
    # YAML-None safety: a key present-but-None must not raise.
    out = members_to_lenses([
        {"name": "Maya", "role": None, "system_prompt": None},
    ])
    assert out == [{"name": "Maya", "focus": "this member's perspective"}]
