"""Unit tests for the tool-execution isolation policy (emptyos/sdk/tool_isolation.py).

Pure + offline — no daemon, no kernel. The async executor test drives the real
BashTool against a stub app via asyncio.run() so we don't need pytest-asyncio.

Covers:
  * decide() truth table — the dark-default + readonly + supervised + refuse paths
  * command_within_jail() — accept relative, reject absolute-escape + '..'
  * jailed_workspace() — path shape + session-id sanitisation
  * run_bash_jailed() — refuses an escaping command without running; runs an
    in-jail command and tags the result isolated
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from emptyos.sdk import tool_isolation as ti
from emptyos.sdk.agent_tools.bash import BashTool


# ── decide() truth table ────────────────────────────────────────────────────

def test_decide_flag_off_always_inline():
    # Dark default: with the flag off, even an unsupervised side-effecting tool
    # runs inline → byte-for-byte current behaviour.
    assert ti.decide(
        tool_name="Bash", is_readonly=False, supervised=False, flag_on=False
    ) == "inline"


def test_decide_readonly_always_inline():
    assert ti.decide(
        tool_name="Grep", is_readonly=True, supervised=False, flag_on=True
    ) == "inline"


def test_decide_supervised_inline():
    # Human reviews each action → no isolation needed even for side-effecting.
    assert ti.decide(
        tool_name="Bash", is_readonly=False, supervised=True, flag_on=True
    ) == "inline"


def test_decide_unsupervised_bash_isolate():
    assert ti.decide(
        tool_name="Bash", is_readonly=False, supervised=False, flag_on=True
    ) == "isolate"


def test_decide_unsupervised_write_refuse():
    # Side-effecting tool with no v1 executor → fail-safe refuse, not inline.
    for name in ("Write", "Edit", "CallApp", "DeleteFunction"):
        assert ti.decide(
            tool_name=name, is_readonly=False, supervised=False, flag_on=True
        ) == "refuse", name


def test_decide_unsupervised_readonly_inline():
    # Even unsupervised, a read-only tool is fine inline.
    assert ti.decide(
        tool_name="Read", is_readonly=True, supervised=False, flag_on=True
    ) == "inline"


# ── command_within_jail() ───────────────────────────────────────────────────

def test_jail_allows_relative(tmp_path):
    ok, _ = ti.command_within_jail("echo hi", jail_dir=tmp_path)
    assert ok
    ok, _ = ti.command_within_jail("ls subdir", jail_dir=tmp_path)
    assert ok


def test_jail_rejects_absolute_outside(tmp_path):
    ok, reason = ti.command_within_jail("cat /etc/passwd", jail_dir=tmp_path)
    assert not ok and "outside" in reason.lower()


def test_jail_allows_absolute_inside(tmp_path):
    inside = tmp_path / "note.txt"
    ok, _ = ti.command_within_jail(f"cat {inside}", jail_dir=tmp_path)
    assert ok


def test_jail_rejects_dotdot(tmp_path):
    ok, reason = ti.command_within_jail("cat ../secret.txt", jail_dir=tmp_path)
    assert not ok and ".." in reason


def test_jail_rejects_unparseable(tmp_path):
    ok, _ = ti.command_within_jail('echo "unterminated', jail_dir=tmp_path)
    assert not ok


def test_jail_rejects_empty(tmp_path):
    ok, _ = ti.command_within_jail("   ", jail_dir=tmp_path)
    assert not ok


# ── jailed_workspace() ──────────────────────────────────────────────────────

class _StubApp:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    @property
    def repo_root(self) -> Path:
        return Path(self.data_dir)

    async def emit(self, *a, **k):
        return None


def test_jailed_workspace_shape(tmp_path):
    app = _StubApp(tmp_path)
    jail = ti.jailed_workspace(app, "sess-123")
    assert jail == tmp_path / "tool-isolation" / "sess-123"


def test_jailed_workspace_sanitises_session_id(tmp_path):
    app = _StubApp(tmp_path)
    jail = ti.jailed_workspace(app, "../../evil")
    # No traversal survives into the path.
    assert ".." not in jail.parts
    assert jail.name == "evil"


def test_jailed_workspace_defaults_without_app():
    jail = ti.jailed_workspace(None, "x")
    assert jail == Path("data") / "tool-isolation" / "x"


# ── run_bash_jailed() ───────────────────────────────────────────────────────

def test_run_bash_jailed_refuses_escape(tmp_path):
    app = _StubApp(tmp_path)
    jail = tmp_path / "jail"
    res = asyncio.run(
        ti.run_bash_jailed(BashTool(), app, {"command": "cat /etc/passwd"}, jail_dir=jail)
    )
    assert res.ok is False
    assert res.display.get("refused") is True
    # Refused before running → jail dir was never created.
    assert not jail.exists()


def test_run_bash_jailed_runs_in_jail(tmp_path):
    app = _StubApp(tmp_path)
    jail = tmp_path / "jail"
    res = asyncio.run(
        ti.run_bash_jailed(BashTool(), app, {"command": "echo isolation-ok"}, jail_dir=jail)
    )
    assert res.ok is True
    assert res.display.get("isolated") is True
    assert "isolation-ok" in res.content
    assert jail.exists()
