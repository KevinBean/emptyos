"""Both-direction pins for scripts/check_nested_tests.py.

Per .claude/skills/eos-graduate-audit: a graduated checker ships with tests
pinning BOTH "fires on the real regression" and "silent on healthy code".

The regression this guards is the 2026-07-31 defect in
`tests/test_unit_music_studio_frames.py`, where a truncated class body
re-parented 24 test methods into the body of the preceding function. They were
never collected, and the enclosing test still passed — so CI was green while
1,197 lines of assertions sat dead.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_nested_tests.py"
_spec = importlib.util.spec_from_file_location("check_nested_tests", SCRIPT)
cnt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cnt)


def _scan(tmp_path: Path, body: str):
    p = tmp_path / "test_sample.py"
    p.write_text(body, encoding="utf-8")
    return cnt.nested_tests(p)


class TestFiresOnRegression:
    def test_truncated_class_body_shape(self, tmp_path):
        """The exact shape that shipped: class methods swallowed by a
        module-level function that was inserted above them."""
        hits = _scan(tmp_path, (
            "def test_outer(visual):\n"
            "    assert visual is not None\n"
            "\n"
            "    def test_swallowed(self, visual, tmp_path):\n"
            "        assert False\n"
        ))
        assert [h["name"] for h in hits] == ["test_swallowed"]
        assert hits[0]["enclosing"] == "test_outer"

    def test_async_nested_test(self, tmp_path):
        hits = _scan(tmp_path, (
            "async def test_outer():\n"
            "    async def test_inner():\n"
            "        assert False\n"
        ))
        assert [h["name"] for h in hits] == ["test_inner"]

    def test_nested_inside_a_block_is_still_caught(self, tmp_path):
        """A test buried under `if`/`with` inside a test is just as dead."""
        hits = _scan(tmp_path, (
            "def test_outer():\n"
            "    if True:\n"
            "        def test_buried():\n"
            "            assert False\n"
        ))
        assert [h["name"] for h in hits] == ["test_buried"]

    def test_reports_every_orphan_not_just_the_first(self, tmp_path):
        hits = _scan(tmp_path, (
            "def test_outer():\n"
            "    def test_a(self):\n"
            "        pass\n"
            "\n"
            "    def test_b(self):\n"
            "        pass\n"
        ))
        assert [h["name"] for h in hits] == ["test_a", "test_b"]


class TestSilentOnHealthyCode:
    def test_helper_closures_are_not_flagged(self, tmp_path):
        """The common, correct idiom — monkeypatch fakes defined in a test.
        These are why the check keys on the `test_` prefix, not on nesting."""
        hits = _scan(tmp_path, (
            "def test_outer(monkeypatch):\n"
            "    def fake_probe(path):\n"
            "        return 1\n"
            "    async def fake_materialize(src, dest):\n"
            "        return True\n"
            "    assert fake_probe('x') == 1\n"
        ))
        assert hits == []

    def test_class_methods_are_collected_normally(self, tmp_path):
        hits = _scan(tmp_path, (
            "class TestThing:\n"
            "    def test_one(self):\n"
            "        pass\n"
            "\n"
            "    def test_two(self):\n"
            "        pass\n"
        ))
        assert hits == []

    def test_module_level_tests_are_fine(self, tmp_path):
        hits = _scan(tmp_path, (
            "def test_one():\n"
            "    pass\n"
            "\n"
            "def test_two():\n"
            "    pass\n"
        ))
        assert hits == []

    def test_nested_test_inside_a_plain_helper_is_not_a_test_file_bug(self, tmp_path):
        """A `test_*` inside a NON-test function is a factory/fixture builder,
        not a swallowed test — pytest was never going to collect it there and
        no coverage is silently lost."""
        hits = _scan(tmp_path, (
            "def make_suite():\n"
            "    def test_generated():\n"
            "        pass\n"
            "    return test_generated\n"
        ))
        assert hits == []


class TestRealTree:
    def test_the_repo_is_clean(self):
        """The tree stays clean; this is the gate itself."""
        repo = Path(__file__).resolve().parent.parent
        findings = [
            f for p in sorted((repo / "tests").rglob("test_*.py"))
            for f in cnt.nested_tests(p)
        ]
        assert findings == [], findings
