"""The personal-pattern files never reach a public snapshot, and the snapshot
scan still has teeth without them.

Until 2026-09-24 three tracked files shipped the strings `.eos-personal` guards:
the pattern file itself, the pattern-coverage test's example inputs, and
`sync_user_skills.py`'s substitution table. All three were on
`check-personal.py`'s ALLOWLIST, which is exactly how they passed the release
scan. The fix has four parts, one pinned per class below:

  - `release-public.py` sweeps both `.eos-personal*` files as cruft;
  - its snapshot scan passes the PRIVATE pattern file and turns the allowlist off
    (driven through `run_scans`, the call site, not read out of a string);
  - `check-personal.py --patterns` fails on an empty pattern set instead of
    reporting clean, and `--no-allowlist` stops the allowlist muting a file;
  - `sync_user_skills.py` reads its table from `.eos-personal-subs` and refuses
    to sync without it.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cp = _load("check_personal_snap", "check-personal.py")
rp = _load("release_public_snap", "release-public.py")
sus = _load("sync_user_skills_snap", "sync_user_skills.py")


def _exit_code(fn) -> int:
    try:
        fn()
    except SystemExit as e:
        return e.code
    return 0


# ── release-public ───────────────────────────────────────────────────────────

class TestReleaseDropsThePatternFiles:
    def test_both_files_are_cruft(self):
        assert ".eos-personal" in rp.CRUFT_PATHS
        assert ".eos-personal-subs" in rp.CRUFT_PATHS

    def test_snapshot_scan_uses_private_patterns_without_allowlist(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(rp, "run", lambda cmd, cwd=None, **k: calls.append(cmd))
        monkeypatch.setattr(rp, "check_no_private_apps", lambda cwd: None)
        rp.run_scans(tmp_path)
        personal = [c for c in calls if c[1].endswith("check-personal.py")]
        assert len(personal) == 1
        cmd = personal[0]
        i = cmd.index("--patterns")
        assert Path(cmd[i + 1]) == rp.ROOT / ".eos-personal"
        assert "--no-allowlist" in cmd

    def test_working_tree_scan_keeps_default_behaviour(self, monkeypatch):
        calls = []
        monkeypatch.setattr(rp, "run", lambda cmd, cwd=None, **k: calls.append(cmd))
        monkeypatch.setattr(rp, "check_no_private_apps", lambda cwd: None)
        rp.run_scans(None)
        personal = [c for c in calls if c[1].endswith("check-personal.py")]
        assert personal and "--patterns" not in personal[0] and "--no-allowlist" not in personal[0]


# ── check-personal flags ─────────────────────────────────────────────────────

def _run_checker(tmp_path, monkeypatch, argv, *, allowlisted: bool):
    f = tmp_path / "doc.md"
    f.write_text("our client is AcmeCorp\n", encoding="utf-8")
    pats = tmp_path / "pats"
    pats.write_text(r"(?i)\bAcmeCorp\b" + "\n", encoding="utf-8")
    monkeypatch.setattr(cp, "get_files", lambda *a, **k: [str(f)])
    monkeypatch.setattr(cp, "PATTERNS_FILE", str(pats))
    if allowlisted:
        monkeypatch.setattr(cp, "ALLOWLIST", {str(f)})
    monkeypatch.setattr(sys, "argv", ["check-personal.py", *argv])
    return _exit_code(cp.main)


class TestCheckPersonalFlags:
    def test_allowlist_mutes_by_default(self, tmp_path, monkeypatch):
        assert _run_checker(tmp_path, monkeypatch, [], allowlisted=True) == 0

    def test_no_allowlist_scans_an_allowlisted_file(self, tmp_path, monkeypatch):
        assert _run_checker(tmp_path, monkeypatch, ["--no-allowlist"], allowlisted=True) == 1

    def test_explicit_patterns_are_used(self, tmp_path, monkeypatch):
        other = tmp_path / "other"
        other.write_text(r"(?i)\bNotPresent\b" + "\n", encoding="utf-8")
        # PATTERNS_FILE would fire; --patterns points elsewhere and must win.
        assert _run_checker(tmp_path, monkeypatch, ["--patterns", str(other)], allowlisted=False) == 0

    def test_explicit_missing_patterns_fail_instead_of_clean(self, tmp_path, monkeypatch):
        code = _run_checker(tmp_path, monkeypatch, ["--patterns", str(tmp_path / "nope")], allowlisted=False)
        assert code == 2

    def test_equals_form_is_read_too(self, tmp_path, monkeypatch):
        # `--patterns=path` used to be ignored, falling back to the default file.
        code = _run_checker(tmp_path, monkeypatch, [f"--patterns={tmp_path / 'nope'}"], allowlisted=False)
        assert code == 2


# ── sync_user_skills ─────────────────────────────────────────────────────────

class TestSyncSubsFile:
    def test_parses_literal_placeholder_lines(self, tmp_path):
        p = tmp_path / "subs"
        # A comment that contains the separator, and a line without one: both
        # must be skipped, or a note in the file becomes a substitution.
        p.write_text(
            "# old => new is the format\n\nno separator here\n"
            "X:\\Home Dir => {vault}\nhost.example => {homepc}\n",
            encoding="utf-8",
        )
        assert sus.load_subs(p) == [("X:\\Home Dir", "{vault}"), ("host.example", "{homepc}")]
        assert sus.scrub("at X:\\Home Dir\\n", sus.load_subs(p)) == "at {vault}\\n"

    def test_missing_file_is_empty(self, tmp_path):
        assert sus.load_subs(tmp_path / "absent") == []

    def test_sync_refuses_without_subs(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        store.mkdir()
        monkeypatch.setattr(sus, "GLOBAL", store)
        monkeypatch.setattr(sus, "_shared_slugs", lambda: ["some-skill"])
        monkeypatch.setattr(sus, "load_personal_patterns", lambda p: [re.compile("x")])
        monkeypatch.setattr(sus, "SUBS_FILE", tmp_path / "absent")
        monkeypatch.setattr(sys, "argv", ["sync_user_skills.py", "--check"])
        assert sus.main() == 2
