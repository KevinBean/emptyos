"""Unit tests for scripts/check_settings_dead_toggle.py.

The checker exists because a `[provides.settings]` key read with `app_config`
alone is a toggle the user can flip and the app never reads: the panel writes
to the settings service, `app_config` reads `emptyos.toml`. Fixed by hand in
four apps on 2026-07-17; the first run of the scanner found 32 keys in 11 apps
on 2026-10-01.

Two properties matter, and the second is the one that keeps a checker alive
(`.claude/rules/audits.md`):
  1. It flags the two shapes every regression took — the schema key read with
     its `<app-id>.` prefix stripped (viz: `viz.think_domain` read as
     `app_config("think_domain")`), and read verbatim (library).
  2. It does NOT fire on a wired key, whatever the read shape — plain
     `setting()`, `setting_or_config()` with or without `config_key=`, or an
     inline opt-out — and a settings read of a DIFFERENT key must not cover
     this one (the `check_field_authors` trap: excluding any file that calls
     the helper silences the very finding the check exists for).

Fixtures are shaped like real apps: a nested track-tree path, an `[app]` table,
a module docstring ahead of the code. A fixture that two exclusions would skip
pins neither (`.claude/rules/audits-casebook.md`), so nothing here sits under
`apps/personal/` or `_retired/` unless that is the rule under test.
"""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_settings_dead_toggle.py"
_spec = importlib.util.spec_from_file_location("check_settings_dead_toggle", _SCRIPT)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def _app(repo: Path, *, app_id: str, keys: list[str], source: str,
         track: str = "public/standard", manifest: str | None = None) -> Path:
    """One app under a miniature repo: manifest with a schema + an app.py."""
    d = repo / "apps" / Path(track) / app_id
    d.mkdir(parents=True, exist_ok=True)
    if manifest is None:
        schema = ", ".join(
            f'{{key = "{k}", label = "L", type = "text", default = ""}}' for k in keys
        )
        manifest = f'[app]\nid = "{app_id}"\n\n[provides.settings]\nschema = [{schema}]\n'
    (d / "manifest.toml").write_text(manifest, encoding="utf-8")
    (d / "app.py").write_text(
        '"""A fixture app, shaped like a real one: docstring first."""\n\n'
        "class Demo:\n" + "".join(f"    {line}\n" for line in source.splitlines()),
        encoding="utf-8",
    )
    return repo


# ── 1. It flags the two regression shapes ──────────────────────────────────

def test_flags_a_schema_key_read_with_its_prefix_stripped(tmp_path):
    """viz: schema `viz.think_domain`, source `app_config("think_domain")`."""
    repo = _app(tmp_path, app_id="viz", keys=["viz.think_domain"],
                source='def f(self):\n    return self.app_config("think_domain", "code")')
    (d,) = checker.scan(repo)["dead"]
    assert d["app"] == "viz"
    assert d["key"] == "viz.think_domain"
    assert d["config_key"] == "think_domain"


def test_flags_a_schema_key_read_verbatim(tmp_path):
    """library: schema `library.crossref_mailto`, same string in app_config."""
    repo = _app(tmp_path, app_id="library", keys=["library.crossref_mailto"],
                source='def f(self):\n    return self.app_config("library.crossref_mailto", "")')
    (d,) = checker.scan(repo)["dead"]
    assert d["key"] == d["config_key"] == "library.crossref_mailto"


def test_flags_a_dead_key_in_the_extension_track(tmp_path):
    """Two of the eleven regressions (model-bench, pattern-harvester) lived under
    apps/extension/; a scanner that only walks apps/public/ passes every other test."""
    repo = _app(tmp_path, app_id="mb", keys=["mb.a"], track="extension/dev",
                source='def f(self):\n    return self.app_config("a", 1)')
    assert [d["app"] for d in checker.scan(repo)["dead"]] == ["mb"]


def test_single_quoted_key_is_the_same_key(tmp_path):
    repo = _app(tmp_path, app_id="demo", keys=["demo.a"],
                source="def f(self):\n    return self.app_config('a', 1)")
    assert [d["key"] for d in checker.scan(repo)["dead"]] == ["demo.a"]


# ── 2. It is silent on a wired key, whatever the read shape ───────────────

def test_silent_when_setting_or_config_reads_the_schema_key(tmp_path):
    repo = _app(tmp_path, app_id="viz", keys=["viz.think_domain"],
                source='def f(self):\n    return self.setting_or_config('
                       '"viz.think_domain", "code", config_key="think_domain")')
    assert checker.scan(repo)["dead"] == []


def test_silent_when_the_config_key_differs_and_app_config_also_appears(tmp_path):
    """model-bench shape after the fix: the TOML key stays `feature.x.enabled`
    for check_dark_flags.py, and a boot-time path may still read it raw."""
    repo = _app(tmp_path, app_id="mb", keys=["mb.feature.x.enabled"],
                source='def f(self):\n    return self.setting_or_config('
                       '"mb.feature.x.enabled", False, config_key="feature.x.enabled")\n'
                       'def g(self):\n    return self.app_config("feature.x.enabled", False)')
    assert checker.scan(repo)["dead"] == []


def test_silent_when_plain_setting_reads_the_key(tmp_path):
    repo = _app(tmp_path, app_id="hub", keys=["hub.smart_route"],
                source='def f(self):\n    return self.setting("hub.smart_route", True)')
    assert checker.scan(repo)["dead"] == []


def test_a_settings_read_of_another_key_does_not_cover_this_one(tmp_path):
    """The narrowing that matters: per key, not per file."""
    repo = _app(tmp_path, app_id="demo", keys=["demo.a", "demo.b"],
                source='def f(self):\n    return self.setting_or_config("demo.a", 1)\n'
                       'def g(self):\n    return self.app_config("b", 2)')
    assert [d["key"] for d in checker.scan(repo)["dead"]] == ["demo.b"]


