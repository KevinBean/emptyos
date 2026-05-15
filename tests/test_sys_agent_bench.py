"""Tests for apps/model-bench/agent_bench.py + agent_scenarios.py.

Pure-python — no running daemon needed. The model-bench package directory
uses a hyphen which Python can't import by normal name, so we load the
two modules via importlib.util and wire them into sys.modules under a
safe alias (mirrors what the kernel's app_loader does at runtime).
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import textwrap
import types
from dataclasses import asdict
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_MB_DIR = _REPO_ROOT / "apps" / "model-bench"
_PKG_ALIAS = "apps_model_bench_pkg_test"


def _load_modules():
    """Load agent_bench and agent_scenarios once into sys.modules (test-scope)."""
    if _PKG_ALIAS in sys.modules:
        return (
            sys.modules[f"{_PKG_ALIAS}.agent_bench"],
            sys.modules[f"{_PKG_ALIAS}.agent_scenarios"],
        )
    pkg = types.ModuleType(_PKG_ALIAS)
    pkg.__path__ = [str(_MB_DIR)]
    sys.modules[_PKG_ALIAS] = pkg
    out = []
    for name in ("agent_bench", "agent_scenarios"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG_ALIAS}.{name}", _MB_DIR / f"{name}.py",
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG_ALIAS}.{name}"] = mod
        spec.loader.exec_module(mod)
        out.append(mod)
    return out[0], out[1]


agent_bench, agent_scenarios = _load_modules()


# ── Scratch management ────────────────────────────────────────────────

class TestScratchManagement:
    def test_scratch_root_under_data_dir(self, tmp_path):
        root = agent_bench.scratch_root(tmp_path)
        assert root == tmp_path / "agent_scratch"

    def test_make_run_id_is_unique_and_safe(self):
        a = agent_bench.make_run_id("foo", "eos+ollama")
        b = agent_bench.make_run_id("foo", "eos+ollama")
        assert a != b
        assert "+" not in a  # subject prefix sanitized for filesystems
        assert a.startswith("foo__eos_ollama__")

    def test_prepare_scratch_calls_setup(self, tmp_path):
        def setup(d: Path):
            (d / "marker.txt").write_text("hello", encoding="utf-8")
        run_id = agent_bench.make_run_id("s1", "eos+ollama")
        scratch = agent_bench.prepare_scratch(tmp_path, run_id, setup)
        assert scratch.exists() and scratch.is_dir()
        assert (scratch / "marker.txt").read_text(encoding="utf-8") == "hello"
        assert scratch.parent == tmp_path / "agent_scratch"

    def test_prune_keeps_only_n_most_recent(self, tmp_path):
        root = agent_bench.scratch_root(tmp_path)
        root.mkdir(parents=True)
        # Create 10 dirs with staggered mtimes
        import os, time
        for i in range(10):
            d = root / f"run_{i:02d}"
            d.mkdir()
            ts = time.time() - (10 - i) * 60  # older for lower i
            os.utime(d, (ts, ts))
        agent_bench.prune_old_scratches(tmp_path, keep=3)
        remaining = sorted(d.name for d in root.iterdir() if d.is_dir())
        assert remaining == ["run_07", "run_08", "run_09"]


# ── Verifier behavior ─────────────────────────────────────────────────

class TestVerifiers:
    def test_write_new_util_fails_on_empty(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["write-new-util"]
        s.setup(tmp_path)
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "strings.py" in r.notes

    def test_write_new_util_passes_on_correct_output(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["write-new-util"]
        s.setup(tmp_path)
        (tmp_path / "strings.py").write_text(textwrap.dedent("""
            import re
            def slugify(s: str) -> str:
                s = s.strip().lower()
                s = re.sub(r'[^a-z0-9]+', '-', s)
                return s.strip('-')
        """).strip(), encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_write_new_util_fails_on_wrong_output(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["write-new-util"]
        s.setup(tmp_path)
        (tmp_path / "strings.py").write_text(
            "def slugify(s): return s",  # identity — won't match cases
            encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "slugify" in r.notes

    def test_add_temperature_setup_includes_missing_call(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["add-temperature"]
        s.setup(tmp_path)
        # Source fixture should have at least one missing-temperature call
        src = (tmp_path / "focus_app.py").read_text(encoding="utf-8")
        missing = agent_scenarios._calls_missing_temperature(src)
        assert len(missing) >= 1

    def test_add_temperature_passes_when_fixture_is_fully_patched(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["add-temperature"]
        s.setup(tmp_path)
        src = (tmp_path / "focus_app.py").read_text(encoding="utf-8")
        # Patch every `self.think(` call to include `temperature=0.5`
        patched = src.replace(
            "return await self.think(",
            "return await self.think(temperature=0.5, ",
        )
        (tmp_path / "focus_app.py").write_text(patched, encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_find_missing_tests_passes_on_exact_match(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["find-missing-tests"]
        s.setup(tmp_path)
        (tmp_path / "gaps.txt").write_text("dates\nlists\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_find_missing_tests_fails_on_wrong_gaps(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["find-missing-tests"]
        s.setup(tmp_path)
        (tmp_path / "gaps.txt").write_text("dates\nstrings\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False

    def test_explain_structure_fails_if_fixture_mutated(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["explain-structure"]
        s.setup(tmp_path)
        # Agent deleted one of the fixture files → verifier must catch
        (tmp_path / "fake_app" / "app.py").unlink()
        r = s.verify(tmp_path)
        assert r.ok is False

    # ── call-app-discovery ────────────────────────────────────────

    def test_call_app_discovery_fails_without_summary(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["call-app-discovery"]
        s.setup(tmp_path)
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "apps_summary.txt" in r.notes

    def test_call_app_discovery_passes_on_real_looking_counts(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["call-app-discovery"]
        s.setup(tmp_path)
        (tmp_path / "apps_summary.txt").write_text(
            "task has 12 methods\njournal has 9 methods\ncapture has 7 methods\n",
            encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_call_app_discovery_rejects_suspiciously_low_counts(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["call-app-discovery"]
        s.setup(tmp_path)
        # Hallucinated counts — below the reasonable floor
        (tmp_path / "apps_summary.txt").write_text(
            "task has 1 methods\njournal has 1 methods\ncapture has 1 methods\n",
            encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "implausibly" in r.notes

    def test_call_app_discovery_rejects_wrong_format(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["call-app-discovery"]
        s.setup(tmp_path)
        (tmp_path / "apps_summary.txt").write_text(
            "task: 12\njournal: 9\ncapture: 7\n",
            encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is False

    def test_call_app_discovery_rejects_wrong_apps(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["call-app-discovery"]
        s.setup(tmp_path)
        (tmp_path / "apps_summary.txt").write_text(
            "task has 10 methods\njournal has 10 methods\nprojects has 10 methods\n",
            encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is False

    # ── grep-replace ──────────────────────────────────────────────

    def test_grep_replace_fixture_has_expected_matches(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["grep-replace"]
        s.setup(tmp_path)
        total = sum(
            p.read_text(encoding="utf-8").count('print("hello")')
            for p in (tmp_path / "pkg").rglob("*.py")
        )
        assert total == 4   # 1 in a.py + 2 in b.py + 1 in c.py

    def test_grep_replace_passes_after_full_swap(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["grep-replace"]
        s.setup(tmp_path)
        for p in (tmp_path / "pkg").rglob("*.py"):
            if p.name != "nope.py":
                p.write_text(
                    p.read_text(encoding="utf-8").replace('print("hello")', 'print("world")'),
                    encoding="utf-8",
                )
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_grep_replace_fails_when_decoy_touched(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["grep-replace"]
        s.setup(tmp_path)
        # Do the swap correctly, but also break the decoy
        for p in (tmp_path / "pkg").rglob("*.py"):
            p.write_text(
                p.read_text(encoding="utf-8")
                 .replace('print("hello")', 'print("world")')
                 .replace('print("goodbye")', 'print("something else")'),
                encoding="utf-8",
            )
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "decoy" in r.notes.lower() or "nope" in r.notes.lower()

    def test_grep_replace_fails_when_partial(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["grep-replace"]
        s.setup(tmp_path)
        # Only swap in one file
        p = tmp_path / "pkg" / "a.py"
        p.write_text(p.read_text(encoding="utf-8").replace('print("hello")', 'print("world")'), encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False

    # ── multi-file-refactor ───────────────────────────────────────

    def test_refactor_fixture_has_6_references(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["multi-file-refactor"]
        s.setup(tmp_path)
        import re as _re
        total = sum(
            len(_re.findall(r"\bold_func\b", p.read_text(encoding="utf-8")))
            for p in (tmp_path / "lib").rglob("*.py")
        )
        # 1 def + 2 imports + 1 call in use_a + 2 calls in use_b = 6
        assert total == 6

    def test_refactor_passes_after_word_boundary_rename(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["multi-file-refactor"]
        s.setup(tmp_path)
        import re as _re
        for p in (tmp_path / "lib").rglob("*.py"):
            if p.name != "decoy.py":
                p.write_text(_re.sub(r"\bold_func\b", "new_func", p.read_text(encoding="utf-8")), encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_refactor_fails_when_decoy_corrupted(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["multi-file-refactor"]
        s.setup(tmp_path)
        import re as _re
        # Proper rename on real files
        for p in (tmp_path / "lib").rglob("*.py"):
            if p.name != "decoy.py":
                p.write_text(_re.sub(r"\bold_func\b", "new_func", p.read_text(encoding="utf-8")), encoding="utf-8")
        # But also accidentally rename old_function in the decoy
        decoy = tmp_path / "lib" / "decoy.py"
        decoy.write_text(decoy.read_text(encoding="utf-8").replace("old_function", "new_function"), encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "decoy" in r.notes.lower()

    def test_refactor_fails_on_stray_old_func(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["multi-file-refactor"]
        s.setup(tmp_path)
        # Only rename in core.py, miss the callers
        core = tmp_path / "lib" / "core.py"
        core.write_text(core.read_text(encoding="utf-8").replace("old_func", "new_func"), encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False

    # ── debug-and-fix ──────────────────────────────────────────────

    def test_debug_fixture_has_bug(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["debug-and-fix"]
        s.setup(tmp_path)
        r = s.verify(tmp_path)
        # Setup state should fail tests out of the box
        assert r.ok is False
        assert "exited 1" in r.notes or "FAIL" in r.notes

    def test_debug_verifier_passes_after_correct_fix(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["debug-and-fix"]
        s.setup(tmp_path)
        p = tmp_path / "broken.py"
        p.write_text(p.read_text(encoding="utf-8").replace("return x + y", "return x * y"), encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_debug_verifier_rejects_missing_files(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["debug-and-fix"]
        # Don't run setup — verifier must fail cleanly
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "missing" in r.notes.lower()

    def test_debug_verifier_rejects_modified_runner(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["debug-and-fix"]
        s.setup(tmp_path)
        # Fix the bug AND corrupt the runner — verifier must catch the runner mutation
        p = tmp_path / "broken.py"
        p.write_text(p.read_text(encoding="utf-8").replace("return x + y", "return x * y"), encoding="utf-8")
        runner = tmp_path / "run_tests.py"
        runner.write_text("print('hacked'); import sys; sys.exit(0)", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "run_tests.py" in r.notes

    def test_debug_verifier_rejects_wrong_fix(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["debug-and-fix"]
        s.setup(tmp_path)
        # "Fix" by breaking factorial — multiply still fails
        p = tmp_path / "broken.py"
        p.write_text(p.read_text(encoding="utf-8").replace("factorial(n - 1)", "factorial(n)"), encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False

    # ── long-context-needle ────────────────────────────────────────

    def test_long_context_fixture_has_exactly_one_needle(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["long-context-needle"]
        s.setup(tmp_path)
        pkg = tmp_path / "pkg"
        files = list(pkg.rglob("*.py"))
        assert len(files) == 40
        hits = [p for p in files if "NEEDLE(bench):" in p.read_text(encoding="utf-8")]
        assert len(hits) == 1
        assert hits[0].name == "module_27.py"

    def test_long_context_verifier_passes_on_correct_answer(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["long-context-needle"]
        s.setup(tmp_path)
        (tmp_path / "found.txt").write_text("module_27.py: tune the batch size\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_long_context_verifier_accepts_pathed_filename(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["long-context-needle"]
        s.setup(tmp_path)
        # Agent might pass the absolute path; verifier should still accept
        (tmp_path / "found.txt").write_text("pkg/module_27.py: tune the batch size\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_long_context_verifier_rejects_wrong_file(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["long-context-needle"]
        s.setup(tmp_path)
        (tmp_path / "found.txt").write_text("module_10.py: tune the batch size\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "wrong file" in r.notes

    def test_long_context_verifier_rejects_missing_text(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["long-context-needle"]
        s.setup(tmp_path)
        # Right file, missing needle content
        (tmp_path / "found.txt").write_text("module_27.py: something else entirely\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False

    def test_long_context_verifier_rejects_bad_format(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["long-context-needle"]
        s.setup(tmp_path)
        (tmp_path / "found.txt").write_text("just a plain sentence\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False

    # ── false-premise ──────────────────────────────────────────────

    def test_false_premise_passes_when_file_untouched(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["false-premise"]
        s.setup(tmp_path)
        r = s.verify(tmp_path)
        # Setup is already correct; no edit needed = pass
        assert r.ok is True

    def test_false_premise_passes_with_equivalent_refactor(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["false-premise"]
        s.setup(tmp_path)
        # Agent may refactor multiply as long as it still works
        p = tmp_path / "lib.py"
        p.write_text(
            "def multiply(x, y):\n    return y * x\n\ndef divide(x, y):\n    return x / y\n",
            encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_false_premise_fails_when_multiply_broken_by_fix(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["false-premise"]
        s.setup(tmp_path)
        p = tmp_path / "lib.py"
        p.write_text(p.read_text(encoding="utf-8").replace("return x * y", "return x + y"), encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False

    def test_false_premise_fails_when_multiply_deleted(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["false-premise"]
        s.setup(tmp_path)
        (tmp_path / "lib.py").write_text("def divide(x, y):\n    return x / y\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False

    def test_false_premise_fails_when_divide_removed(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["false-premise"]
        s.setup(tmp_path)
        (tmp_path / "lib.py").write_text(
            "def multiply(x, y):\n    return x * y\n", encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "divide" in r.notes

    # ── error-recovery ──────────────────────────────────────────────

    def test_error_recovery_setup_fails_out_of_box(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["error-recovery"]
        s.setup(tmp_path)
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "maht" in r.notes or "fails" in r.notes.lower()

    def test_error_recovery_passes_after_typo_fix(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["error-recovery"]
        s.setup(tmp_path)
        p = tmp_path / "broken_mod.py"
        p.write_text(p.read_text(encoding="utf-8").replace("from maht", "from math"), encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_error_recovery_fails_if_output_is_wrong(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["error-recovery"]
        s.setup(tmp_path)
        # Replace the whole file — runs clean but prints the wrong thing
        (tmp_path / "broken_mod.py").write_text(
            "if __name__ == '__main__':\n    print(42)\n", encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is False

    # ── progressive-dependency ─────────────────────────────────────

    def test_progressive_fails_on_empty(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["progressive-dependency"]
        s.setup(tmp_path)
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "calc.py" in r.notes

    def test_progressive_fails_when_only_first_file(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["progressive-dependency"]
        s.setup(tmp_path)
        (tmp_path / "calc.py").write_text("def add(x, y):\n    return x + y\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "use_calc.py" in r.notes

    def test_progressive_passes_with_both(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["progressive-dependency"]
        s.setup(tmp_path)
        (tmp_path / "calc.py").write_text("def add(x, y):\n    return x + y\n", encoding="utf-8")
        (tmp_path / "use_calc.py").write_text(
            "from calc import add\nprint(add(2, 3))\n", encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_progressive_fails_on_import_error(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["progressive-dependency"]
        s.setup(tmp_path)
        # calc.py exists but named wrong function — use_calc's import fails
        (tmp_path / "calc.py").write_text("def plus(x, y):\n    return x + y\n", encoding="utf-8")
        (tmp_path / "use_calc.py").write_text(
            "from calc import add\nprint(add(2, 3))\n", encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is False

    def test_progressive_fails_on_wrong_output(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["progressive-dependency"]
        s.setup(tmp_path)
        # Works, but prints a wrong value
        (tmp_path / "calc.py").write_text("def add(x, y):\n    return x + y + 1\n", encoding="utf-8")
        (tmp_path / "use_calc.py").write_text(
            "from calc import add\nprint(add(2, 3))\n", encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is False

    # ── ambiguity-clarify (v2: task names non-existent function) ──

    def _inject_print_in_existing(self, scratch, fn_name):
        """Helper — simulate agent silently guessing by adding the log to one
        of the existing functions instead of reporting the mismatch."""
        p = scratch / "app.py"
        text = p.read_text(encoding="utf-8")
        text = text.replace(
            f"def {fn_name}():\n    return",
            f"def {fn_name}():\n    print(\"entered\")\n    return",
            1,
        )
        p.write_text(text, encoding="utf-8")

    def test_ambiguity_passes_when_agent_reports_without_editing(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["ambiguity-clarify"]
        s.setup(tmp_path)
        # No edits — the correct response is to report the non-existent function
        r = s.verify(tmp_path)
        assert r.ok is True
        assert "reported" in r.notes or "mismatch" in r.notes

    def test_ambiguity_passes_when_agent_creates_stub(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["ambiguity-clarify"]
        s.setup(tmp_path)
        # Agent proactively creates _validate_input as a new function with the log
        p = tmp_path / "app.py"
        p.write_text(
            p.read_text(encoding="utf-8") +
            '\n\ndef _validate_input():\n    print("entered")\n    pass\n',
            encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is True
        assert "_validate_input" in r.notes

    def test_ambiguity_passes_stub_without_log(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["ambiguity-clarify"]
        s.setup(tmp_path)
        # Agent created the stub but forgot the log — partial, but didn't hallucinate
        p = tmp_path / "app.py"
        p.write_text(
            p.read_text(encoding="utf-8") + '\n\ndef _validate_input():\n    pass\n',
            encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is True
        assert "_validate_input" in r.notes

    def test_ambiguity_fails_when_agent_guesses_at_existing_function(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["ambiguity-clarify"]
        s.setup(tmp_path)
        # Agent silently guessed — added the log to `two()` because "close enough"
        self._inject_print_in_existing(tmp_path, "two")
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "GUESSED" in r.notes
        assert "two" in r.notes

    def test_ambiguity_fails_on_first_function_guess(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["ambiguity-clarify"]
        s.setup(tmp_path)
        # Common failure mode: pick the first function
        self._inject_print_in_existing(tmp_path, "one")
        r = s.verify(tmp_path)
        assert r.ok is False

    def test_ambiguity_fails_if_original_function_removed(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["ambiguity-clarify"]
        s.setup(tmp_path)
        (tmp_path / "app.py").write_text(
            "def one():\n    return 1\n\ndef two():\n    return 2\n", encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "removed" in r.notes

    def test_ambiguity_fails_on_syntax_error(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["ambiguity-clarify"]
        s.setup(tmp_path)
        (tmp_path / "app.py").write_text("def one()  # broken\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False

    # ── anti-goal ──────────────────────────────────────────────────

    def test_anti_goal_passes_on_correct_carveout(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["anti-goal"]
        s.setup(tmp_path)
        # Do the swap correctly, preserving hello()'s prints
        p = tmp_path / "code.py"
        text = p.read_text(encoding="utf-8")
        # Replace prints outside hello()
        text = (text
                .replace('def farewell():\n    print(', 'def farewell():\n    log.info(')
                .replace('def welcome(name):\n    print(', 'def welcome(name):\n    log.info(')
                .replace('    print("settled in")', '    log.info("settled in")')
                .replace('def diagnostic():\n    print(', 'def diagnostic():\n    log.info('))
        p.write_text(text, encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is True

    def test_anti_goal_fails_if_hello_was_modified(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["anti-goal"]
        s.setup(tmp_path)
        # Uniform swap — breaks hello()'s protected prints
        p = tmp_path / "code.py"
        p.write_text(p.read_text(encoding="utf-8").replace("print(", "log.info("), encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False
        assert "hello" in r.notes

    def test_anti_goal_fails_if_nothing_done(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["anti-goal"]
        s.setup(tmp_path)
        r = s.verify(tmp_path)
        assert r.ok is False

    def test_anti_goal_fails_on_syntax_break(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["anti-goal"]
        s.setup(tmp_path)
        (tmp_path / "code.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
        r = s.verify(tmp_path)
        assert r.ok is False

    def test_anti_goal_fails_if_half_swapped(self, tmp_path):
        scenarios = {s.id: s for s in agent_scenarios.build_scenarios()}
        s = scenarios["anti-goal"]
        s.setup(tmp_path)
        # Only farewell gets swapped, others still have print()
        p = tmp_path / "code.py"
        p.write_text(
            p.read_text(encoding="utf-8")
             .replace('def farewell():\n    print(', 'def farewell():\n    log.info('),
            encoding="utf-8",
        )
        r = s.verify(tmp_path)
        assert r.ok is False


# ── Overall-ok logic (tool-floor enforcement) ─────────────────────────

class TestOverallOk:
    def test_verify_pass_tool_floor_met(self):
        sc = agent_scenarios.build_scenarios()[0]  # write-new-util, floor=1
        assert agent_bench._overall_ok(
            agent_bench.VerifyResult(ok=True, notes=""),
            None, tool_calls=1, scenario=sc,
        ) is True

    def test_zero_tool_calls_fails_scenario_with_floor(self):
        sc = agent_scenarios.build_scenarios()[0]  # floor=1
        assert agent_bench._overall_ok(
            agent_bench.VerifyResult(ok=True, notes=""),
            None, tool_calls=0, scenario=sc,
        ) is False

    def test_runner_error_fails_even_if_verify_ok(self):
        sc = agent_scenarios.build_scenarios()[0]
        assert agent_bench._overall_ok(
            agent_bench.VerifyResult(ok=True, notes=""),
            "boom", tool_calls=5, scenario=sc,
        ) is False

    def test_verify_fail_fails(self):
        sc = agent_scenarios.build_scenarios()[0]
        assert agent_bench._overall_ok(
            agent_bench.VerifyResult(ok=False, notes=""),
            None, tool_calls=3, scenario=sc,
        ) is False


# ── Results persistence ───────────────────────────────────────────────

class TestResultsRoundTrip:
    def test_save_and_load_appends(self, tmp_path):
        r1 = agent_bench.AgentRunResult(
            run_id="r1", scenario_id="s", subject_id="eos+ollama",
            ok=True, tool_calls=2, tool_errors=0, iterations=2,
            wall_ms=1234,
        )
        agent_bench.save_results(tmp_path, [r1])
        loaded = agent_bench.load_results(tmp_path)
        assert len(loaded) == 1 and loaded[0]["run_id"] == "r1"
        agent_bench.save_results(tmp_path, [r1])  # second save appends
        assert len(agent_bench.load_results(tmp_path)) == 2

    def test_save_transcript_writes_jsonl(self, tmp_path):
        events = [{"type": "agent:tool_call", "name": "Read"}, {"type": "agent:done"}]
        path = agent_bench.save_transcript(tmp_path, "run1", events)
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ── Subject dispatch (stubbed) ────────────────────────────────────────

class TestDispatch:
    """Error-shape tests that don't drive the real event loop.

    These originally called `run_scenario` via @pytest.mark.asyncio but
    hit pytest-asyncio session-runner state leaking from other async
    test files. Since we only care about the error surface shape, we
    test `_failed_result` directly and inspect the dispatch branches
    synchronously."""

    def test_failed_result_has_expected_shape(self, tmp_path):
        s = agent_scenarios.build_scenarios()[0]
        r = agent_bench._failed_result(
            "run-1", s, "ghost-subject", tmp_path, error="unknown subject_id 'ghost-subject'",
        )
        assert r.ok is False
        assert r.subject_id == "ghost-subject"
        assert r.scenario_id == s.id
        assert "unknown subject_id" in (r.error or "")
        assert r.tool_calls == 0
        assert r.wall_ms == 0
        assert r.scratch_path == str(tmp_path)

    def test_claude_external_missing_binary_surfaces_error(self, tmp_path, monkeypatch):
        import shutil as _s
        monkeypatch.setattr(_s, "which", lambda name: None)
        # Exercise the early-exit branch in run_claude_external_subject
        # — it's an async function but the early-return-on-missing-binary
        # path doesn't await anything, so we can pump one step of the
        # coroutine and get the result synchronously.
        s = agent_scenarios.build_scenarios()[0]
        s.setup(tmp_path)
        coro = agent_bench.run_claude_external_subject(
            scenario=s, scratch=tmp_path, run_id="r1", data_dir=tmp_path,
        )
        try:
            coro.send(None)
        except StopIteration as e:
            result = e.value
        else:  # pragma: no cover — defensive
            coro.close()
            pytest.fail("early-exit branch did not complete synchronously")
        assert result.ok is False
        assert "claude CLI" in (result.error or "")
        assert result.subject_id == "claude-external"


class _StubApp:
    """Minimal app stand-in — run_scenario only needs app for eos+* subjects."""

    class _Kernel:
        class _Apps:
            instances = {}
        apps = _Apps()

    kernel = _Kernel()
    data_dir = Path(tempfile.mkdtemp())


# ── Shape invariants ──────────────────────────────────────────────────

class TestShape:
    def test_all_subjects_handled_or_documented(self):
        # Every listed subject must be a documented external baseline (claude-
        # external / claude-code-eos) OR start with eos+ (EmptyOS-native agent).
        known_externals = {"claude-external", "claude-code-eos"}
        for sid in agent_bench.ALL_SUBJECTS:
            assert sid in known_externals or sid.startswith("eos+")

    def test_agent_run_result_is_serializable(self):
        r = agent_bench.AgentRunResult(
            run_id="r", scenario_id="s", subject_id="eos+ollama",
            ok=True, tool_calls=1, tool_errors=0, iterations=1, wall_ms=10,
        )
        d = asdict(r)
        import json
        assert json.loads(json.dumps(d, default=str))["run_id"] == "r"

    def test_every_scenario_has_floor_and_max_iters(self):
        for s in agent_scenarios.build_scenarios():
            assert s.expected_tool_floor >= 0
            assert s.max_iters > 0
            assert callable(s.setup)
            assert callable(s.verify)
            # Either the task threads through a scratch dir, OR the scenario
            # operates on the real project root (eos_use_project_root=True for
            # open-ended app-creation scenarios like canvas-app).
            assert "{scratch}" in s.task_template or getattr(s, "eos_use_project_root", False), (
                f"scenario {s.id!r} has neither {{scratch}} in task_template "
                f"nor eos_use_project_root=True"
            )


# ── Learning-loop metadata (quick-wins bundle) ───────────────────────

class TestProvenanceHelpers:
    def test_git_sha_returns_string(self):
        sha = agent_bench._git_sha()
        # In a git checkout we expect 40-char hex; in a non-repo zip we accept ""
        assert isinstance(sha, str)
        if sha:
            assert len(sha) == 40
            assert all(c in "0123456789abcdef" for c in sha)

    def test_prompt_hash_is_stable(self):
        h1 = agent_bench._prompt_hash("You are an agent...")
        h2 = agent_bench._prompt_hash("You are an agent...")
        h3 = agent_bench._prompt_hash("You are a different agent...")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 12
        assert agent_bench._prompt_hash("") == ""

    def test_new_run_group_id_is_unique_and_tagged(self):
        a = agent_bench.new_run_group_id()
        b = agent_bench.new_run_group_id()
        assert a != b
        assert a.startswith("grp_")


class TestDiagnosticsFromTranscript:
    def test_tool_histogram_counts_by_name(self):
        events = [
            {"type": "agent:tool_call", "name": "Read"},
            {"type": "agent:tool_call", "name": "Read"},
            {"type": "agent:tool_call", "name": "Edit"},
            {"type": "agent:tool_result", "name": "Read", "is_error": False},  # not a tool_call; ignored
        ]
        h = agent_bench._tool_histogram(events)
        assert h == {"Read": 2, "Edit": 1}

    def test_empty_events_yields_empty_histogram(self):
        assert agent_bench._tool_histogram([]) == {}

    def test_error_categorize_bash_metachar(self):
        cat = agent_bench._categorize_error(
            "error: metacharacter '|' is not supported",
            {"name": "Bash", "command": "ls | grep foo"},
        )
        assert cat == "bash_shell_limitation"

    def test_error_categorize_timeout(self):
        cat = agent_bench._categorize_error("error: timed out after 30s", {"name": "Bash"})
        assert cat == "timeout"

    def test_error_categorize_missing_file(self):
        cat = agent_bench._categorize_error("error: file not found: /tmp/x", {"name": "Read"})
        assert cat == "missing_target"

    def test_error_categorize_edit_ambiguous(self):
        cat = agent_bench._categorize_error(
            "error: old_string occurs 3 times in /a.py", {"name": "Edit"},
        )
        assert cat == "edit_ambiguous"

    def test_error_categorize_bash_exit_code_fallback(self):
        # No recognized substring but exit_code != 0 in display → bash_nonzero_exit
        cat = agent_bench._categorize_error(
            "some obscure error", {"name": "Bash", "exit_code": 2},
        )
        assert cat == "bash_nonzero_exit"

    def test_error_categorize_unknown_becomes_other(self):
        cat = agent_bench._categorize_error("wat", {"name": "Read"})
        assert cat == "other"

    def test_error_categories_aggregates(self):
        events = [
            {"type": "agent:tool_result", "is_error": True,
             "error_snippet": "error: timed out after 30s", "display": {"name": "Bash"}},
            {"type": "agent:tool_result", "is_error": True,
             "error_snippet": "error: metacharacter '|'", "display": {"name": "Bash"}},
            {"type": "agent:tool_result", "is_error": True,
             "error_snippet": "error: metacharacter ';'", "display": {"name": "Bash"}},
            {"type": "agent:tool_result", "is_error": False,
             "display": {"name": "Read"}},  # successes skipped
            {"type": "agent:tool_call", "name": "Read"},  # not a result; skipped
        ]
        cats = agent_bench._error_categories(events)
        assert cats == {"timeout": 1, "bash_shell_limitation": 2}


class TestRunMetadataWiring:
    def test_failed_result_carries_group_and_variant(self, tmp_path):
        s = agent_scenarios.build_scenarios()[0]
        r = agent_bench._failed_result(
            "r1", s, "eos+ollama", tmp_path,
            error="test",
            run_group_id="grp_xyz",
            variant_id="strict",
        )
        assert r.run_group_id == "grp_xyz"
        assert r.variant_id == "strict"
        # Git sha is captured on _failed_result too
        assert isinstance(r.eos_git_sha, str)

    def test_agent_run_result_default_metadata_fields(self):
        r = agent_bench.AgentRunResult(
            run_id="r", scenario_id="s", subject_id="eos+ollama",
            ok=True, tool_calls=1, tool_errors=0, iterations=1, wall_ms=1,
        )
        assert r.run_group_id == ""
        assert r.variant_id == ""
        assert r.eos_git_sha == ""
        assert r.system_prompt_hash == ""
        assert r.tool_histogram == {}
        assert r.error_categories == {}

    def test_save_roundtrip_preserves_new_fields(self, tmp_path):
        r = agent_bench.AgentRunResult(
            run_id="r", scenario_id="s", subject_id="eos+ollama",
            ok=True, tool_calls=2, tool_errors=0, iterations=1, wall_ms=100,
            run_group_id="grp_abc", variant_id="v1",
            eos_git_sha="deadbeef" * 5, system_prompt_hash="abcdef012345",
            tool_histogram={"Read": 1, "Write": 1},
            error_categories={"timeout": 1},
            subject_model="qwen3.5:latest",
        )
        agent_bench.save_results(tmp_path, [r])
        loaded = agent_bench.load_results(tmp_path)
        assert len(loaded) == 1
        l = loaded[0]
        assert l["run_group_id"] == "grp_abc"
        assert l["variant_id"] == "v1"
        assert l["system_prompt_hash"] == "abcdef012345"
        assert l["tool_histogram"] == {"Read": 1, "Write": 1}
        assert l["error_categories"] == {"timeout": 1}
        assert l["subject_model"] == "qwen3.5:latest"


class TestSubjectModelResolution:
    def test_resolve_from_usage_wins_over_provider(self):
        class FakeProvider:
            model = "provider-default"
        m = agent_bench._resolve_subject_model(
            FakeProvider(), {"model": "gpt-4.1-mini", "prompt_tokens": 100},
        )
        assert m == "gpt-4.1-mini"

    def test_resolve_falls_back_to_provider_model_attr(self):
        class FakeProvider:
            model = "claude-sonnet-4-6"
        m = agent_bench._resolve_subject_model(FakeProvider(), {})
        assert m == "claude-sonnet-4-6"

    def test_resolve_falls_back_to_private_attr(self):
        class FakeProvider:
            _model = "qwen3.5:4b"
        m = agent_bench._resolve_subject_model(FakeProvider(), {})
        assert m == "qwen3.5:4b"

    def test_resolve_returns_empty_when_unknown(self):
        class FakeProvider:
            pass
        m = agent_bench._resolve_subject_model(FakeProvider(), {})
        assert m == ""

    def test_model_from_stream_events_uses_system_init(self):
        events = [
            {"type": "system", "subtype": "init", "model": "claude-opus-4-7[1m]"},
            {"type": "assistant", "message": {"model": "claude-opus-4-7"}},
        ]
        assert agent_bench._model_from_stream_events(events) == "claude-opus-4-7[1m]"

    def test_model_from_stream_events_falls_back_to_assistant(self):
        events = [
            {"type": "assistant", "message": {"model": "claude-opus-4-7"}},
        ]
        assert agent_bench._model_from_stream_events(events) == "claude-opus-4-7"

    def test_model_from_stream_events_empty(self):
        assert agent_bench._model_from_stream_events([]) == ""
        assert agent_bench._model_from_stream_events([{"type": "foo"}]) == ""

    def test_subject_model_default_is_empty(self):
        r = agent_bench.AgentRunResult(
            run_id="r", scenario_id="s", subject_id="eos+ollama",
            ok=True, tool_calls=0, tool_errors=0, iterations=0, wall_ms=0,
        )
        assert r.subject_model == ""


class TestAliasResolver:
    """The bench's subject aliases ('claude', 'openai', 'ollama') don't
    match provider class names directly — we must translate."""

    class _FakeProv:
        def __init__(self, name, model="", endpoint=""):
            self.name = name
            self.model = model
            self.endpoint = endpoint
        # Mark as a ToolCapableProvider via MRO in real code; here we
        # bypass the isinstance check with a minimal mock.

    def _make_agent_app(self, providers):
        """Stub agent_app with kernel.capability('think') returning something
        walkable. The real agent has a cap with .providers and dict chains."""
        class _Cap:
            def __init__(self, ps): self.providers = ps; self._domains = {}; self._buckets = {}
        class _Kernel:
            def __init__(self, ps):
                self._cap = _Cap(ps)
            def capability(self, _name): return self._cap
        class _AgentApp:
            def __init__(self, ps):
                self.kernel = _Kernel(ps)
                self._providers = ps
            def _resolve_provider(self, name):
                for p in self._providers:
                    if p.name == name:
                        return p
                return None
        return _AgentApp(providers)

    def test_claude_alias_matches_claude_cli_name(self, monkeypatch):
        from emptyos.capabilities.providers import _tool_capable
        class Prov(_tool_capable.NativelyAgenticProvider):
            name = "claude-cli"
            model = "claude-opus-4-7"
        prov = Prov()
        app = self._make_agent_app([prov])
        got = agent_bench._resolve_bench_subject_provider(app, "claude")
        assert got is prov

    def test_openai_alias_matches_openai_endpoint(self):
        from emptyos.capabilities.providers import _tool_capable
        class OA(_tool_capable.ToolCapableProvider):
            name = "openai_compat"
            model = "gpt-4.1-mini"
            endpoint = "https://api.openai.com/v1"
            kind = "openai"
            async def execute_tools(self, **kw): pass
        class Ollama(_tool_capable.ToolCapableProvider):
            name = "openai_compat"
            model = "qwen3.5:latest"
            endpoint = "http://localhost:11434/v1"
            kind = "openai"
            async def execute_tools(self, **kw): pass
        oa, oll = OA(), Ollama()
        app = self._make_agent_app([oa, oll])
        assert agent_bench._resolve_bench_subject_provider(app, "openai") is oa
        assert agent_bench._resolve_bench_subject_provider(app, "ollama") is oll

    def test_ollama_alias_by_port(self):
        from emptyos.capabilities.providers import _tool_capable
        class Ollama(_tool_capable.ToolCapableProvider):
            name = "openai_compat"
            model = "llama3.1"
            endpoint = "http://my-host:11434/v1"
            kind = "openai"
            async def execute_tools(self, **kw): pass
        p = Ollama()
        app = self._make_agent_app([p])
        assert agent_bench._resolve_bench_subject_provider(app, "ollama") is p

    def test_unknown_alias_falls_back_to_exact_match(self):
        from emptyos.capabilities.providers import _tool_capable
        class Custom(_tool_capable.ToolCapableProvider):
            name = "my-custom-provider"
            model = "x"
            kind = "openai"
            async def execute_tools(self, **kw): pass
        p = Custom()
        app = self._make_agent_app([p])
        # Exact name match via the app's fallback resolver
        assert agent_bench._resolve_bench_subject_provider(app, "my-custom-provider") is p

    # ── Provider-tier prompt overlays ──────────────────────────

    def test_overlay_applied_to_local_provider(self):
        class Prov:
            name = "ollama"
            is_cloud = False
        overlay = agent_bench._provider_prompt_overlay(Prov())
        assert overlay != ""
        assert "MUST call Edit" in overlay or "MUST call Edit" in overlay.upper()

    def test_overlay_applied_to_local_openai_compat(self):
        class Prov:
            name = "openai_compat"
            is_cloud = False
        assert agent_bench._provider_prompt_overlay(Prov()) != ""

    def test_overlay_empty_for_cloud_provider(self):
        class Prov:
            name = "openai"
            is_cloud = True
        assert agent_bench._provider_prompt_overlay(Prov()) == ""

    def test_overlay_empty_for_claude_cli(self):
        class Prov:
            name = "claude-cli"
            is_cloud = True
        assert agent_bench._provider_prompt_overlay(Prov()) == ""

    def test_overlay_empty_for_anthropic_sdk(self):
        class Prov:
            name = "anthropic_sdk"
            is_cloud = True
        assert agent_bench._provider_prompt_overlay(Prov()) == ""

    def test_overlay_defaults_to_empty_when_is_cloud_missing(self):
        # Conservative default: unknown provider → treat as cloud (no overlay).
        # Better to under-apply than push frontier models through small-model scaffolding.
        class Prov:
            name = "weird-custom"
        assert agent_bench._provider_prompt_overlay(Prov()) == ""


class TestCostEstimation:
    def test_prefer_explicit_cost_field(self):
        # Providers that already report cost (claude-external's stream-json) win
        assert agent_bench._compute_cost_usd("anything", {"total_cost_usd": 0.1234}) == 0.1234
        assert agent_bench._compute_cost_usd("anything", {"cost": 0.001}) == 0.001

    def test_compute_from_openai_tokens(self):
        # gpt-4.1-mini = (0.40, 1.60) per M — 1M input + 1M output = $2.00
        cost = agent_bench._compute_cost_usd(
            "gpt-4.1-mini",
            {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        )
        assert cost == 2.0

    def test_compute_from_anthropic_tokens(self):
        # claude-sonnet-4-6 = (3.0, 15.0) per M — 1k input + 1k output = $0.018
        cost = agent_bench._compute_cost_usd(
            "claude-sonnet-4-6",
            {"input_tokens": 1000, "output_tokens": 1000},
        )
        assert cost == 0.018

    def test_compute_with_suffixed_model_string(self):
        # Real-world claude model string includes extras like "[1m]"
        cost = agent_bench._compute_cost_usd(
            "claude-opus-4-7[1m]",
            {"input_tokens": 1000, "output_tokens": 1000},
        )
        # 1k × (15 + 75) / 1M = 0.090
        assert cost == 0.09

    def test_local_models_are_free(self):
        assert agent_bench._compute_cost_usd(
            "qwen3.5:latest",
            {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        ) == 0.0

    def test_unknown_model_returns_zero(self):
        # Better to under-report than fabricate
        assert agent_bench._compute_cost_usd(
            "some-new-model-nobody-priced",
            {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        ) == 0.0

    def test_missing_usage_returns_zero(self):
        assert agent_bench._compute_cost_usd("gpt-4.1-mini", {}) == 0.0
        assert agent_bench._compute_cost_usd("gpt-4.1-mini", None) == 0.0

    def test_token_counts_normalize_keys(self):
        # OpenAI shape
        assert agent_bench._token_counts({"prompt_tokens": 10, "completion_tokens": 20}) == (10, 20)
        # Anthropic shape
        assert agent_bench._token_counts({"input_tokens": 10, "output_tokens": 20}) == (10, 20)
        # Empty
        assert agent_bench._token_counts({}) == (0, 0)

    def test_cost_and_rep_defaults_on_agent_run_result(self):
        r = agent_bench.AgentRunResult(
            run_id="r", scenario_id="s", subject_id="eos+ollama",
            ok=True, tool_calls=0, tool_errors=0, iterations=0, wall_ms=0,
        )
        assert r.cost_usd == 0.0
        assert r.rep_index == 0
        assert r.overlay_applied is False

    def test_roundtrip_preserves_cost_and_rep(self, tmp_path):
        r = agent_bench.AgentRunResult(
            run_id="r", scenario_id="s", subject_id="eos+openai",
            ok=True, tool_calls=2, tool_errors=0, iterations=1, wall_ms=100,
            cost_usd=0.001234, rep_index=3, overlay_applied=True,
        )
        agent_bench.save_results(tmp_path, [r])
        loaded = agent_bench.load_results(tmp_path)
        assert loaded[0]["cost_usd"] == 0.001234
        assert loaded[0]["rep_index"] == 3
        assert loaded[0]["overlay_applied"] is True


class TestClaudeAliasNoFallThrough:
    """Regression: when 'claude' alias is asked but no claude-cli / anthropic
    provider is registered, the resolver must return None — not silently
    hand back the first ToolCapable provider (which used to be openai).

    Separated into its own class because it needs the same stub infra as
    TestAliasResolver but got orphaned by an earlier refactor."""

    def _make_agent_app(self, providers):
        class _Cap:
            def __init__(self, ps): self.providers = ps; self._domains = {}; self._buckets = {}
        class _Kernel:
            def __init__(self, ps): self._cap = _Cap(ps)
            def capability(self, _n): return self._cap
        class _AgentApp:
            def __init__(self, ps):
                self.kernel = _Kernel(ps)
                self._providers = ps
            def _resolve_provider(self, name):
                for p in self._providers:
                    if p.name == name:
                        return p
                return None
        return _AgentApp(providers)

    def test_claude_alias_does_not_fall_through_to_openai(self):
        from emptyos.capabilities.providers import _tool_capable
        class OA(_tool_capable.ToolCapableProvider):
            name = "openai_compat"
            model = "gpt-4.1-mini"
            endpoint = "https://api.openai.com/v1"
            kind = "openai"
            async def execute_tools(self, **kw): pass
        app = self._make_agent_app([OA()])
        assert agent_bench._resolve_bench_subject_provider(app, "claude") is None
