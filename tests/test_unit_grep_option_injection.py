"""The grep search provider must never let a query be read as a flag.

The query is caller text — the KB body search sends what a user types — and
ripgrep has options that run programs (`--pre=<program>`). Before 2026-10-03
the query sat in argv with no `--` in front of it, so a query beginning with
`-` was an option. These tests use `--files` as the probe: harmless, and with
the bug it changes the result (every file listed) rather than running anything.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from emptyos.capabilities.providers import grep_search
from emptyos.capabilities.providers.grep_search import GrepSearchProvider


@pytest.mark.skipif(not shutil.which("rg"), reason="ripgrep not installed")
def test_a_flag_shaped_query_is_searched_as_text(tmp_path):
    (tmp_path / "plain.md").write_text("nothing to see\n", encoding="utf-8")
    (tmp_path / "flag.md").write_text("the text --files appears here\n", encoding="utf-8")
    prov = GrepSearchProvider(str(tmp_path))
    assert asyncio.run(prov.available())
    hits = asyncio.run(prov.execute(query="--files", path=str(tmp_path)))
    names = sorted(h["path"].replace("\\", "/").rsplit("/", 1)[-1] for h in hits)
    assert names == ["flag.md"], names


@pytest.mark.parametrize("cmd_name, mode", [("rg", "files_with_matches"), ("rg", "content"),
                                            ("grep", "files_with_matches")])
def test_the_query_always_follows_an_end_of_options_marker(monkeypatch, cmd_name, mode):
    seen = {}

    class _Proc:
        async def communicate(self):
            return b"", b""

    async def fake_exec(*argv, **kw):
        seen["argv"] = list(argv)
        return _Proc()

    monkeypatch.setattr(grep_search.asyncio, "create_subprocess_exec", fake_exec)
    prov = GrepSearchProvider("/vault")
    prov._cmd = cmd_name
    asyncio.run(prov.execute(query="--pre=calc", path="/vault", mode=mode, glob="*.md"))
    argv = seen["argv"]
    i = argv.index("--pre=calc")
    assert argv[i - 1] == "--", argv
