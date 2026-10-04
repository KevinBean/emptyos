"""Both directions for the export-groups guard (editions M12).

`export-groups.toml` ships in the public snapshot. Two pieces keep a closed app
out of it: `scripts/check_export_groups.py` fails on a public group that lists
an app the public release does not ship, and `release-public.py`'s
`filter_export_groups_toml` drops every private group, then re-runs the checker
on the snapshot (its preflight row never runs on the release path). Each rule
gets a fixture that must fire next to one that must stay silent.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from check_common import load_by_path  # noqa: E402

chk = load_by_path("check_export_groups_under_test", "scripts/check_export_groups.py")
rp = load_by_path("relpub_export_groups_under_test", "scripts/release-public.py")

KNOWN = {"task", "journal", "canvas", "fix-agent", "mine"}
PUBLIC = {"task", "journal"}  # canvas: an apps/public/labs app no public tier ships


def _codes(findings):
    return [(f["group"], f["app"], f["code"]) for f in findings]


# ── the checker ────────────────────────────────────────────────────────────

def test_live_file_is_clean():
    assert chk.scan() == []


def test_live_operands_are_read():
    # A walk that found nothing would pass everything vacuously.
    assert "task" in chk.app_ids(REPO) and len(chk.app_ids(REPO)) > 50
    assert "task" in chk.public_apps(REPO) and "fix-agent" not in chk.public_apps(REPO)


def test_public_tiers_match_the_release():
    assert tuple(chk.PUBLIC_TIERS) == tuple(rp.PUBLIC_TIERS)


def test_public_apps_in_a_public_group_are_fine():
    assert chk.scan_groups([{"id": "g", "apps": ["task", "journal"]}], KNOWN, PUBLIC) == []


@pytest.mark.parametrize("app", ["fix-agent", "mine", "canvas"])
def test_an_app_the_release_does_not_ship_fires_in_a_public_group(app):
    # canvas is the case a folder rule misses: it lives in apps/public/labs/.
    got = chk.scan_groups([{"id": "g", "apps": ["task", app]}], KNOWN, PUBLIC)
    assert _codes(got) == [("g", app, "closed_app_in_public_group")]


def test_a_private_group_may_hold_closed_and_unknown_apps():
    # Unknown too: a private group may name a personal app absent from a clone.
    groups = [{"id": "g", "private": True, "apps": ["fix-agent", "not-here"]}]
    assert chk.scan_groups(groups, KNOWN, PUBLIC) == []


@pytest.mark.parametrize("flag", ["true", "false", 1, "yes"])
def test_a_non_boolean_private_fires(flag):
    got = chk.scan_groups([{"id": "g", "private": flag, "apps": ["task"]}], KNOWN, PUBLIC)
    assert ("g", "", "private_not_boolean") in _codes(got)


def test_an_unknown_app_in_a_public_group_fires():
    got = chk.scan_groups([{"id": "g", "apps": ["taks"]}], KNOWN, PUBLIC)
    assert _codes(got) == [("g", "taks", "unknown_app")]


@pytest.mark.parametrize("groups", [
    {"id": "g"},                          # not an array of tables
    [{"apps": ["task"]}],                 # no id
    [{"id": "g", "apps": "task"}],        # apps not a list
    [{"id": "g", "apps": ["task", 3]}],   # non-string member
])
def test_input_it_cannot_read_is_an_error(groups):
    with pytest.raises(ValueError):
        chk.scan_groups(groups, KNOWN, PUBLIC)


def _tree(root: Path, groups: str, apps: dict[str, str]) -> Path:
    """A minimal repo: release.toml (core ships `task`), apps, export-groups.toml."""
    (root / "release.toml").write_text(
        '[tiers.core]\napps = ["task"]\n[tiers.standard]\nextends = "core"\napps = ["journal"]\n',
        encoding="utf-8")
    for app_id, track in apps.items():
        d = root / "apps" / track / "g" / app_id
        d.mkdir(parents=True)
        (d / "manifest.toml").write_text(f'[app]\nid = "{app_id}"\n', encoding="utf-8")
    (root / "export-groups.toml").write_text(groups, encoding="utf-8")
    return root


def test_exit_code_is_the_finding_count(tmp_path, monkeypatch, capsys):
    _tree(tmp_path, '[[group]]\nid = "g"\napps = ["fix-agent", "nope"]\n',
          {"task": "public", "fix-agent": "extension"})
    monkeypatch.setattr(chk, "REPO", tmp_path)
    assert chk.main([]) == 2
    assert "closed_app_in_public_group" in capsys.readouterr().out


# ── the release filter ─────────────────────────────────────────────────────

_SAMPLE = '''\
# header prose — always kept

[[group]]
id = "open"
apps = ["task"]

# held bundle: internal consulting kit
[[group]]
id = "held"
private = true
apps = ["fix-agent"]

[[group]]
id = "open-2"
apps = ["journal"]
'''


def test_filter_drops_a_private_group_and_its_intro(tmp_path):
    _tree(tmp_path, _SAMPLE, {"task": "public", "journal": "public"})
    rp.filter_export_groups_toml(tmp_path)
    text = (tmp_path / "export-groups.toml").read_text(encoding="utf-8")
    assert [g["id"] for g in tomllib.loads(text)["group"]] == ["open", "open-2"]
    assert "fix-agent" not in text and "consulting kit" not in text
    assert "header prose" in text


def test_filter_leaves_a_file_with_no_private_group_untouched(tmp_path):
    text = _SAMPLE.replace("private = true\n", "")
    (tmp_path / "export-groups.toml").write_text(text, encoding="utf-8")
    rp._filter_private_blocks(tmp_path, "export-groups.toml", "group", "export group")
    assert (tmp_path / "export-groups.toml").read_text(encoding="utf-8") == text


@pytest.mark.parametrize("text", [
    # an inline array of tables has no [[group]] header to scan for
    'group = [{ id = "open", apps = ["task"] },\n'
    '         { id = "held", private = true, apps = ["fix-agent"] }]\n',
    # a header with a trailing comment swallows the next public group
    '[[group]]\nid = "held"\nprivate = true\napps = ["x"]\n\n[[group]]  # public\nid = "open"\napps = ["task"]\n',
    # a table after a private final group is swallowed with it
    '[[group]]\nid = "held"\nprivate = true\napps = ["x"]\n\n[meta]\nowner = "team"\n',
    # a single [group] table, not an array
    '[group]\nid = "held"\nprivate = true\napps = ["x"]\n',
])
def test_filter_refuses_a_shape_its_block_scan_mis_edits(tmp_path, text):
    (tmp_path / "export-groups.toml").write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit):
        rp._filter_private_blocks(tmp_path, "export-groups.toml", "group", "export group")
    assert (tmp_path / "export-groups.toml").read_text(encoding="utf-8") == text


def test_the_release_path_runs_the_checker_on_the_snapshot(tmp_path):
    # A public group naming an app the snapshot does not hold must stop the
    # release, even though nothing ran preflight first.
    _tree(tmp_path, '[[group]]\nid = "open"\napps = ["task", "fix-agent"]\n',
          {"task": "public"})
    with pytest.raises(SystemExit):
        rp.filter_export_groups_toml(tmp_path)


def test_the_filter_is_called_right_after_the_suites_filter_in_main():
    # Executed structure, not a text grep: a commented-out call, one moved into
    # another branch, or one after the push all fail here.
    tree = ast.parse((REPO / "scripts" / "release-public.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")

    def called(stmt, name):
        return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and getattr(stmt.value.func, "id", None) == name)

    for node in ast.walk(main):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if not isinstance(stmts, list):
                continue
            for i, stmt in enumerate(stmts):
                if called(stmt, "filter_suites_toml"):
                    assert i + 1 < len(stmts) and called(stmts[i + 1], "filter_export_groups_toml"), \
                        "filter_export_groups_toml must directly follow filter_suites_toml"
                    return
    pytest.fail("filter_suites_toml is not called in main()")
