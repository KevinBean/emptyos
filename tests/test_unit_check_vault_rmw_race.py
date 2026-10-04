"""Pins the RMW scanner in BOTH directions.

An advisory check earns its keep only if it stays quiet on correct code and
still fires on the real defect. This scanner failed the first half twice: apps
that route every writer through one canonical key wrap it in a helper that
RETURNS a lock (``task._file_lock``, ``publish._post_lock``), and matching only
the literal platform names reported those as unlocked. Two audit runs spent
effort re-deriving that it was a false alarm.

The dangerous over-correction is suppressing a whole FILE once any helper lock
appears — that would hide the partial-lock hole (some writers locked, a sibling
not), which is the bug shape these runs actually keep finding. So the last test
is the load-bearing one.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCANNER = SCRIPTS / "check-vault-rmw-race.py"


def _load():
    # The scanner imports its sibling `check_base` by bare name.
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("check_vault_rmw_race", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def scanner():
    return _load()


def _scan(scanner, src: str) -> list[dict]:
    import ast

    # Parsed from source; the path is only used to render a repo-relative
    # location, so it must sit under REPO_ROOT but need not exist on disk.
    f = scanner.REPO_ROOT / "sample_for_test.py"
    tree = ast.parse(src)
    out: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            out += scanner._scan_func(node, f)
    return out


UNLOCKED = """
class A:
    async def save(self, path):
        content = await self.read(path)
        await self.write(path, content + "x")
"""

LITERAL_LOCK = """
class A:
    async def save(self, path):
        async with self.write_lock(f"k:{path}"):
            content = await self.read(path)
            await self.write(path, content + "x")
"""

HELPER_LOCK = """
class A:
    async def save(self, path):
        async with self._post_lock(path):
            content = await self.read(path)
            await self.write(path, content + "x")
"""

PARTIAL_LOCK = """
class A:
    async def save(self, path):
        async with self._post_lock(path):
            content = await self.read(path)
            await self.write(path, content + "x")

    async def sibling_rewrite(self, path):
        content = await self.read(path)
        await self.write(path, content.replace("a", "b"))
"""


def test_fires_on_unlocked_read_modify_write(scanner):
    assert len(_scan(scanner, UNLOCKED)) == 1


def test_silent_on_literal_platform_lock(scanner):
    assert _scan(scanner, LITERAL_LOCK) == []


def test_silent_on_lock_returning_helper(scanner):
    """The false alarm that hit task._file_lock and publish._post_lock."""
    assert _scan(scanner, HELPER_LOCK) == []


def test_still_fires_on_unlocked_sibling_of_a_locked_writer(scanner):
    """The partial-lock hole — a lock held by one writer is false safety.

    Helper-lock tolerance must stay function-scoped; if it were file-scoped
    this returns [] and the scanner goes blind to the exact class of bug the
    projects / people / publish findings all were.
    """
    found = _scan(scanner, PARTIAL_LOCK)
    assert len(found) == 1
    assert "sibling_rewrite" in found[0]["detail"]
