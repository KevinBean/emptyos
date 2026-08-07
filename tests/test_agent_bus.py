"""Tests for scripts/agent_bus.py — the Agent Context Bus ('Ripple').

These are pure unit/integration tests against the script. They do NOT need
a running daemon, which is why they're named `test_agent_bus.py` rather
than `test_sys_*` (system tests imply the daemon-required convention from
.claude/rules/testing.md).
"""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from emptyos.sdk.agent_bus import (  # noqa: E402
    RippleError,
    check_dry_run,
    detect_native_divergence,
    dump_toml,
    load_rule,
    load_section,
    load_skill,
    load_toml,
    run_import,
    run_status,
    run_transpile,
    slugify,
    split_markdown_into_sections,
)


# ─── Parser ────────────────────────────────────────────────────────────────

def test_parser_basic():
    content = (
        "# Title\n\nIntro.\n\n"
        "## One\nbody one\n\n"
        "## Two\nbody two\n"
    )
    header, sections = split_markdown_into_sections(content)
    assert "Intro." in header
    assert [t for t, _ in sections] == ["One", "Two"]
    # Bodies preserve verbatim spacing — section "One" was followed by a
    # blank line before the next ##, so its body keeps a trailing newline.
    assert sections[0][1] == "body one\n"
    assert sections[1][1] == "body two"


def test_parser_ignores_headers_inside_fenced_code_blocks():
    """The bug that motivated the rewrite: ## headers inside ``` blocks are not real."""
    content = (
        "# Title\n\n"
        "## Real\nbefore the fence\n\n"
        "```\n"
        "## Fake In Fence\n"
        "still inside the fence\n"
        "```\n\n"
        "## Also Real\nafter the fence\n"
    )
    header, sections = split_markdown_into_sections(content)
    titles = [t for t, _ in sections]
    assert titles == ["Real", "Also Real"], f"unexpected: {titles}"
    # The fenced block content must be carried in the body of the preceding section.
    assert "## Fake In Fence" in sections[0][1]
    assert "```" in sections[0][1]


def test_parser_handles_tilde_fences():
    content = (
        "## Real\n\n~~~\n## Not A Header\n~~~\n\n## After\nbody\n"
    )
    _, sections = split_markdown_into_sections(content)
    assert [t for t, _ in sections] == ["Real", "After"]


def test_parser_ignores_h3_and_h4():
    content = "## H2\n### H3\n#### H4\nbody\n"
    _, sections = split_markdown_into_sections(content)
    assert [t for t, _ in sections] == ["H2"]
    assert "### H3" in sections[0][1]


def test_slugify_drops_unicode_punctuation():
    assert slugify("Task ↔ Project Data Flow") == "task-project-data-flow"
    assert slugify("Storage & Vault") == "storage-vault"


# ─── TOML round-trip ────────────────────────────────────────────────────────

def test_toml_round_trip_with_backslashes_and_quotes(tmp_path):
    data = {
        "workspace_name": "emptyos",
        "original_rules_dir": ".claude/rules",  # forward slashes is the new normal
        "weird": 'has "quotes" and \\ backslash',
        "sections": {"a-b": "Hello", "c-d": "World"},
        "assembly_x": {"boot_file": "X.md", "sections": ["a-b", "c-d"]},
    }
    p = tmp_path / "m.toml"
    p.write_text(dump_toml(data), encoding="utf-8")
    loaded = load_toml(p)
    assert loaded["workspace_name"] == "emptyos"
    assert loaded["original_rules_dir"] == ".claude/rules"
    assert loaded["weird"] == 'has "quotes" and \\ backslash'
    assert loaded["sections"] == {"a-b": "Hello", "c-d": "World"}
    assert loaded["assembly_x"]["sections"] == ["a-b", "c-d"]


# ─── End-to-end import + ripple ─────────────────────────────────────────────

@pytest.fixture
def workspace(tmp_path):
    """A workspace with: a CLAUDE.md containing a fenced code block with
    fake ## headers; an AGENTS.md; one rule; one skill."""
    root = tmp_path / "ws"
    root.mkdir()

    (root / "CLAUDE.md").write_text(
        "# CLAUDE.md — Mock\n\nIntro.\n\n"
        "## Real Section\n"
        "before fence.\n\n"
        "```\n"
        "## Fake Header\n"
        "still in fence\n"
        "```\n\n"
        "## Second Section\nbody two\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "# AGENTS.md\n\nAgents header.\n\n## Agents One\nbody\n",
        encoding="utf-8",
    )

    rules = root / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "rule-one.md").write_text("# Rule One\n", encoding="utf-8")

    skills = root / ".claude" / "skills"
    (skills / "skill-one").mkdir(parents=True)
    (skills / "skill-one" / "SKILL.md").write_text("# Skill One\n", encoding="utf-8")

    return root


