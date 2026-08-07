"""Pin `scripts/check_skill_script_sync.py` in both directions.

The guard exists because on 2026-08-05 the `.claude` copy of the conversation-
ingest writer was 162 lines behind `.agents` and lacked three functions — so a
spec key it did not know (`repair_source_digest_link`) was accepted and silently
discarded while the runner reported success.

Both directions matter equally here. A checker that only proves it *fires* can
still be one that fires on everything; a checker that only proves it *stays
quiet* can be one that never fires at all. The real tree is asserted separately
(and is currently 27/27 identical), so a regression there fails too.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCANNER = REPO / "scripts" / "check_skill_script_sync.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("check_skill_script_sync", SCANNER)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _skill(root: Path, name: str, files: dict[str, str], frontmatter: str = "") -> Path:
    d = root / name
    (d / "scripts").mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\n{frontmatter}---\n\n# {name}\n", encoding="utf-8"
    )
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return d


class TestStaysQuiet:
    def test_identical_pair_is_clean(self, mod, tmp_path):
        a, c = tmp_path / "agents", tmp_path / "claude"
        for root in (a, c):
            _skill(root, "demo", {"scripts/run.py": "x = 1\n"})
        r = mod.scan(a, c)
        assert r["findings"] == []
        assert r["pairs"] == 1

    def test_line_endings_alone_are_not_drift(self, mod, tmp_path):
        """CRLF vs LF is a checkout artefact, not a behavioural difference.

        Written as bytes on purpose: `write_text` applies platform newline
        translation, so a `"\\r\\n"` literal becomes `\\r\\r\\n` on Windows and
        the fixture stops testing what it claims to.
        """
        a, c = tmp_path / "agents", tmp_path / "claude"
        _skill(a, "demo", {})
        _skill(c, "demo", {})
        (a / "demo" / "scripts" / "run.py").write_bytes(b"x = 1\ny = 2\n")
        (c / "demo" / "scripts" / "run.py").write_bytes(b"x = 1\r\ny = 2\r\n")
        assert mod.scan(a, c)["findings"] == []

    def test_skill_in_one_tree_only_is_advisory_not_gated(self, mod, tmp_path):
        """A Claude-only skill is a legitimate choice — 13 exist in the real tree."""
        a, c = tmp_path / "agents", tmp_path / "claude"
        a.mkdir(); _skill(c, "claude-only", {"scripts/run.py": "x = 1\n"})
        r = mod.scan(a, c)
        assert r["findings"] == [], "a one-tree skill must not gate"
        assert [x["problem"] for x in r["advisories"]] == ["skill-only-in-claude"]

    def test_opt_out_marker_skips_the_skill(self, mod, tmp_path):
        a, c = tmp_path / "agents", tmp_path / "claude"
        _skill(a, "demo", {"scripts/run.py": "x = 1\n"}, frontmatter="script_sync: false\n")
        _skill(c, "demo", {"scripts/run.py": "TOTALLY DIFFERENT\n"})
        r = mod.scan(a, c)
        assert r["findings"] == []
        assert r["skipped"] == ["demo"]


class TestFires:
    def test_diverged_content_is_reported(self, mod, tmp_path):
        a, c = tmp_path / "agents", tmp_path / "claude"
        _skill(a, "demo", {"scripts/run.py": "def repair(): pass\n"})
        _skill(c, "demo", {"scripts/run.py": "pass\n"})
        r = mod.scan(a, c)
        assert len(r["findings"]) == 1
        assert r["findings"][0]["problem"] == "differs"
        assert r["findings"][0]["file"] == "scripts/run.py"

    def test_script_missing_from_claude_is_reported(self, mod, tmp_path):
        """The shape that made a whole runner invisible to Claude Code."""
        a, c = tmp_path / "agents", tmp_path / "claude"
        _skill(a, "demo", {"scripts/run.py": "x = 1\n", "scripts/extra.py": "y = 2\n"})
        _skill(c, "demo", {"scripts/run.py": "x = 1\n"})
        r = mod.scan(a, c)
        assert [f["problem"] for f in r["findings"]] == ["missing-in-claude"]
        assert r["findings"][0]["file"] == "scripts/extra.py"

    def test_script_missing_from_agents_is_reported(self, mod, tmp_path):
        a, c = tmp_path / "agents", tmp_path / "claude"
        _skill(a, "demo", {"scripts/run.py": "x = 1\n"})
        _skill(c, "demo", {"scripts/run.py": "x = 1\n", "scripts/extra.py": "y = 2\n"})
        r = mod.scan(a, c)
        assert [f["problem"] for f in r["findings"]] == ["missing-in-agents"]

    def test_pycache_is_ignored(self, mod, tmp_path):
        """Compiled artefacts differ constantly and mean nothing."""
        a, c = tmp_path / "agents", tmp_path / "claude"
        _skill(a, "demo", {"scripts/run.py": "x = 1\n",
                           "scripts/__pycache__/run.cpython-313.py": "A\n"})
        _skill(c, "demo", {"scripts/run.py": "x = 1\n"})
        assert mod.scan(a, c)["findings"] == []

    def test_exit_code_is_the_finding_count(self, mod, tmp_path, monkeypatch, capsys):
        a, c = tmp_path / "agents", tmp_path / "claude"
        _skill(a, "demo", {"scripts/one.py": "A\n", "scripts/two.py": "B\n"})
        _skill(c, "demo", {"scripts/one.py": "X\n", "scripts/two.py": "Y\n"})
        monkeypatch.setattr(mod, "AGENTS_ROOT", a)
        monkeypatch.setattr(mod, "CLAUDE_ROOT", c)
        assert mod.main([]) == 2
        assert "DRIFT" in capsys.readouterr().out


class TestRealTree:
    def test_the_repo_is_currently_in_sync(self, mod):
        """Asserted on the real tree, not a fixture.

        Measured 2026-08-05: 27 shared pairs, 0 drifted, after syncing four test
        files and two scripts that `.claude` was missing or behind on.
        """
        r = mod.scan(mod.AGENTS_ROOT, mod.CLAUDE_ROOT)
        assert r["findings"] == [], (
            "skill scripts have drifted between .agents/ and .claude/: "
            + "; ".join(f"{f['skill']}/{f['file']} ({f['problem']})" for f in r["findings"])
        )
        assert r["pairs"] > 0, "scanner found no pairs at all — check the roots"
