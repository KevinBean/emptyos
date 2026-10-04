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

    def test_snapshot_scan_points_both_scanners_at_the_snapshot(self, tmp_path, monkeypatch):
        # Without --root each scanner lists files from the private repo's git
        # index, so a file that exists only in the snapshot is never scanned.
        calls = []
        monkeypatch.setattr(rp, "run", lambda cmd, cwd=None, **k: calls.append(cmd))
        monkeypatch.setattr(rp, "check_no_private_apps", lambda cwd: None)
        rp.run_scans(tmp_path)
        for script in ("check-personal.py", "check-branding.py"):
            (cmd,) = [c for c in calls if c[1].endswith(script)]
            assert Path(cmd[cmd.index("--root") + 1]) == tmp_path, script

    def test_working_tree_scan_passes_no_root(self, monkeypatch):
        calls = []
        monkeypatch.setattr(rp, "run", lambda cmd, cwd=None, **k: calls.append(cmd))
        monkeypatch.setattr(rp, "check_no_private_apps", lambda cwd: None)
        rp.run_scans(None)
        scans = [c for c in calls if c[1].endswith(("check-personal.py", "check-branding.py"))]
        assert len(scans) == 2 and not any("--root" in c for c in scans)


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


# ── --root scans the snapshot itself ─────────────────────────────────────────
#
# Unmocked `get_files`: the file lives only in a temp "snapshot", never in this
# repo's git index, which is exactly the file the old scan could not see.

cb = _load("check_branding_snap", "check-branding.py")


def _snapshot(tmp_path: Path, rel: str, text: str) -> Path:
    snap = tmp_path / "snap"
    target = snap / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return snap


class TestRootScope:
    def _personal(self, tmp_path, monkeypatch, text: str) -> int:
        snap = _snapshot(tmp_path, "docs/snapshot-only.md", text)
        pats = tmp_path / "pats"
        pats.write_text(r"(?i)\bAcmeCorp\b" + "\n", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", [
            "check-personal.py", "--root", str(snap),
            "--patterns", str(pats), "--no-allowlist",
        ])
        return _exit_code(cp.main)

    def test_personal_finds_a_file_only_the_snapshot_has(self, tmp_path, monkeypatch):
        assert self._personal(tmp_path, monkeypatch, "our client is AcmeCorp\n") == 1

    def test_personal_clean_snapshot_passes(self, tmp_path, monkeypatch, capsys):
        assert self._personal(tmp_path, monkeypatch, "nothing to see\n") == 0
        assert "(1 files" in capsys.readouterr().out

    def test_personal_root_must_exist(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["check-personal.py", "--root", str(tmp_path / "nope")])
        assert _exit_code(cp.main) == 2

    def _branding(self, tmp_path, monkeypatch, *, with_patterns: bool) -> int:
        snap = _snapshot(tmp_path, "apps/x/pages/index.html", "<p>Open in Acmebrandx</p>\n")
        if with_patterns:
            (snap / ".eos-branding").write_text(r"(?i)\bAcmebrandx\b" + "\n", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["check-branding.py", "--root", str(snap)])
        return _exit_code(cb.main)

    def test_branding_finds_a_file_only_the_snapshot_has(self, tmp_path, monkeypatch):
        assert self._branding(tmp_path, monkeypatch, with_patterns=True) == 1

    def test_branding_snapshot_without_patterns_refuses_to_pass(self, tmp_path, monkeypatch):
        assert self._branding(tmp_path, monkeypatch, with_patterns=False) == 2

    def test_branding_exemptions_match_root_relative_paths(self, tmp_path, monkeypatch):
        # `docs/` is exempt; an absolute path would never match the prefix and
        # the same hit would fail every release.
        snap = _snapshot(tmp_path, "docs/notes.md", "Open in Acmebrandx\n")
        (snap / ".eos-branding").write_text(r"(?i)\bAcmebrandx\b" + "\n", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["check-branding.py", "--root", str(snap)])
        assert _exit_code(cb.main) == 0

    def test_personal_root_without_patterns_refuses_to_pass(self, tmp_path, monkeypatch):
        snap = _snapshot(tmp_path, "docs/a.md", "x\n")
        monkeypatch.setattr(cp, "PATTERNS_FILE", str(tmp_path / "absent"))
        monkeypatch.setattr(sys, "argv", ["check-personal.py", "--root", str(snap)])
        assert _exit_code(cp.main) == 2


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