def test_import_then_immediate_ripple_is_byte_equal(workspace):
    """A clean import followed by ripple must produce identical boot files."""
    original_claude = (workspace / "CLAUDE.md").read_bytes()
    original_agents = (workspace / "AGENTS.md").read_bytes()

    run_import(workspace)
    run_transpile(workspace)

    assert (workspace / "CLAUDE.md").read_bytes() == original_claude
    assert (workspace / "AGENTS.md").read_bytes() == original_agents


def test_fenced_code_headers_not_promoted(workspace):
    run_import(workspace)
    manifest = load_toml(workspace / ".agent-bus" / "manifest.toml")
    section_keys = list(manifest["sections"].keys())
    # "Fake Header" was inside a fenced block; it must not appear.
    assert not any("fake-header" in k for k in section_keys), section_keys
    assert "claude-real-section" in section_keys
    assert "claude-second-section" in section_keys


def test_canonical_edit_propagates_to_native(workspace):
    run_import(workspace)
    section_file = workspace / ".agent-bus" / "sections" / "claude-real-section.md"
    section_file.write_text("Edited body.", encoding="utf-8")

    run_transpile(workspace)
    new_claude = (workspace / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Edited body." in new_claude
    assert "before fence." not in new_claude  # replaced


def test_native_divergence_refuses_to_clobber(workspace):
    run_import(workspace)
    # User edits the native rule directly.
    (workspace / ".claude" / "rules" / "rule-one.md").write_text(
        "# Rule One — edited locally", encoding="utf-8"
    )

    divergences = detect_native_divergence(
        load_toml(workspace / ".agent-bus" / "manifest.toml"),
        workspace,
        workspace / ".agent-bus",
    )
    assert len(divergences) == 1
    assert "rule-one.md" in divergences[0]

    with pytest.raises(RippleError) as exc:
        run_transpile(workspace)
    assert exc.value.code == 2

    # --force overrides and reverts.
    run_transpile(workspace, force=True)
    assert (workspace / ".claude" / "rules" / "rule-one.md").read_text(encoding="utf-8") == "# Rule One\n"


def test_obsolete_rule_removed_on_ripple(workspace):
    run_import(workspace)
    canonical_rule = workspace / ".agent-bus" / "rules" / "rule-one.md"
    canonical_rule.unlink()
    run_transpile(workspace)
    assert not (workspace / ".claude" / "rules" / "rule-one.md").exists()


def test_missing_section_file_hard_fails(workspace):
    run_import(workspace)
    # Delete a section file referenced by the assembly.
    (workspace / ".agent-bus" / "sections" / "claude-real-section.md").unlink()
    with pytest.raises(RippleError) as exc:
        run_transpile(workspace)
    assert exc.value.code == 3


# ─── BaseApp helpers (bus_context / bus_assemble) ───────────────────────────


def test_load_section_rule_skill_helpers(workspace):
    run_import(workspace)
    assert load_section(workspace, "claude-real-section") is not None
    assert load_section(workspace, "claude-nonexistent") is None
    assert load_rule(workspace, "rule-one") is not None
    assert load_rule(workspace, "rule-one.md") is not None  # suffix optional
    assert load_rule(workspace, "missing") is None
    assert load_skill(workspace, "skill-one") is not None
    assert load_skill(workspace, "missing") is None


def test_bus_context_via_baseapp(workspace, monkeypatch):
    """BaseApp.bus_context selectively composes named bus entries."""
    run_import(workspace)

    # Build a minimal stand-in BaseApp without booting the kernel — we only
    # need `repo_root` to point at the test workspace.
    from emptyos.sdk.base_app import BaseApp

    class _StubApp(BaseApp):
        def __init__(self, root):
            self._root = root

        @property
        def repo_root(self):
            return self._root

    app = _StubApp(workspace)

    ctx = app.bus_context(
        sections=["claude-real-section"],
        rules=["rule-one"],
        skills=["skill-one"],
    )
    assert "## section: claude-real-section" in ctx
    assert "## rule: rule-one" in ctx
    assert "## skill: skill-one" in ctx
    assert "before fence." in ctx
    assert "# Rule One" in ctx
    assert "# Skill One" in ctx

    # missing="skip" (default) drops silently.
    empty = app.bus_context(rules=["nonexistent-rule"])
    assert empty == ""

    # missing="error" raises.
    with pytest.raises(KeyError):
        app.bus_context(rules=["nonexistent-rule"], missing="error")


def test_bus_assemble_returns_full_boot_file(workspace):
    run_import(workspace)
    from emptyos.sdk.base_app import BaseApp

    class _StubApp(BaseApp):
        def __init__(self, root):
            self._root = root

        @property
        def repo_root(self):
            return self._root

    app = _StubApp(workspace)
    full = app.bus_assemble("CLAUDE.md")
    assert "# CLAUDE.md — Mock" in full
    assert "## Real Section" in full
    # Empty when bus isn't initialized.
    fresh = workspace.parent / "fresh"
    fresh.mkdir()
    assert _StubApp(fresh).bus_assemble("CLAUDE.md") == ""


def test_dry_run_on_uninitialized_workspace(tmp_path, capsys):
    # No .agent-bus/ → should NOT error; should report cleanly.
    rc = check_dry_run(tmp_path)
    out = capsys.readouterr().out
    assert "not initialized" in out.lower()
    assert rc == 0


def test_dry_run_detects_canonical_edit(workspace, capsys):
    run_import(workspace)
    run_transpile(workspace)
    capsys.readouterr()  # clear

    section_file = workspace / ".agent-bus" / "sections" / "claude-real-section.md"
    section_file.write_text("Different body.", encoding="utf-8")

    rc = check_dry_run(workspace)
    out = capsys.readouterr().out
    assert "[MODIFY] CLAUDE.md" in out
    assert rc == 1


def test_status_reports_after_import(workspace, capsys):
    run_import(workspace)
    run_status(workspace)
    out = capsys.readouterr().out
    assert "ws" in out  # workspace_name
    assert "CLAUDE.md" in out
    assert "AGENTS.md" in out


def test_paths_in_manifest_use_forward_slashes(workspace):
    run_import(workspace)
    raw = (workspace / ".agent-bus" / "manifest.toml").read_text(encoding="utf-8")
    # No \r-as-escape, no Windows separators.
    assert "\\" not in raw or all(
        line.startswith("#") for line in raw.splitlines() if "\\" in line
    )
    manifest = load_toml(workspace / ".agent-bus" / "manifest.toml")
    assert "/" in manifest["original_rules_dir"]
    assert "\\" not in manifest["original_rules_dir"]


# ─── Import atomicity ───────────────────────────────────────────────────────
#
# The store used to be wiped before it was refilled, so any failure part-way
# through the copy left it truncated. Observed 2026-07-25: one locked rule file
# and `.agent-bus/rules/` came out of the import holding 35 of 62 rules — with
# nothing in the output saying so. These pin the inverted order: build first,
# swap last, and a failure is a no-op.

def _store_snapshot(root: Path) -> dict[str, bytes]:
    bus = root / ".agent-bus"
    return {
        str(p.relative_to(bus)).replace("\\", "/"): p.read_bytes()
        for p in sorted(bus.rglob("*")) if p.is_file()
    }


def test_failed_import_leaves_the_store_byte_identical(workspace, monkeypatch):
    """A locked source file must cost the import, not the store."""
    run_import(workspace)
    before = _store_snapshot(workspace)
    assert any(k.startswith("rules/") for k in before), "fixture built no rules"

    # A second rule, so the copy loop has somewhere to fail part-way.
    (workspace / ".claude" / "rules" / "rule-two.md").write_text(
        "# Rule Two\n", encoding="utf-8"
    )

    real_copy2 = shutil.copy2

    def locked(src, dst, *a, **kw):
        if Path(src).name == "rule-two.md":
            raise PermissionError(f"[WinError 32] file in use: {src}")
        return real_copy2(src, dst, *a, **kw)

    monkeypatch.setattr(shutil, "copy2", locked)

    with pytest.raises(PermissionError):
        run_import(workspace)

    assert _store_snapshot(workspace) == before, (
        "a failed import modified the live store — this is the 35-of-62 bug"
    )


def test_failed_import_leaves_no_staging_directory(workspace, monkeypatch):
    """Staging is an implementation detail; a failure must not leave litter
    behind that a later import would have to reason about."""
    run_import(workspace)

    def boom(*a, **kw):
        raise PermissionError("locked")

    monkeypatch.setattr(shutil, "copy2", boom)
    with pytest.raises(PermissionError):
        run_import(workspace)

    leftovers = [p.name for p in (workspace / ".agent-bus").iterdir()
                 if p.name.startswith((".staging-", "rules.old-",
                                       "sections.old-", "skills.old-"))]
    assert leftovers == [], leftovers


def test_import_still_drops_deleted_rules(workspace):
    """The swap must not turn into a merge: a rule deleted from `.claude/`
    has to disappear from the store, which is what the original wipe bought."""
    run_import(workspace)
    assert (workspace / ".agent-bus" / "rules" / "rule-one.md").exists()

    (workspace / ".claude" / "rules" / "rule-one.md").unlink()
    (workspace / ".claude" / "rules" / "rule-three.md").write_text(
        "# Rule Three\n", encoding="utf-8"
    )
    run_import(workspace)

    names = {p.name for p in (workspace / ".agent-bus" / "rules").iterdir()}
    assert names == {"rule-three.md"}, names