def test_inline_marker_records_a_deliberate_toml_only_read(tmp_path):
    repo = _app(tmp_path, app_id="demo", keys=["demo.a"],
                source='def f(self):\n    # settings-dead-toggle: ignore demo.a\n'
                       '    return self.app_config("a", 1)')
    assert checker.scan(repo)["dead"] == []


def test_marker_silences_only_the_key_it_names(tmp_path):
    """The docstring promises per-key opt-out; an app-wide silence must not pass."""
    repo = _app(tmp_path, app_id="demo", keys=["demo.a", "demo.b"],
                source='def f(self):\n    # settings-dead-toggle: ignore demo.a\n'
                       '    return (self.app_config("a", 1), self.app_config("b", 2))')
    assert [d["key"] for d in checker.scan(repo)["dead"]] == ["demo.b"]


def test_pages_are_not_source(tmp_path):
    """A .py under pages/ is served, not run; a read there wires nothing."""
    repo = _app(tmp_path, app_id="demo", keys=["demo.a"],
                source='def f(self):\n    return self.app_config("a", 1)')
    pages = repo / "apps/public/standard/demo/pages"
    pages.mkdir()
    (pages / "helper.py").write_text('x = self.setting_or_config("demo.a", 1)\n', encoding="utf-8")
    assert [d["key"] for d in checker.scan(repo)["dead"]] == ["demo.a"]


# ── 3. The advisory class never gates ──────────────────────────────────────

def test_a_key_read_by_neither_helper_is_advisory_not_dead(tmp_path):
    repo = _app(tmp_path, app_id="journal", keys=["journal.default_mood"],
                source="def f(self):\n    return 1")
    result = checker.scan(repo)
    assert result["dead"] == []
    assert [u["key"] for u in result["unread"]] == ["journal.default_mood"]


def test_exit_code_is_zero_with_only_advisory_findings(tmp_path, monkeypatch, capsys):
    _app(tmp_path, app_id="journal", keys=["journal.default_mood"],
         source="def f(self):\n    return 1")
    monkeypatch.setattr(checker, "REPO", tmp_path)
    assert checker.main([]) == 0
    assert "advisory" in capsys.readouterr().out


# ── 4. Exit code and scope ─────────────────────────────────────────────────

def test_exit_code_is_one_not_the_count(tmp_path, monkeypatch, capsys):
    """Two findings → 1. `return len(dead)` would also pass a one-finding fixture."""
    _app(tmp_path, app_id="demo", keys=["demo.a", "demo.b"],
         source='def f(self):\n    return (self.app_config("a", 1), self.app_config("b", 2))')
    monkeypatch.setattr(checker, "REPO", tmp_path)
    assert checker.main([]) == 1
    out = capsys.readouterr().out
    assert "2 declared setting(s)" in out
    assert 'config_key="a"' in out  # the human output names the fix


def test_json_envelope_mirrors_the_exit_code(tmp_path, monkeypatch, capsys):
    import json
    _app(tmp_path, app_id="demo", keys=["demo.a"],
         source='def f(self):\n    return self.app_config("a", 1)')
    monkeypatch.setattr(checker, "REPO", tmp_path)
    assert checker.main(["--json"]) == 1
    env = json.loads(capsys.readouterr().out)
    assert env["ok"] is False
    assert env["code"] == "settings_dead_toggle"
    assert env["data"]["dead"][0]["key"] == "demo.a"


def test_json_mode_reports_a_bad_manifest_as_an_envelope(tmp_path, monkeypatch, capsys):
    """`--json` must print exactly one JSON object even when scan() fails loud."""
    import json
    _app(tmp_path, app_id="demo", keys=[], source="x = 1", manifest='[app\nid = "demo"\n')
    monkeypatch.setattr(checker, "REPO", tmp_path)
    assert checker.main(["--json"]) == 1
    env = json.loads(capsys.readouterr().out)
    assert env["ok"] is False
    assert env["code"] == "bad_manifest"
    assert "apps/public/standard/demo/manifest.toml" in env["message"]


def test_retired_apps_are_skipped(tmp_path):
    repo = _app(tmp_path, app_id="old", keys=["old.a"], track="public/_retired",
                source='def f(self):\n    return self.app_config("a", 1)')
    assert checker.scan(repo) == {"dead": [], "unread": []}


def test_personal_track_is_skipped_at_any_depth(tmp_path):
    """apps/personal/labs/<id>/ is a documented layout at the scanned depth; it is
    gitignored, so a finding there is noise nobody on another machine can act on."""
    repo = _app(tmp_path, app_id="mine", keys=["mine.a"], track="personal/labs",
                source='def f(self):\n    return self.app_config("a", 1)')
    assert checker.scan(repo) == {"dead": [], "unread": []}


def test_a_manifest_it_cannot_parse_fails_loud(tmp_path):
    """A swallowed parse error would report 'no findings' for an app the
    loader also rejects (`.claude/rules/audits-casebook.md`)."""
    _app(tmp_path, app_id="demo", keys=[], source="x = 1",
         manifest='[app\nid = "demo"\n')
    with pytest.raises(tomllib.TOMLDecodeError):
        checker.scan(tmp_path)


# ── 5. The live tree ───────────────────────────────────────────────────────

def test_live_tree_has_no_dead_toggle():
    """Assert only the gating class — the advisory list is other people's work."""
    dead = checker.scan()["dead"]
    assert dead == [], "\n".join(f'{d["app"]}: {d["key"]}' for d in dead)
