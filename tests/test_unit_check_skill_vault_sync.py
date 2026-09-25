"""Pin the skill↔vault drift checker in both directions.

The value of this checker is entirely in what it does NOT fire on. Measured
2026-07-28, a naive byte-compare over every skill present in both places
flagged 12 of 33, and 7 of those were intentional: the tracked copy uses
``{vault}`` / ``{home}`` placeholders because CLAUDE.md rule 13 bans personal
paths from git, while the vault copy has them expanded. So the opt-in marker is
the mechanism, and "an unmarked skill is never checked" is the property most
worth pinning.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_skill_vault_sync.py"


@pytest.fixture(scope="module")
def checker():
    spec = importlib.util.spec_from_file_location("check_skill_vault_sync", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _skill(root: Path, name: str, body: str, *, marked: bool) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    marker = "\nvault_sync: true" if marked else ""
    text = f"---\nname: {name}\ndescription: x{marker}\n---\n\n{body}\n"
    # write_bytes, not write_text: on Windows write_text translates \n to \r\n,
    # which would make the line-ending test below assert against \r\r\n.
    (d / "SKILL.md").write_bytes(text.encode("utf-8"))
    return d / "SKILL.md"


def _wire(checker, monkeypatch, repo: Path, vault: Path):
    monkeypatch.setattr(checker, "REPO_ROOT", repo)
    monkeypatch.setattr(checker, "SKILL_ROOTS", ("skills",))
    monkeypatch.setattr(checker, "vault_skills_dir", lambda: vault)


def test_unmarked_skill_is_never_checked(checker, tmp_path, monkeypatch):
    """The false-positive guard: placeholder templating must not be flagged."""
    repo, vault = tmp_path / "repo", tmp_path / "vault"
    _skill(repo / "skills", "templated", "path is {vault}/notes", marked=False)
    # A synthetic expanded path, not this machine's real one: the fixture only
    # needs to *differ* from the repo copy's `{vault}` placeholder, and
    # check-personal (CLAUDE.md rule 13) blocks the real value from git — which
    # is the same rule this checker exists to support.
    _skill(vault, "templated", "path is X:/Example Vault/notes", marked=False)
    _wire(checker, monkeypatch, repo, vault)
    findings, checked = checker.check()
    assert checked == []
    assert findings == []


def test_marked_and_identical_is_clean(checker, tmp_path, monkeypatch):
    repo, vault = tmp_path / "repo", tmp_path / "vault"
    _skill(repo / "skills", "synced", "same body", marked=True)
    _skill(vault, "synced", "same body", marked=True)
    _wire(checker, monkeypatch, repo, vault)
    findings, checked = checker.check()
    assert checked == ["synced"]
    assert findings == []


def test_marked_and_drifted_is_reported(checker, tmp_path, monkeypatch):
    repo, vault = tmp_path / "repo", tmp_path / "vault"
    _skill(repo / "skills", "synced", "repo body", marked=True)
    _skill(vault, "synced", "vault body", marked=True)
    _wire(checker, monkeypatch, repo, vault)
    findings, _ = checker.check()
    assert [f["problem"] for f in findings] == ["drifted"]


def test_line_endings_alone_are_not_drift(checker, tmp_path, monkeypatch):
    """A CRLF vault copy of an LF repo file is the same file."""
    repo, vault = tmp_path / "repo", tmp_path / "vault"
    a = _skill(repo / "skills", "synced", "line one\nline two", marked=True)
    b = _skill(vault, "synced", "line one\nline two", marked=True)
    b.write_bytes(a.read_bytes().replace(b"\n", b"\r\n"))
    _wire(checker, monkeypatch, repo, vault)
    findings, _ = checker.check()
    assert findings == []


def test_missing_vault_copy_is_reported(checker, tmp_path, monkeypatch):
    repo, vault = tmp_path / "repo", tmp_path / "vault"
    _skill(repo / "skills", "synced", "body", marked=True)
    vault.mkdir(parents=True, exist_ok=True)
    _wire(checker, monkeypatch, repo, vault)
    findings, _ = checker.check()
    assert [f["problem"] for f in findings] == ["missing in vault"]


def test_marker_must_be_in_frontmatter_not_the_body(checker, tmp_path, monkeypatch):
    """Prose mentioning the marker must not silently opt a skill in."""
    repo, vault = tmp_path / "repo", tmp_path / "vault"
    _skill(
        repo / "skills", "prose",
        "Set `vault_sync: true` to opt in.", marked=False,
    )
    _skill(vault, "prose", "totally different", marked=False)
    _wire(checker, monkeypatch, repo, vault)
    _findings, checked = checker.check()
    assert checked == []


def test_no_vault_configured_reports_rather_than_crashes(
    checker, tmp_path, monkeypatch,
):
    repo = tmp_path / "repo"
    _skill(repo / "skills", "synced", "body", marked=True)
    monkeypatch.setattr(checker, "REPO_ROOT", repo)
    monkeypatch.setattr(checker, "SKILL_ROOTS", ("skills",))
    monkeypatch.setattr(checker, "vault_skills_dir", lambda: None)
    findings, _ = checker.check()
    assert [f["problem"] for f in findings] == ["no vault configured"]


def test_live_tree_is_clean(checker):
    """The real repo must stay in sync — this is the regression net.

    Needs a configured vault: a fresh clone has no emptyos.toml (it ships
    emptyos.example.toml), so `check()` returns the "no vault configured"
    finding and this read as drift rather than as an unconfigured machine.
    The no-vault behaviour is pinned by its own test above.
    """
    findings, checked = checker.check()
    if [f["problem"] for f in findings] == ["no vault configured"]:
        pytest.skip("no vault configured; nothing to reconcile against")
    assert checked, "expected at least one skill marked vault_sync: true"
    assert findings == [], f"skill/vault drift: {findings}"
