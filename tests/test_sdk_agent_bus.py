"""Tests for scripts/agent_bus.py — the Agent Context Bus ('Ripple').

These are pure unit/integration tests against the SDK module. They do NOT need
a running daemon — which is why the file must be named `test_sdk_*`.

That prefix is load-bearing, not descriptive: conftest's autouse
`_require_daemon_for_http_tests` skips any module whose name does not start
with `test_sdk_` / `test_unit_`. Under the old name `test_agent_bus.py` all 25
tests silently skipped whenever :9000 was down, so the pins here read as green
without ever executing — and a daemon-gated test cannot serve as a regression
pin (`.claude/rules/test-fix-verify-loop.md`). Avoiding `test_sys_*` was
necessary but not sufficient; the allowlist is positive.
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
    content_differs,
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


def test_import_leaves_build_artifacts_out_of_the_store(workspace):
    """The store is context an agent reads, so a skill's bytecode and tool
    caches must not be copied into it. Measured before the fix: three
    ``__pycache__`` dirs (pytest bytecode included) landed in ``.agent-bus/``.

    Only the divergence check was pinned for this; nothing covered the copy
    itself, so dropping ``ignore=`` from ``run_import`` stayed green.
    """
    skill = workspace / ".claude" / "skills" / "skill-one"
    (skill / "scripts").mkdir()
    (skill / "scripts" / "tool.py").write_text("print('hi')\n", encoding="utf-8")
    for d in ("__pycache__", ".pytest_cache"):
        (skill / d).mkdir()
        (skill / d / "junk.cpython-313.pyc").write_bytes(b"\x00compiled")
    (skill / "scripts" / "loose.pyc").write_bytes(b"\x00compiled")

    run_import(workspace)

    stored = workspace / ".agent-bus" / "skills" / "skill-one"
    got = sorted(p.relative_to(stored).as_posix() for p in stored.rglob("*"))
    # Authored files still arrive; every artifact shape is left behind.
    assert got == ["SKILL.md", "scripts", "scripts/tool.py"], got


# ─── Divergence detection ──────────────────────────────────────────────────

CRLF = "\r\n"
BODY = "# Demo\n\nline one\nline two\n"


def _mirror_workspace(tmp_path):
    """A workspace holding the same rule and skill in the store and natively."""
    (tmp_path / ".agent-bus" / "rules").mkdir(parents=True)
    (tmp_path / ".agent-bus" / "skills" / "demo").mkdir(parents=True)
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "demo").mkdir(parents=True)
    return {"original_rules_dir": ".claude/rules",
            "original_skills_dir": ".claude/skills"}


def _write(path: Path, text: str, crlf: bool) -> None:
    path.write_bytes((text.replace("\n", CRLF) if crlf else text).encode("utf-8"))


def test_line_endings_alone_are_not_a_divergence(tmp_path):
    """The bug this pins: comparing raw BYTES calls a CRLF/LF mismatch an edit.

    .gitattributes normalises some paths while core.autocrlf rewrites others, so
    a mirrored file can be byte-different and text-identical. Measured on
    eos-session-wrapup/docs-sync-commands.md — same normalised sha256, size delta
    exactly one byte per line — which blocked every ripple in the workspace while
    offering only two remedies, both of which act on a difference that is not
    there: re-import rewrites the store from native, and --force discards real
    native edits.
    """
    manifest = _mirror_workspace(tmp_path)
    _write(tmp_path / ".claude" / "rules" / "demo.md", BODY, crlf=True)
    _write(tmp_path / ".agent-bus" / "rules" / "demo.md", BODY, crlf=False)
    _write(tmp_path / ".claude" / "skills" / "demo" / "SKILL.md", BODY, crlf=True)
    _write(tmp_path / ".agent-bus" / "skills" / "demo" / "SKILL.md", BODY, crlf=False)

    assert detect_native_divergence(manifest, tmp_path, tmp_path / ".agent-bus") == []


def test_a_real_edit_is_still_a_divergence(tmp_path):
    """The other direction, in BOTH branches.

    Rules compared text and skills compared bytes — they had drifted into
    answering different questions, which is why only the skill tree produced the
    false positive. Both are asserted so they cannot drift apart again.
    """
    manifest = _mirror_workspace(tmp_path)
    _write(tmp_path / ".claude" / "rules" / "demo.md", "# Demo\n\nNATIVE ONLY\n", crlf=True)
    _write(tmp_path / ".agent-bus" / "rules" / "demo.md", "# Demo\n\ncanonical\n", crlf=False)
    _write(tmp_path / ".claude" / "skills" / "demo" / "SKILL.md",
           "# Demo\n\nNATIVE ONLY\n", crlf=True)
    _write(tmp_path / ".agent-bus" / "skills" / "demo" / "SKILL.md",
           "# Demo\n\ncanonical\n", crlf=False)

    out = detect_native_divergence(manifest, tmp_path, tmp_path / ".agent-bus")
    assert len(out) == 2, out
    assert any("rules/demo.md" in d for d in out), out
    assert any("skills/demo/SKILL.md" in d for d in out), out


def test_a_non_utf8_file_falls_back_to_bytes(tmp_path):
    """A skill may ship a png or a zip fixture, where a newline is not a
    newline and normalising the bytes would be wrong."""
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    png = b"\x89PNG\r\n\x1a\n\xff\xfe"
    a.write_bytes(png)
    b.write_bytes(png)
    assert content_differs(a, b) is False
    b.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfd")
    assert content_differs(a, b) is True

    # A CRLF inside binary is data, not a line ending. Differing only there must
    # still be reported, or the fallback is normalising bytes rather than
    # comparing them — which a trailing-byte difference cannot detect.
    a.write_bytes(b"\x89PNG\r\n\xff\xfe")
    b.write_bytes(b"\x89PNG\n\xff\xfe")
    assert content_differs(a, b) is True


def test_a_stale_pyc_is_not_a_native_edit(tmp_path):
    """A .pyc is rewritten by whichever interpreter last imported the skill, so
    the two sides differ for a reason no human caused. Measured on
    eos-ai-conversation-ingest, which carries four of them and blocked the
    ripple with the same two wrong remedies as the CRLF case above.
    """
    manifest = _mirror_workspace(tmp_path)
    for side in (".claude", ".agent-bus"):
        cache = tmp_path / side / "skills" / "demo" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "mod.cpython-313.pyc").write_bytes(b"\x00compiled-by-" + side.encode())
    _write(tmp_path / ".claude" / "skills" / "demo" / "SKILL.md", BODY, crlf=False)
    _write(tmp_path / ".agent-bus" / "skills" / "demo" / "SKILL.md", BODY, crlf=False)

    assert detect_native_divergence(manifest, tmp_path, tmp_path / ".agent-bus") == []

    # The other direction: an authored file beside the .pyc is still reported.
    _write(tmp_path / ".claude" / "skills" / "demo" / "SKILL.md", "# Demo\n\nedited\n", crlf=False)
    out = detect_native_divergence(manifest, tmp_path, tmp_path / ".agent-bus")
    assert len(out) == 1 and "SKILL.md" in out[0], out


def test_the_artifact_skip_is_narrower_than_non_markdown(tmp_path):
    """Separates the two clauses of the skip, which a .pyc-under-__pycache__
    fixture cannot: that file matches BOTH, so it passes even a skip as broad as
    ``suffix != ".md"``. Canonical skills hold 33 authored non-markdown files
    (.py/.yaml/.toml/.ps1/.json), and the skip fails OPEN — an unreported
    divergence lets ripple overwrite a real native edit.

    So: a cache dir with a non-.pyc suffix must be skipped (dir clause alone),
    and an authored .py must be reported (neither clause).
    """
    manifest = _mirror_workspace(tmp_path)
    for side, body in ((".claude", "native"), (".agent-bus", "canonical")):
        skill = tmp_path / side / "skills" / "demo"
        _write(skill / "SKILL.md", BODY, crlf=False)
        (skill / ".pytest_cache").mkdir(parents=True)
        (skill / ".pytest_cache" / "nodeids.json").write_text(f'["{body}"]', encoding="utf-8")
        (skill / "scripts").mkdir()
        (skill / "scripts" / "run.py").write_text(f"X = {body!r}\n", encoding="utf-8")
        # Authored, and named to defeat a substring matcher: "cache_warmer.py"
        # contains "cache", so a skip written as `"cache" in str(rel)` swallows
        # it. Match on path COMPONENTS and the exact suffix, never a substring.
        (skill / "scripts" / "cache_warmer.py").write_text(f"Y = {body!r}\n", encoding="utf-8")

    out = detect_native_divergence(manifest, tmp_path, tmp_path / ".agent-bus")
    assert len(out) == 2, out
    assert any("run.py" in d for d in out), out
    assert any("cache_warmer.py" in d for d in out), out
    assert not any("nodeids.json" in d for d in out), out


def test_an_absolute_ancestor_named_pycache_does_not_disable_the_check(tmp_path):
    """The skip takes the skill-RELATIVE path. Against the absolute path, a
    checkout under any directory named ``__pycache__`` matches on that ancestor
    and silently skips every file in the tree — failing open, so ripple then
    overwrites native edits with no divergence reported.
    """
    root = tmp_path / "__pycache__" / "checkout"
    root.mkdir(parents=True)
    manifest = _mirror_workspace(root)
    _write(root / ".claude" / "skills" / "demo" / "SKILL.md", "# Demo\n\nedited\n", crlf=False)
    _write(root / ".agent-bus" / "skills" / "demo" / "SKILL.md", BODY, crlf=False)

    out = detect_native_divergence(manifest, root, root / ".agent-bus")
    assert len(out) == 1 and "SKILL.md" in out[0], out


def test_a_directory_where_a_file_is_expected_does_not_crash(tmp_path):
    """``.exists()`` is True for a directory, and reading one raises OSError —
    not the ValueError content_differs catches — so the ripple died with a raw
    traceback instead of an actionable RippleError. Both branches guard with
    ``.is_file()``.
    """
    manifest = _mirror_workspace(tmp_path)
    (tmp_path / ".agent-bus" / "rules" / "demo.md").mkdir()
    _write(tmp_path / ".claude" / "rules" / "demo.md", BODY, crlf=False)
    _write(tmp_path / ".agent-bus" / "skills" / "demo" / "SKILL.md", BODY, crlf=False)
    (tmp_path / ".claude" / "skills" / "demo" / "SKILL.md").mkdir()

    assert detect_native_divergence(manifest, tmp_path, tmp_path / ".agent-bus") == []
