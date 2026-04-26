"""Seed scenarios for agent-bench.

Each scenario is a tuple of (instruction, setup, deterministic verifier).
The verifier is what makes this different from text-bench: we measure
*did the task actually get done*, not just "did the model respond".

Scenarios currently registered:
  - write-new-util      — Write-focused. Create a new file with one function.
  - add-temperature     — Edit-focused. Add a temperature= kwarg to a think() call.
  - find-missing-tests  — Grep + CallApp. Count SDK gaps, open tasks for each.
  - explain-structure   — Read-only. List top-level dirs with purposes.

To add a scenario: write setup/verify callables, build an `AgentScenario`,
register in `SCENARIOS`.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path

from .agent_bench import AgentScenario, VerifyResult


# ── Scenario 1 — write-new-util ──────────────────────────────────────

_SLUGIFY_TASK = """Create a new Python file at `{scratch}/strings.py`.

Requirements:
- Define a single function `slugify(s: str) -> str`.
- `slugify` should lowercase the string, strip leading/trailing whitespace,
  and replace any run of non-alphanumeric characters with a single hyphen.
  Leading and trailing hyphens in the result should be stripped.
- Examples:
    slugify("Hello World")     -> "hello-world"
    slugify("  Foo  Bar!  ")   -> "foo-bar"
    slugify("multi--dash")     -> "multi-dash"
    slugify("UPPERCASE")       -> "uppercase"
- The file should be import-safe — no side effects at module import time.
- Do not create any other files.
"""


def _setup_write_new_util(scratch: Path) -> None:
    # Nothing to seed — scratch dir is empty, agent creates the file.
    pass


def _verify_write_new_util(scratch: Path) -> VerifyResult:
    target = scratch / "strings.py"
    if not target.exists():
        return VerifyResult(ok=False, notes=f"strings.py not created at {target}")
    try:
        spec = importlib.util.spec_from_file_location(
            f"scratch_strings_{scratch.name}", target,
        )
        if spec is None or spec.loader is None:
            return VerifyResult(ok=False, notes="could not build import spec")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return VerifyResult(ok=False, notes=f"import failed: {type(e).__name__}: {e}")

    slug = getattr(mod, "slugify", None)
    if not callable(slug):
        return VerifyResult(ok=False, notes="no slugify callable found")

    cases = [
        ("Hello World", "hello-world"),
        ("  Foo  Bar!  ", "foo-bar"),
        ("multi--dash", "multi-dash"),
        ("UPPERCASE", "uppercase"),
    ]
    failures = []
    for inp, want in cases:
        try:
            got = slug(inp)
        except Exception as e:
            failures.append(f"slugify({inp!r}) raised {e}")
            continue
        if got != want:
            failures.append(f"slugify({inp!r}) = {got!r}, want {want!r}")
    if failures:
        return VerifyResult(ok=False, notes="; ".join(failures))
    return VerifyResult(ok=True, notes=f"slugify passed {len(cases)} cases")


# ── Scenario 2 — add-temperature (Edit-focused) ──────────────────────

_TEMPERATURE_TASK = """In `{scratch}/focus_app.py`, find any `self.think(...)` call that is
missing a `temperature=` kwarg. For that call:
  1. Read 15 lines of surrounding context.
  2. Classify the task per EmptyOS rule 12:
       - parsing / extraction / classification: temperature 0.1-0.3
       - analysis / reasoning: 0.3-0.5
       - creative / selective / generative: 0.6-0.8
  3. Pick one specific temperature in the appropriate band.
  4. Use Edit to add `temperature=<value>` to that call, keeping other kwargs.
Return a one-line summary: `<line_no> <classification> temperature=<value>`.
Do not edit any other file.
"""


def _setup_add_temperature(scratch: Path) -> None:
    # Copy the real focus/app.py as a fixture. We copy, not link, so that
    # edits are isolated to this scratch.
    src = _repo_root() / "apps" / "focus" / "app.py"
    dst = scratch / "focus_app.py"
    shutil.copy2(src, dst)


def _verify_add_temperature(scratch: Path) -> VerifyResult:
    target = scratch / "focus_app.py"
    if not target.exists():
        return VerifyResult(ok=False, notes="focus_app.py missing (setup failed?)")
    text = target.read_text(encoding="utf-8")

    # Locate the target call: `self.think(` with no `temperature=` before its
    # matching close paren.
    missing_calls = _calls_missing_temperature(text)
    if missing_calls:
        return VerifyResult(
            ok=False,
            notes=f"{len(missing_calls)} self.think() call(s) still missing temperature=",
        )

    # Sanity: a temperature= did appear somewhere with a plausible value
    m = re.search(r"temperature\s*=\s*([0-9]*\.?[0-9]+)", text)
    if not m:
        return VerifyResult(
            ok=False,
            notes="no temperature= literal found anywhere in file",
        )
    val = float(m.group(1))
    if not (0.0 <= val <= 1.0):
        return VerifyResult(ok=False, notes=f"temperature={val} out of 0..1 range")
    return VerifyResult(ok=True, notes=f"temperature= added, value {val}")


def _calls_missing_temperature(text: str) -> list[tuple[int, str]]:
    """Return (line_no, snippet) for every `await self.think(...)` call that
    lacks a `temperature=` kwarg anywhere inside its paren-balanced body."""
    missing = []
    for m in re.finditer(r"await\s+self\.think(?:_stream|_compare)?\s*\(", text):
        start = m.end() - 1
        depth = 0
        end = start
        while end < len(text):
            c = text[end]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        call = text[m.start():end + 1]
        if "temperature" not in call:
            line_no = text[: m.start()].count("\n") + 1
            missing.append((line_no, call[:120]))
    return missing


# ── Scenario 3 — find-missing-tests (Grep + CallApp) ─────────────────

_FIND_TESTS_TASK = """The scratch dir contains two folders:
  - `{scratch}/sdk/`   — Python modules
  - `{scratch}/tests/` — pytest test files following the `test_<module>.py` convention

Some sdk modules have no matching test file. Your task:
  1. Use Glob with ABSOLUTE patterns `{scratch}/sdk/*.py` and
     `{scratch}/tests/test_*.py` to list the two folders.
  2. For each sdk module in `{scratch}/sdk/` WITHOUT a matching test in
     `{scratch}/tests/`, append a single line to `{scratch}/gaps.txt`
     in the form: `<module_name>\\n`.
  3. Do not create any test files. Just the gaps.txt manifest.
"""


def _setup_find_missing_tests(scratch: Path) -> None:
    sdk = scratch / "sdk"
    tests = scratch / "tests"
    sdk.mkdir()
    tests.mkdir()
    # 4 modules, 2 have tests, 2 do not.
    modules = {
        "strings.py":   "def slugify(s): return s.lower()",
        "dates.py":     "def today(): pass",
        "numbers.py":   "def add(a, b): return a + b",
        "lists.py":     "def head(xs): return xs[0]",
    }
    for name, body in modules.items():
        (sdk / name).write_text(body, encoding="utf-8")
    (tests / "test_strings.py").write_text("def test_slug(): pass", encoding="utf-8")
    (tests / "test_numbers.py").write_text("def test_add(): pass", encoding="utf-8")
    # Expected gaps: dates, lists


def _verify_find_missing_tests(scratch: Path) -> VerifyResult:
    gaps = scratch / "gaps.txt"
    if not gaps.exists():
        return VerifyResult(ok=False, notes="gaps.txt not created")
    found = {l.strip() for l in gaps.read_text(encoding="utf-8").splitlines() if l.strip()}
    # Accept either bare module name (dates) or filename (dates.py)
    normalized = {f.removesuffix(".py") for f in found}
    expected = {"dates", "lists"}
    if normalized == expected:
        return VerifyResult(ok=True, notes=f"gaps exactly matched: {sorted(expected)}")
    missing = expected - normalized
    extra = normalized - expected
    bits = []
    if missing:
        bits.append(f"missed: {sorted(missing)}")
    if extra:
        bits.append(f"extra: {sorted(extra)}")
    return VerifyResult(ok=False, notes="; ".join(bits) or "mismatch")


# ── Scenario 4 — explain-structure (read-only) ───────────────────────

_EXPLAIN_TASK = """Look at the directory `{scratch}/fake_app/`.
List each top-level subdirectory and explain, in one short sentence,
what that subdirectory is for. Output as plain text, one dir per line:
`<name>: <purpose>`.
Do not list files, only subdirectories. Do not modify anything.
"""


def _setup_explain_structure(scratch: Path) -> None:
    app = scratch / "fake_app"
    (app / "tools").mkdir(parents=True)
    (app / "pages").mkdir()
    (app / "data").mkdir()
    (app / "tools" / "grep.py").write_text("# the grep tool", encoding="utf-8")
    (app / "pages" / "index.html").write_text("<html></html>", encoding="utf-8")
    (app / "data" / "store.json").write_text("{}", encoding="utf-8")
    (app / "app.py").write_text("class App: pass", encoding="utf-8")
    (app / "manifest.toml").write_text("[app]\nid='fake'", encoding="utf-8")


def _verify_explain_structure(scratch: Path) -> VerifyResult:
    # No artifact — this is read-only. The agent's final text lives in the
    # transcript. For verifier purposes we consider it OK if the agent
    # DIDN'T mutate anything and the transcript's final text (loaded from
    # the result's transcript_path) mentions each of the three subdirs.
    # The harness doesn't have the transcript at verify time (by design —
    # verify is pure filesystem). So this verifier checks only the
    # non-mutation invariant; tool_calls > 0 is checked by the runner
    # to distinguish "answered without reading" from "answered after reading".
    app = scratch / "fake_app"
    expected_files = [
        app / "tools" / "grep.py",
        app / "pages" / "index.html",
        app / "data" / "store.json",
        app / "app.py",
        app / "manifest.toml",
    ]
    missing = [str(p) for p in expected_files if not p.exists()]
    if missing:
        return VerifyResult(ok=False, notes=f"agent mutated fixture (missing: {missing})")
    return VerifyResult(ok=True, notes="fixture intact (answer quality checked via transcript)")


# ── Scenario 5 — call-app-discovery (CallApp-focused) ────────────────
#
# Tests the EmptyOS-specific advantage: CallApp lets an eos+ agent
# enumerate and talk to other apps natively. claude-external has no
# CallApp equivalent — it would have to read files or hit the HTTP API,
# which is slower and more error-prone. That asymmetry is the point.

_CALL_APP_TASK = """Using the CallApp tool, discover EmptyOS apps:

1. Call CallApp with NO arguments to list all available apps.
2. For each of these three specific apps — `task`, `journal`, `capture` —
   call CallApp with just `app_id="<name>"` (no method) to list that
   app's methods.
3. Write `{scratch}/apps_summary.txt` with EXACTLY three lines, one per
   app, in this form:
       task has <N> methods
       journal has <N> methods
       capture has <N> methods
   (Substitute each <N> with the count you observed.)

Do NOT call any app's actual methods — only enumerate them via CallApp.
Do NOT use Bash, Read, or HTTP for this task. CallApp is the way.
"""

_SUMMARY_LINE_RE = re.compile(
    r"^(?P<app>task|journal|capture)\s+has\s+(?P<n>\d+)\s+methods?\s*$",
    re.IGNORECASE,
)


def _setup_call_app_discovery(scratch: Path) -> None:
    # No fixture — this scenario exercises the live app graph.
    pass


def _verify_call_app_discovery(scratch: Path) -> VerifyResult:
    summary = scratch / "apps_summary.txt"
    if not summary.exists():
        return VerifyResult(ok=False, notes="apps_summary.txt not created")
    lines = [l for l in summary.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(lines) != 3:
        return VerifyResult(ok=False, notes=f"expected 3 lines, got {len(lines)}")
    seen: dict[str, int] = {}
    for l in lines:
        m = _SUMMARY_LINE_RE.match(l.strip())
        if not m:
            return VerifyResult(
                ok=False,
                notes=f"line doesn't match '<app> has <N> methods': {l!r}",
            )
        app = m.group("app").lower()
        if app in seen:
            return VerifyResult(ok=False, notes=f"{app!r} listed twice")
        seen[app] = int(m.group("n"))
    needed = {"task", "journal", "quick-action"}
    if set(seen) != needed:
        return VerifyResult(ok=False, notes=f"wrong apps: {sorted(seen)}")
    # Sanity: any real EmptyOS app exposes at least a handful of methods.
    too_few = [a for a, n in seen.items() if n < 3]
    if too_few:
        return VerifyResult(
            ok=False,
            notes=f"implausibly low method counts: {too_few} — did the agent actually call CallApp?",
        )
    return VerifyResult(
        ok=True,
        notes=f"method counts task={seen['task']} journal={seen['journal']} capture={seen['quick-action']}",
    )


# ── Scenario 6 — grep-replace (Grep-directed Edit chain) ─────────────

_GREP_REPLACE_TASK = """In every `.py` file under `{scratch}/pkg/`, replace
every occurrence of `print("hello")` with `print("world")`. Leave all
other code unchanged.

Workflow:
  1. Use Grep to find every occurrence of the literal pattern across
     the pkg/ directory.
  2. For each file that has a match, use Edit (with `replace_all=true`
     when the file has multiple occurrences) to swap `print("hello")`
     → `print("world")`.
  3. Verify with Grep that no `print("hello")` remains.
"""


def _setup_grep_replace(scratch: Path) -> None:
    pkg = scratch / "pkg"
    pkg.mkdir()
    # 3 files, 4 total occurrences (one file has two)
    (pkg / "a.py").write_text(
        'def greet():\n    print("hello")\n    return 1\n',
        encoding="utf-8",
    )
    (pkg / "b.py").write_text(
        'def one():\n    print("hello")\n\ndef two():\n    print("hello")\n',
        encoding="utf-8",
    )
    (pkg / "c.py").write_text(
        'import sys\nprint("hello")\nsys.exit(0)\n',
        encoding="utf-8",
    )
    # A decoy file that doesn't match — shouldn't be touched
    (pkg / "nope.py").write_text(
        'def other():\n    print("goodbye")\n',
        encoding="utf-8",
    )


def _verify_grep_replace(scratch: Path) -> VerifyResult:
    pkg = scratch / "pkg"
    if not pkg.exists():
        return VerifyResult(ok=False, notes="pkg/ missing (setup failed)")
    hello_count = 0
    world_count = 0
    decoy_modified = False
    for p in pkg.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        hello_count += text.count('print("hello")')
        world_count += text.count('print("world")')
        if p.name == "nope.py" and 'print("goodbye")' not in text:
            decoy_modified = True
    if decoy_modified:
        return VerifyResult(
            ok=False,
            notes="nope.py was modified — agent touched the decoy file",
        )
    if hello_count != 0:
        return VerifyResult(
            ok=False,
            notes=f"{hello_count} `print(\"hello\")` occurrence(s) still present",
        )
    if world_count != 4:
        return VerifyResult(
            ok=False,
            notes=f"expected 4 `print(\"world\")`, got {world_count}",
        )
    return VerifyResult(ok=True, notes="all 4 occurrences swapped, decoy untouched")


# ── Scenario 7 — multi-file-refactor (rename across files) ───────────

_REFACTOR_TASK = """A module `{scratch}/lib/` has a function called
`old_func` that is used in several other files. Rename `old_func` to
`new_func` EVERYWHERE — definition, imports, and calls — across all
`.py` files under `{scratch}/lib/`.

Workflow:
  1. Use Grep to find every file referencing `old_func`.
  2. For each file, Edit each occurrence (use `replace_all=true` when a
     file has multiple occurrences; match the word carefully so you
     don't touch unrelated strings).
  3. The symbol appears as a definition (`def old_func`), as an import
     (`from <x> import old_func`), and as a call (`old_func(...)`).

When you're done, zero occurrences of `old_func` should remain, and
`new_func` should appear the same number of times `old_func` did.
"""


def _setup_multi_file_refactor(scratch: Path) -> None:
    lib = scratch / "lib"
    lib.mkdir()
    # Definition
    (lib / "core.py").write_text(
        "def old_func(x):\n"
        "    \"\"\"Does something.\"\"\"\n"
        "    return x * 2\n",
        encoding="utf-8",
    )
    # Two callers + one importer
    (lib / "use_a.py").write_text(
        "from core import old_func\n\n"
        "print(old_func(3))\n",
        encoding="utf-8",
    )
    (lib / "use_b.py").write_text(
        "from core import old_func\n\n"
        "def run():\n"
        "    return old_func(10) + old_func(20)\n",
        encoding="utf-8",
    )
    # A decoy that has a similar name — should NOT be touched
    (lib / "decoy.py").write_text(
        "def old_function():\n"
        "    pass\n"
        "\n"
        "# note: old_funcs (plural) also stays as-is\n",
        encoding="utf-8",
    )


def _verify_multi_file_refactor(scratch: Path) -> VerifyResult:
    lib = scratch / "lib"
    if not lib.exists():
        return VerifyResult(ok=False, notes="lib/ missing (setup failed)")
    # Match `old_func` as a whole word — but tolerate that `new_func` should
    # NOT be matched as `old_func`. Use explicit word boundaries.
    word_old = re.compile(r"\bold_func\b")
    word_new = re.compile(r"\bnew_func\b")
    old_count = 0
    new_count = 0
    decoy_touched = False
    for p in lib.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        old_count += len(word_old.findall(text))
        new_count += len(word_new.findall(text))
        if p.name == "decoy.py":
            if "old_function" not in text or "old_funcs" not in text:
                decoy_touched = True
    if decoy_touched:
        return VerifyResult(
            ok=False,
            notes="decoy.py was touched — agent matched too aggressively",
        )
    if old_count != 0:
        return VerifyResult(
            ok=False,
            notes=f"{old_count} `old_func` reference(s) still present",
        )
    # Expected: 1 def + 2 imports + 3 calls = 6 occurrences
    if new_count != 6:
        return VerifyResult(
            ok=False,
            notes=f"expected 6 `new_func` occurrences, got {new_count}",
        )
    return VerifyResult(ok=True, notes="all 6 occurrences renamed, decoy untouched")


# ── Scenario 8 — debug-and-fix (Bash-verify loop) ────────────────────

_DEBUG_FIX_TASK = """There is a bug in `{scratch}/broken.py` — one function is
implemented incorrectly. A test runner `{scratch}/run_tests.py` prints
PASS/FAIL lines and exits 0 on all-pass, 1 otherwise.

Workflow:
  1. Run `python {scratch}/run_tests.py` via Bash to see which tests
     fail and by how much.
  2. Read `{scratch}/broken.py` to find the buggy function.
  3. Edit `{scratch}/broken.py` to fix the bug. Do NOT edit
     `{scratch}/run_tests.py`.
  4. Run the tests again via Bash to confirm the fix — all tests must
     pass (exit code 0).

Your task is only complete when the second `python ... run_tests.py`
invocation exits successfully.
"""


def _setup_debug_and_fix(scratch: Path) -> None:
    (scratch / "broken.py").write_text(
        "def multiply(x, y):\n"
        "    # BUG: this should multiply, not add\n"
        "    return x + y\n"
        "\n"
        "def factorial(n):\n"
        "    if n <= 1:\n"
        "        return 1\n"
        "    return n * factorial(n - 1)\n",
        encoding="utf-8",
    )
    (scratch / "run_tests.py").write_text(
        'import sys\n'
        '# Avoid bytecode caching — on Windows, mtime granularity can cause\n'
        '# Python to reuse a stale .pyc after a same-second edit, making the\n'
        '# agent appear to have not fixed the bug.\n'
        'sys.dont_write_bytecode = True\n'
        'sys.path.insert(0, ".")\n'
        'from broken import multiply, factorial\n'
        '\n'
        'cases = [\n'
        '    ("multiply(3, 4)", multiply(3, 4), 12),\n'
        '    ("multiply(5, 2)", multiply(5, 2), 10),\n'
        '    ("factorial(5)", factorial(5), 120),\n'
        '    ("factorial(0)", factorial(0), 1),\n'
        ']\n'
        'ok = True\n'
        'for name, got, want in cases:\n'
        '    mark = "PASS" if got == want else "FAIL"\n'
        '    print(f"{mark}: {name} = {got}, expected {want}")\n'
        '    if got != want:\n'
        '        ok = False\n'
        'sys.exit(0 if ok else 1)\n',
        encoding="utf-8",
    )


def _verify_debug_and_fix(scratch: Path) -> VerifyResult:
    broken = scratch / "broken.py"
    runner = scratch / "run_tests.py"
    if not broken.exists():
        return VerifyResult(ok=False, notes="broken.py missing (setup failed)")
    if not runner.exists():
        return VerifyResult(ok=False, notes="run_tests.py missing (agent deleted it?)")
    # Did the agent touch run_tests.py? Its content is deterministic;
    # check the import statement survived.
    if "from broken import multiply, factorial" not in runner.read_text(encoding="utf-8"):
        return VerifyResult(ok=False, notes="run_tests.py was modified (agent should not edit it)")
    # Run the tests ourselves to verify the fix works. Use the scratch as cwd
    # so `from broken import ...` resolves. Use -B to skip bytecode caching
    # and remove any __pycache__ left by the agent's own runs — on Windows
    # same-second mtime collisions can make a stale .pyc look fresh.
    import subprocess, sys, shutil as _shutil
    cache = scratch / "__pycache__"
    if cache.exists():
        _shutil.rmtree(cache, ignore_errors=True)
    try:
        out = subprocess.run(
            [sys.executable, "-B", str(runner)],
            cwd=str(scratch), capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return VerifyResult(ok=False, notes=f"runner failed to execute: {e}")
    if out.returncode == 0:
        return VerifyResult(ok=True, notes=f"all tests pass — {out.stdout.count('PASS:')} assertions")
    return VerifyResult(
        ok=False,
        notes=f"runner exited {out.returncode}; still failing: {out.stdout[:200]!r}",
    )


# ── Scenario 9 — long-context-needle (Grep-first discipline) ─────────

_LONG_CONTEXT_TASK = """In `{scratch}/pkg/` there are many `.py` files. EXACTLY ONE
file contains the marker `NEEDLE(bench):` (followed by a description
on the same line).

Find it. Write `{scratch}/found.txt` with exactly one line in the form:
    <filename>: <text after "NEEDLE(bench):" on that line>

For example, if `module_42.py` has `# NEEDLE(bench): tune the batch size`,
write: `module_42.py: tune the batch size`.

Strategy note: the directory has many files. Using Grep to pattern-match
is dramatically faster than reading each file with Read. You do not need
to read every file.
"""

_LONG_CONTEXT_FILE_COUNT = 40
_LONG_CONTEXT_NEEDLE_FILE = "module_27.py"
_LONG_CONTEXT_NEEDLE_TEXT = "tune the batch size"


def _setup_long_context_needle(scratch: Path) -> None:
    pkg = scratch / "pkg"
    pkg.mkdir()
    for i in range(_LONG_CONTEXT_FILE_COUNT):
        name = f"module_{i:02d}.py"
        if name == _LONG_CONTEXT_NEEDLE_FILE:
            body = (
                f"# module_{i:02d} — routine utility\n"
                f"# NEEDLE(bench): {_LONG_CONTEXT_NEEDLE_TEXT}\n"
                f"def helper_{i:02d}(x):\n"
                f"    return x + {i}\n"
            )
        else:
            # Distinct filler — makes sure Grep on the marker finds exactly one hit
            body = (
                f"# module_{i:02d} — routine utility {i}\n"
                f"# this module computes something involving {i*3}\n"
                f"def helper_{i:02d}(x):\n"
                f"    return x + {i}\n"
            )
        (pkg / name).write_text(body, encoding="utf-8")


_NEEDLE_LINE_RE = re.compile(
    r"^\s*(?P<file>[^:\s]+)\s*:\s*(?P<text>.+?)\s*$",
)


def _verify_long_context_needle(scratch: Path) -> VerifyResult:
    found = scratch / "found.txt"
    if not found.exists():
        return VerifyResult(ok=False, notes="found.txt not created")
    content = found.read_text(encoding="utf-8").strip()
    lines = [l for l in content.splitlines() if l.strip()]
    if len(lines) != 1:
        return VerifyResult(ok=False, notes=f"expected exactly 1 line, got {len(lines)}")
    m = _NEEDLE_LINE_RE.match(lines[0])
    if not m:
        return VerifyResult(ok=False, notes=f"line not in '<filename>: <text>' form: {lines[0]!r}")
    filename = m.group("file").strip()
    text = m.group("text").strip()
    # Filename may or may not have the module_ prefix — accept either
    if not (filename == _LONG_CONTEXT_NEEDLE_FILE
            or filename.endswith("/" + _LONG_CONTEXT_NEEDLE_FILE)
            or filename.endswith("\\" + _LONG_CONTEXT_NEEDLE_FILE)):
        return VerifyResult(
            ok=False,
            notes=f"wrong file: {filename!r} (expected {_LONG_CONTEXT_NEEDLE_FILE})",
        )
    if _LONG_CONTEXT_NEEDLE_TEXT not in text:
        return VerifyResult(
            ok=False,
            notes=f"text doesn't contain {_LONG_CONTEXT_NEEDLE_TEXT!r}: got {text!r}",
        )
    return VerifyResult(ok=True, notes=f"needle found in {filename}")


# ── Scenario 10 — false-premise (diagnostic override) ────────────────
#
# The task claims there's a bug in `multiply`, but `multiply` is correct.
# A literal agent "fixes" the non-bug and breaks it. A diagnostic agent
# reads, verifies, and reports no bug. This tests whether models override
# the task text when evidence contradicts it.

_FALSE_PREMISE_TASK = """The `multiply` function in `{scratch}/lib.py` is reported
to have a bug. Read the file, determine if the bug is real, and fix it
if it is. The correctness criterion is `multiply(3, 4) == 12` and
`multiply(5, 2) == 10`.

If the function already satisfies these criteria, say so clearly — do
not edit working code unnecessarily. If it does not, make the minimal
fix to satisfy them.

Do not modify any function other than `multiply`.
"""


def _setup_false_premise(scratch: Path) -> None:
    (scratch / "lib.py").write_text(
        "def multiply(x, y):\n"
        "    # CORRECT implementation — do not change\n"
        "    return x * y\n"
        "\n"
        "def divide(x, y):\n"
        "    # (unrelated to this task — do not touch)\n"
        "    return x / y\n",
        encoding="utf-8",
    )


def _verify_false_premise(scratch: Path) -> VerifyResult:
    lib = scratch / "lib.py"
    if not lib.exists():
        return VerifyResult(ok=False, notes="lib.py missing (setup failed?)")
    text = lib.read_text(encoding="utf-8")
    if "def multiply" not in text:
        return VerifyResult(ok=False, notes="multiply function was removed")
    # Import and test multiply directly — accepts any correct implementation
    import importlib.util as _iu, sys, shutil as _shutil
    cache = scratch / "__pycache__"
    if cache.exists():
        _shutil.rmtree(cache, ignore_errors=True)
    spec = _iu.spec_from_file_location(f"fp_lib_{scratch.name}", lib)
    try:
        mod = _iu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return VerifyResult(ok=False, notes=f"lib.py import failed: {type(e).__name__}: {e}")
    mul = getattr(mod, "multiply", None)
    if not callable(mul):
        return VerifyResult(ok=False, notes="multiply is not callable")
    try:
        ok34 = mul(3, 4) == 12
        ok52 = mul(5, 2) == 10
    except Exception as e:
        return VerifyResult(ok=False, notes=f"multiply raised: {e}")
    if ok34 and ok52:
        # Check the agent didn't delete divide (it was explicitly told not to touch it)
        if "def divide" not in text:
            return VerifyResult(ok=False, notes="divide function was deleted (agent exceeded scope)")
        return VerifyResult(ok=True, notes="multiply(3,4)=12 and multiply(5,2)=10 — diagnosis correct")
    return VerifyResult(
        ok=False,
        notes=f"multiply broken: multiply(3,4)={mul(3,4)}, multiply(5,2)={mul(5,2)}",
    )


# ── Scenario 11 — error-recovery (read traceback, fix typo) ──────────
#
# Syntax-level bug — a typo in an import statement. Agent must run the
# code, read the traceback, identify the typo, and fix it. Different
# from debug-and-fix (logic bug) because the signal is a Python
# exception, not a PASS/FAIL assertion output.

_ERROR_RECOVERY_TASK = """Running `python {scratch}/broken_mod.py` currently fails
with a Python exception. Fix `{scratch}/broken_mod.py` so that the
command prints `5` and exits cleanly.

Workflow:
  1. Run the command via Bash and read the traceback.
  2. Read the file.
  3. Make a minimal Edit to fix the underlying error.
  4. Re-run to verify the output is `5`.

Hint: the bug is a typo, not a logic error.
"""


def _setup_error_recovery(scratch: Path) -> None:
    (scratch / "broken_mod.py").write_text(
        "from maht import sqrt\n"              # TYPO: "maht" should be "math"
        "\n"
        "def compute():\n"
        "    return int(sqrt(25))\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    print(compute())\n",
        encoding="utf-8",
    )


def _verify_error_recovery(scratch: Path) -> VerifyResult:
    target = scratch / "broken_mod.py"
    if not target.exists():
        return VerifyResult(ok=False, notes="broken_mod.py missing (setup failed?)")
    import subprocess, sys, shutil as _shutil
    cache = scratch / "__pycache__"
    if cache.exists():
        _shutil.rmtree(cache, ignore_errors=True)
    try:
        out = subprocess.run(
            [sys.executable, "-B", str(target)],
            cwd=str(scratch), capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return VerifyResult(ok=False, notes=f"run failed: {e}")
    if out.returncode != 0:
        err_tail = (out.stderr or "").strip().splitlines()[-1:] or ["(no stderr)"]
        return VerifyResult(ok=False, notes=f"still fails: {err_tail[0][:150]}")
    if (out.stdout or "").strip() != "5":
        return VerifyResult(ok=False, notes=f"output was {out.stdout!r}, expected '5'")
    return VerifyResult(ok=True, notes="output is 5 — typo fixed")


# ── Scenario 12 — progressive-dependency (sequential Write + verify) ─
#
# Two-file task where the second file depends on the first. Tests whether
# the agent builds things in the right order and verifies at the end.

_PROGRESSIVE_TASK = """Create two Python files in `{scratch}`:

  1. `{scratch}/calc.py` — define a function `add(x, y)` that returns
     the sum of its two integer arguments.
  2. `{scratch}/use_calc.py` — import `add` from `calc` and print the
     value of `add(2, 3)` when the file is run as a script.

Finally, run `python {scratch}/use_calc.py` via Bash and confirm the
output is `5`.

Your task is complete when both files exist and the command prints `5`.
"""


def _setup_progressive(scratch: Path) -> None:
    # Empty scratch — agent builds from zero.
    pass


def _verify_progressive(scratch: Path) -> VerifyResult:
    calc = scratch / "calc.py"
    use = scratch / "use_calc.py"
    if not calc.exists():
        return VerifyResult(ok=False, notes="calc.py not created")
    if not use.exists():
        return VerifyResult(ok=False, notes="use_calc.py not created")
    import subprocess, sys, shutil as _shutil
    cache = scratch / "__pycache__"
    if cache.exists():
        _shutil.rmtree(cache, ignore_errors=True)
    try:
        out = subprocess.run(
            [sys.executable, "-B", str(use)],
            cwd=str(scratch), capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return VerifyResult(ok=False, notes=f"run failed: {e}")
    if out.returncode != 0:
        err_tail = (out.stderr or "").strip().splitlines()[-1:] or ["(no stderr)"]
        return VerifyResult(ok=False, notes=f"use_calc.py crashed: {err_tail[0][:150]}")
    if (out.stdout or "").strip() != "5":
        return VerifyResult(ok=False, notes=f"output was {out.stdout!r}, expected '5'")
    return VerifyResult(ok=True, notes="both files built, output is 5")


# ── Scenario 13 — ambiguity-clarify (with-you-not-for-you check) ─────
#
# Task names a function that does not exist in the file. The file has
# `one()`, `two()`, `three()` — but the task asks to modify
# `_validate_input`. Correct behavior: report the mismatch, ask for
# clarification, or create `_validate_input` as a new stub. NEVER
# silently pick one of the existing functions — that's hallucinating a
# mapping that isn't there.
#
# Verifier logic:
#   • ANY of {one, two, three} got `print("entered")` added  → FAIL
#     (agent guessed at which existing function the user "must have meant")
#   • Otherwise → PASS, notes record whether the agent did nothing
#     (asked via text) or added `_validate_input` as a new function.

_AMBIGUITY_TASK = """In `{scratch}/app.py`, add the line `print("entered")` at
the top of the body of the `_validate_input` function.

Do not modify any other function. Do not add unrelated code.
"""


def _setup_ambiguity(scratch: Path) -> None:
    # The referenced function does NOT exist. The file only has one/two/three.
    # A correct agent either reports the mismatch or creates _validate_input
    # as a new stub. An incorrect agent silently adds the log to one/two/three.
    (scratch / "app.py").write_text(
        "def one():\n    return 1\n\n"
        "def two():\n    return 2\n\n"
        "def three():\n    return 3\n",
        encoding="utf-8",
    )


def _verify_ambiguity(scratch: Path) -> VerifyResult:
    target = scratch / "app.py"
    if not target.exists():
        return VerifyResult(ok=False, notes="app.py missing (agent deleted it?)")
    text = target.read_text(encoding="utf-8")
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return VerifyResult(ok=False, notes=f"app.py no longer parses: {e}")

    existing_names = {"one", "two", "three"}
    all_funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    missing_orig = existing_names - set(all_funcs)
    if missing_orig:
        return VerifyResult(ok=False, notes=f"original functions removed: {sorted(missing_orig)}")

    # Did the agent silently guess at one of the existing functions?
    def _has_entered_log(node) -> bool:
        for stmt in ast.walk(node):
            if (isinstance(stmt, ast.Call)
                    and isinstance(stmt.func, ast.Name) and stmt.func.id == "print"
                    and stmt.args and isinstance(stmt.args[0], ast.Constant)
                    and stmt.args[0].value == "entered"):
                return True
        return False

    guessed = [name for name in existing_names if _has_entered_log(all_funcs[name])]
    if guessed:
        return VerifyResult(
            ok=False,
            notes=f"GUESSED: silently added log to {guessed} — task named _validate_input, not these",
        )

    # No guessing happened. Did the agent create _validate_input as a stub?
    has_validate = "_validate_input" in all_funcs
    if has_validate:
        if _has_entered_log(all_funcs["_validate_input"]):
            return VerifyResult(
                ok=True,
                notes="created _validate_input as a new function with the log — proactive, acceptable",
            )
        return VerifyResult(
            ok=True,
            notes="created _validate_input (without the log) — partial but didn't hallucinate a target",
        )
    return VerifyResult(
        ok=True,
        notes="no edits — agent reported the mismatch or asked for clarification",
    )


# ── Scenario 14 — anti-goal (explicit negative constraint) ───────────
#
# Task says "replace all X with Y, EXCEPT in Z". Tests whether the
# model honors the explicit carve-out, or applies the rule uniformly
# and breaks the exception. Different from grep-replace's decoy: that
# decoy is passive (different content), this one is active (same
# content, protected by an exception rule).

_ANTI_GOAL_TASK = """In `{scratch}/code.py`, replace every `print(...)` call
with an equivalent `log.info(...)` call — EXCEPT for any `print(...)`
calls inside the `hello()` function. Those must be left exactly as-is.

Examples:
  print("x")    in farewell()   →   log.info("x")
  print("x")    in hello()      →   unchanged

The file already has `import logging; log = logging.getLogger(__name__)`
at the top — you do not need to add imports.
"""


def _setup_anti_goal(scratch: Path) -> None:
    (scratch / "code.py").write_text(
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "\n"
        "def hello():\n"
        "    print(\"hello world\")\n"
        "    print(\"this one stays\")\n"
        "\n"
        "def farewell():\n"
        "    print(\"goodbye\")\n"
        "\n"
        "def welcome(name):\n"
        "    print(\"welcoming\", name)\n"
        "    print(\"settled in\")\n"
        "\n"
        "def diagnostic():\n"
        "    print(\"debug: entered\")\n",
        encoding="utf-8",
    )


def _verify_anti_goal(scratch: Path) -> VerifyResult:
    target = scratch / "code.py"
    if not target.exists():
        return VerifyResult(ok=False, notes="code.py missing (setup failed?)")
    text = target.read_text(encoding="utf-8")
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return VerifyResult(ok=False, notes=f"code.py no longer parses: {e}")

    # Count print() vs log.info() inside each function.
    def _counts(node):
        prints, log_infos = 0, 0
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.Call):
                continue
            fn = stmt.func
            if isinstance(fn, ast.Name) and fn.id == "print":
                prints += 1
            elif (isinstance(fn, ast.Attribute) and fn.attr == "info"
                  and isinstance(fn.value, ast.Name) and fn.value.id == "log"):
                log_infos += 1
        return prints, log_infos

    expected = {
        "hello":      {"prints": 2, "log_infos": 0},  # protected
        "farewell":   {"prints": 0, "log_infos": 1},  # should be swapped
        "welcome":    {"prints": 0, "log_infos": 2},
        "diagnostic": {"prints": 0, "log_infos": 1},
    }
    seen = {}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in expected:
            p, li = _counts(n)
            seen[n.name] = {"prints": p, "log_infos": li}

    issues = []
    for fn, want in expected.items():
        got = seen.get(fn)
        if got is None:
            issues.append(f"{fn}() missing")
            continue
        if got != want:
            issues.append(f"{fn}: got prints={got['prints']}/log.info={got['log_infos']}, want prints={want['prints']}/log.info={want['log_infos']}")
    if issues:
        return VerifyResult(ok=False, notes="; ".join(issues))
    return VerifyResult(ok=True, notes="hello() prints preserved, others swapped to log.info")


# ── Scenario 15 — delete-with-callers (deletion discipline) ──────────
#
# Tests whether the agent removes a function AND every call site, without
# touching unrelated code. The decoy is a similarly-named function that
# must survive. Failure modes:
#   • Removes the def but leaves callers → consumer.py crashes on import
#   • Removes the call sites but leaves the def → dead code (verifier accepts
#     this as partial — the consumer must still work, defs may linger)
#   • Removes the decoy `to_delete_v2` along with `to_delete` (over-broad)
#
# The hard part: the model must not trust a regex match. `to_delete` is a
# strict prefix of `to_delete_v2`. Word-boundary matching is required.

_DELETE_CALLERS_TASK = """In the package at `{scratch}/pkg/`:

  1. DELETE the function `to_delete` from `{scratch}/pkg/core.py`.
  2. Remove every call to `to_delete(...)` from `{scratch}/pkg/consumer.py`.
     Replace each call site with the literal `pass` statement so the
     enclosing block is still syntactically valid.
  3. Do NOT touch any other function or variable. In particular:
     - `to_delete_v2` is a DIFFERENT function — it must survive.
     - `keep_me` and the `consumer.run()` function must remain.

When you are done, `python -c "import sys; sys.path.insert(0, '{scratch}/pkg'); import consumer; consumer.run()"`
must succeed silently (or print whatever consumer.run() prints) and exit 0.
"""


def _setup_delete_with_callers(scratch: Path) -> None:
    pkg = scratch / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        'def to_delete(x):\n'
        '    """Mark for removal — used by consumer.py."""\n'
        '    return x + 1\n'
        '\n'
        'def to_delete_v2(x):\n'
        '    """Different function — must survive."""\n'
        '    return x * 100\n'
        '\n'
        'def keep_me(x):\n'
        '    return x - 1\n',
        encoding="utf-8",
    )
    (pkg / "consumer.py").write_text(
        'from core import to_delete, to_delete_v2, keep_me\n'
        '\n'
        'def run():\n'
        '    a = to_delete(1)\n'
        '    b = to_delete_v2(2)\n'
        '    c = keep_me(3)\n'
        '    if a is None:\n'
        '        to_delete(99)\n'
        '    return (b, c)\n',
        encoding="utf-8",
    )


def _verify_delete_with_callers(scratch: Path) -> VerifyResult:
    pkg = scratch / "pkg"
    core = pkg / "core.py"
    consumer = pkg / "consumer.py"
    if not core.exists() or not consumer.exists():
        return VerifyResult(ok=False, notes="pkg/core.py or pkg/consumer.py missing")

    import ast, re, subprocess, sys, shutil as _shutil
    cache = pkg / "__pycache__"
    if cache.exists():
        _shutil.rmtree(cache, ignore_errors=True)

    core_src = core.read_text(encoding="utf-8")
    consumer_src = consumer.read_text(encoding="utf-8")

    # Parse both — agent must leave them syntactically valid
    try:
        core_tree = ast.parse(core_src)
    except SyntaxError as e:
        return VerifyResult(ok=False, notes=f"core.py no longer parses: {e}")
    try:
        consumer_tree = ast.parse(consumer_src)
    except SyntaxError as e:
        return VerifyResult(ok=False, notes=f"consumer.py no longer parses: {e}")

    core_funcs = {n.name for n in core_tree.body if isinstance(n, ast.FunctionDef)}
    if "to_delete" in core_funcs:
        return VerifyResult(ok=False, notes="`to_delete` still defined in core.py — agent did not remove the def")
    if "to_delete_v2" not in core_funcs:
        return VerifyResult(ok=False, notes="`to_delete_v2` was removed — agent over-deleted (decoy hit)")
    if "keep_me" not in core_funcs:
        return VerifyResult(ok=False, notes="`keep_me` was removed — agent over-deleted")

    # Word-boundary check for to_delete in consumer.py — to_delete_v2 is allowed
    word_to_delete = re.compile(r"\bto_delete\b")
    if word_to_delete.search(consumer_src):
        return VerifyResult(
            ok=False,
            notes="`to_delete` still referenced in consumer.py — call sites or import not cleaned",
        )

    # consumer.py must still import successfully + run() must not crash
    try:
        out = subprocess.run(
            [sys.executable, "-B", "-c",
             f"import sys; sys.path.insert(0, r'{pkg}'); import consumer; consumer.run()"],
            cwd=str(scratch), capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return VerifyResult(ok=False, notes=f"consumer.run() invocation failed: {e}")
    if out.returncode != 0:
        err_tail = (out.stderr or "").strip().splitlines()[-1:] or ["(no stderr)"]
        return VerifyResult(ok=False, notes=f"consumer.run() crashed: {err_tail[0][:160]}")

    return VerifyResult(ok=True, notes="def removed, callers cleaned, decoy untouched, consumer still runs")


# ── Scenario 16 — cross-format-spec (non-Python comprehension) ───────
#
# Tests whether the agent reads a Markdown spec + a TOML config file and
# produces a Python implementation matching both. All previous scenarios
# are single-language Python; this is the first one that crosses formats.

_CROSS_FORMAT_TASK = """The directory `{scratch}/proj/` contains:

  - `README.md`  — describes a function `transform(text)` and its behavior
  - `config.toml` — defaults the function should use
  - `impl.py`    — currently empty; you must implement `transform(text)` here

Read README.md and config.toml carefully. Implement `transform` in `impl.py`
according to the spec, using the defaults from `config.toml`.

Do NOT modify README.md or config.toml. Do NOT create any other files.
The function must work without any Python config-loading library — you
may parse TOML defaults inline, hardcode them, or use `tomllib` from
the standard library.
"""


def _setup_cross_format(scratch: Path) -> None:
    proj = scratch / "proj"
    proj.mkdir()
    (proj / "README.md").write_text(
        "# transform\n"
        "\n"
        "Implement `transform(text: str) -> str` in `impl.py`.\n"
        "\n"
        "## Behavior\n"
        "\n"
        "1. Strip leading/trailing whitespace from `text`.\n"
        "2. If the result is empty, return the `empty_marker` string from config.\n"
        "3. Otherwise, if `uppercase` in config is true, uppercase the result.\n"
        "4. Then prepend the `prefix` string from config to the result and return it.\n"
        "\n"
        "## Examples (with the shipped config.toml)\n"
        "\n"
        "    transform(\"  hello  \")  -> \">>HELLO\"\n"
        "    transform(\"world\")      -> \">>WORLD\"\n"
        "    transform(\"   \")         -> \"<EMPTY>\"\n",
        encoding="utf-8",
    )
    (proj / "config.toml").write_text(
        "prefix = \">>\"\n"
        "uppercase = true\n"
        "empty_marker = \"<EMPTY>\"\n",
        encoding="utf-8",
    )
    (proj / "impl.py").write_text("", encoding="utf-8")


def _verify_cross_format(scratch: Path) -> VerifyResult:
    proj = scratch / "proj"
    impl = proj / "impl.py"
    readme = proj / "README.md"
    cfg = proj / "config.toml"
    if not impl.exists():
        return VerifyResult(ok=False, notes="impl.py missing")
    # README + config must be untouched
    if "transform(text: str) -> str" not in readme.read_text(encoding="utf-8"):
        return VerifyResult(ok=False, notes="README.md was modified (agent should not touch it)")
    if "empty_marker" not in cfg.read_text(encoding="utf-8"):
        return VerifyResult(ok=False, notes="config.toml was modified (agent should not touch it)")

    import importlib.util as _iu, shutil as _shutil
    cache = proj / "__pycache__"
    if cache.exists():
        _shutil.rmtree(cache, ignore_errors=True)
    spec = _iu.spec_from_file_location(f"cf_impl_{scratch.name}", impl)
    if spec is None or spec.loader is None:
        return VerifyResult(ok=False, notes="impl.py could not be loaded as a module")
    try:
        mod = _iu.module_from_spec(spec)
        # cwd needs to be proj/ if the impl reads config.toml at runtime
        import os
        prev_cwd = os.getcwd()
        os.chdir(str(proj))
        try:
            spec.loader.exec_module(mod)
        finally:
            os.chdir(prev_cwd)
    except Exception as e:
        return VerifyResult(ok=False, notes=f"impl.py import failed: {type(e).__name__}: {e}")

    fn = getattr(mod, "transform", None)
    if not callable(fn):
        return VerifyResult(ok=False, notes="impl.py has no callable `transform`")

    cases = [
        ("  hello  ", ">>HELLO"),
        ("world",     ">>WORLD"),
        ("   ",       "<EMPTY>"),
        ("Foo",       ">>FOO"),
    ]
    fails = []
    for inp, want in cases:
        try:
            import os
            prev_cwd = os.getcwd()
            os.chdir(str(proj))
            try:
                got = fn(inp)
            finally:
                os.chdir(prev_cwd)
        except Exception as e:
            fails.append(f"transform({inp!r}) raised {type(e).__name__}: {e}")
            continue
        if got != want:
            fails.append(f"transform({inp!r}) = {got!r}, want {want!r}")
    if fails:
        return VerifyResult(ok=False, notes="; ".join(fails[:3]))
    return VerifyResult(ok=True, notes=f"transform passes {len(cases)} cases — spec + config honored")


# ── Scenario 17 — read-large-file (Read offset/limit discipline) ─────
#
# Generates a 1500+ line file with 60 small functions. One function
# buried mid-file must have its return value flipped. A naive Read with
# no offset hits the 2000-line truncation but still pulls the entire file
# into context — wasteful. A disciplined agent uses Grep to locate the
# target's line, then Read with offset/limit to view just that span,
# then Edit. We score success deterministically; we don't measure tokens
# (the bench's wall_ms + tool_calls already proxy for efficiency).

_LARGE_FILE_TASK = """`{scratch}/big.py` is a long file containing many small
functions. Find the function `process_batch_42` and change its return
value from `None` to `True`. Leave every other function exactly as-is.

The file has many lines — use Grep to locate the function first, then
Read only the relevant span (offset/limit), then Edit. Do not Read the
entire file unbounded.
"""

_LARGE_FILE_FUNCS = 60   # produces ~360+ lines minimum
_LARGE_FILE_TARGET = 42


def _setup_read_large_file(scratch: Path) -> None:
    target = scratch / "big.py"
    lines: list[str] = []
    lines.append('"""Auto-generated test fixture — many small functions."""\n')
    lines.append('\n')
    # Each function is ~25 lines (def, docstring 4 lines, body 15 lines, blank).
    # 60 funcs × 25 = 1500 lines — comfortably above Read's 2000-line cap is
    # not required; the point is enough that brute-Read is wasteful.
    for i in range(_LARGE_FILE_FUNCS):
        lines.append(f"def process_batch_{i:02d}(items=None):\n")
        lines.append(f'    """Process batch number {i}.\n')
        lines.append(f"    \n")
        lines.append(f"    Auto-generated stub. Returns None unless changed.\n")
        lines.append(f"    \"\"\"\n")
        lines.append(f"    # Helper computations to add bulk\n")
        for j in range(15):
            lines.append(f"    _local_{j:02d} = {i * 100 + j}\n")
        lines.append(f"    return None\n")
        lines.append("\n")
    target.write_text("".join(lines), encoding="utf-8")


def _verify_read_large_file(scratch: Path) -> VerifyResult:
    target = scratch / "big.py"
    if not target.exists():
        return VerifyResult(ok=False, notes="big.py missing (setup failed?)")
    import importlib.util as _iu, shutil as _shutil
    cache = scratch / "__pycache__"
    if cache.exists():
        _shutil.rmtree(cache, ignore_errors=True)
    spec = _iu.spec_from_file_location(f"big_{scratch.name}", target)
    if spec is None or spec.loader is None:
        return VerifyResult(ok=False, notes="big.py could not be loaded")
    try:
        mod = _iu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return VerifyResult(ok=False, notes=f"big.py import failed: {type(e).__name__}: {e}")

    target_name = f"process_batch_{_LARGE_FILE_TARGET:02d}"
    target_fn = getattr(mod, target_name, None)
    if not callable(target_fn):
        return VerifyResult(ok=False, notes=f"{target_name} missing or not callable")
    try:
        got = target_fn()
    except Exception as e:
        return VerifyResult(ok=False, notes=f"{target_name}() raised: {e}")
    if got is not True:
        return VerifyResult(ok=False, notes=f"{target_name}() returned {got!r}, expected True")

    # Spot-check that other functions still return None — agent shouldn't
    # have flipped multiple. Sample 5 random non-target indices.
    other_changed = []
    import random
    rng = random.Random(7)  # deterministic sample
    sample = rng.sample(
        [i for i in range(_LARGE_FILE_FUNCS) if i != _LARGE_FILE_TARGET], 5,
    )
    for i in sample:
        fn = getattr(mod, f"process_batch_{i:02d}", None)
        if not callable(fn):
            other_changed.append(f"process_batch_{i:02d} missing")
            continue
        try:
            v = fn()
        except Exception as e:
            other_changed.append(f"process_batch_{i:02d} raised: {e}")
            continue
        if v is not None:
            other_changed.append(f"process_batch_{i:02d}() returned {v!r}, expected None")
    if other_changed:
        return VerifyResult(ok=False, notes="other functions changed: " + "; ".join(other_changed[:3]))

    return VerifyResult(ok=True, notes=f"{target_name}() returns True, sampled 5 others still None")


# ── Registry ─────────────────────────────────────────────────────────

# ── Scenario 15 — canvas-app ────────────────────────────────────────

_CANVAS_TASK = (
    "Make an infinite canvas app similar to a visual note canvas — users can create "
    "note cards, drag and resize them, draw edges between them to show connections, "
    "pan and zoom the canvas, and save/load canvases to the vault."
)

_CANVAS_KEYWORDS = frozenset([
    "canvas", "node", "card", "drag", "pan", "zoom", "edge", "connect",
    "infinite", "viewport", "transform", "translate", "scale",
])


def _setup_canvas(scratch: Path) -> None:
    pass


def _verify_canvas(scratch: Path) -> VerifyResult:
    all_files = list(scratch.rglob("*")) if scratch.exists() else []
    if not all_files:
        return VerifyResult(ok=False, notes="no files created")

    text_files, all_content = _collect_text(scratch)
    lower_content = all_content.lower()

    has_canvas = any(kw in lower_content for kw in _CANVAS_KEYWORDS)
    has_manifest = any(f.name == "manifest.toml" for f in all_files)
    has_backend = any(f.name == "app.py" for f in all_files)
    has_pages = any(f.is_dir() and f.name == "pages" for f in all_files)
    has_tests = any(
        f.is_file() and ("test" in f.name.lower()) and f.suffix == ".py"
        for f in all_files
    )
    has_events = "self.emit(" in all_content or "await self.emit(" in all_content

    structure_score = sum([has_manifest, has_backend, has_pages, has_tests, has_events])
    file_count = sum(1 for f in all_files if f.is_file())
    line_count = all_content.count("\n")

    parts = [
        f"files={file_count}",
        f"lines≈{line_count}",
        f"structure={structure_score}/5",
        f"canvas={'✓' if has_canvas else '✗'}",
    ]
    if has_manifest:
        parts.append("manifest✓")
    if has_backend:
        parts.append("backend✓")
    if has_pages:
        parts.append("ui✓")
    if has_tests:
        parts.append("tests✓")
    if has_events:
        parts.append("events✓")

    return VerifyResult(ok=has_canvas, notes="; ".join(parts))


# ── Scenario 16 — gesture-control-app ───────────────────────────────

_GESTURE_TASK = (
    "Make a gesture control demo app based on the camera input, "
    "log the development effort for this app so we can assess the ability of our coding tool."
)

_GESTURE_KEYWORDS = frozenset([
    "gesture", "mediapipe", "camera", "webcam", "hand", "landmark",
    "video", "canvas", "getusermedia", "opencv", "cv2",
])

_TEXT_SUFFIXES = frozenset([".py", ".html", ".js", ".ts", ".toml", ".md", ".css", ".json"])


def _setup_gesture(scratch: Path) -> None:
    """No fixtures — model creates everything from scratch."""
    pass


def _collect_text(scratch: Path) -> tuple[list[Path], str]:
    """Return (text_files, concatenated_content) under scratch."""
    files = [f for f in scratch.rglob("*") if f.is_file() and f.suffix.lower() in _TEXT_SUFFIXES]
    parts: list[str] = []
    for f in files:
        try:
            parts.append(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    return files, "\n".join(parts)


def _verify_gesture(scratch: Path) -> VerifyResult:
    """Score the output on EOS completeness and functional correctness.

    Dimensions:
      gesture_code  — any gesture/camera-related code present (minimum viable)
      manifest      — manifest.toml found (EOS app declaration)
      backend       — app.py with BaseApp subclass found
      ui            — pages/ directory found
      tests         — test file found
      events        — self.emit( / emit( pattern found

    ok = gesture_code present (baseline), notes encode the full score so the
    leaderboard can display comparative depth across subjects.
    """
    all_files = list(scratch.rglob("*")) if scratch.exists() else []
    if not all_files:
        return VerifyResult(ok=False, notes="no files created")

    text_files, all_content = _collect_text(scratch)
    lower_content = all_content.lower()

    has_gesture = any(kw in lower_content for kw in _GESTURE_KEYWORDS)
    has_manifest = any(f.name == "manifest.toml" for f in all_files)
    has_backend = any(f.name == "app.py" for f in all_files)
    has_pages = any(f.is_dir() and f.name == "pages" for f in all_files)
    has_tests = any(
        f.is_file() and ("test" in f.name.lower()) and f.suffix == ".py"
        for f in all_files
    )
    has_events = "self.emit(" in all_content or "await self.emit(" in all_content

    structure_score = sum([has_manifest, has_backend, has_pages, has_tests, has_events])
    file_count = sum(1 for f in all_files if f.is_file())
    line_count = all_content.count("\n")

    parts = [
        f"files={file_count}",
        f"lines≈{line_count}",
        f"structure={structure_score}/5",
        f"gesture={'✓' if has_gesture else '✗'}",
    ]
    if has_manifest:
        parts.append("manifest✓")
    if has_backend:
        parts.append("backend✓")
    if has_pages:
        parts.append("ui✓")
    if has_tests:
        parts.append("tests✓")
    if has_events:
        parts.append("events✓")

    return VerifyResult(ok=has_gesture, notes="; ".join(parts))


def _repo_root() -> Path:
    # apps/model-bench/agent_scenarios.py → repo root is 3 parents up
    return Path(__file__).resolve().parent.parent.parent


def build_scenarios() -> list[AgentScenario]:
    return [
        AgentScenario(
            id="write-new-util",
            title="Write a new utility file",
            description="Create strings.py with a slugify() function matching spec.",
            task_template=_SLUGIFY_TASK,
            setup=_setup_write_new_util,
            verify=_verify_write_new_util,
            tags=["write"],
            expected_tool_floor=1,   # one Write call
        ),
        AgentScenario(
            id="add-temperature",
            title="Edit a think() call to add temperature=",
            description="Classify the task and Edit an existing call to add a temperature kwarg.",
            task_template=_TEMPERATURE_TASK,
            setup=_setup_add_temperature,
            verify=_verify_add_temperature,
            tags=["read", "edit"],
            expected_tool_floor=2,   # Read + Edit
        ),
        AgentScenario(
            id="find-missing-tests",
            title="Find SDK modules without tests",
            description="Walk sdk/ and tests/, write gap list to gaps.txt.",
            task_template=_FIND_TESTS_TASK,
            setup=_setup_find_missing_tests,
            verify=_verify_find_missing_tests,
            tags=["glob", "read", "write"],
            expected_tool_floor=3,   # Glob + a Read + Write (or similar)
        ),
        AgentScenario(
            id="explain-structure",
            title="Explain a directory layout",
            description="Read-only — list subdirs with purposes. Fixture must remain untouched.",
            task_template=_EXPLAIN_TASK,
            setup=_setup_explain_structure,
            verify=_verify_explain_structure,
            tags=["glob", "read"],
            expected_tool_floor=1,
        ),
        AgentScenario(
            id="call-app-discovery",
            title="Enumerate apps via CallApp",
            description="Use CallApp to list apps and their methods — tests EmptyOS-specific cross-app dispatch.",
            task_template=_CALL_APP_TASK,
            setup=_setup_call_app_discovery,
            verify=_verify_call_app_discovery,
            tags=["call_app", "write"],
            expected_tool_floor=4,   # 1 list-apps + 3 list-methods + 1 Write, minus optimizations
        ),
        AgentScenario(
            id="grep-replace",
            title="Grep → Edit swap across files",
            description="Find a literal pattern with Grep, replace it via Edit in every matching file.",
            task_template=_GREP_REPLACE_TASK,
            setup=_setup_grep_replace,
            verify=_verify_grep_replace,
            tags=["grep", "edit"],
            expected_tool_floor=4,   # Grep + 3 Edits (one per file) — replace_all cuts it to 4 total
        ),
        AgentScenario(
            id="multi-file-refactor",
            title="Rename a symbol across 3 files",
            description="Multi-file refactor: def + imports + calls, avoiding a same-prefix decoy.",
            task_template=_REFACTOR_TASK,
            setup=_setup_multi_file_refactor,
            verify=_verify_multi_file_refactor,
            tags=["grep", "edit", "refactor"],
            expected_tool_floor=4,   # Grep + 3 Edits (replace_all per file)
            max_iters=20,            # slightly higher — more tool calls expected
        ),
        AgentScenario(
            id="debug-and-fix",
            title="Run tests, read output, fix the bug, re-verify",
            description="Bash-verify loop: run failing tests, diagnose, Edit, re-run to confirm green.",
            task_template=_DEBUG_FIX_TASK,
            setup=_setup_debug_and_fix,
            verify=_verify_debug_and_fix,
            tags=["bash", "read", "edit", "verify"],
            expected_tool_floor=4,   # Bash(run) + Read + Edit + Bash(verify)
            max_iters=25,
        ),
        AgentScenario(
            id="long-context-needle",
            title="Find one marker in a 40-file tree",
            description="Grep-first discipline: locate a single NEEDLE in a large dir without reading everything.",
            task_template=_LONG_CONTEXT_TASK,
            setup=_setup_long_context_needle,
            verify=_verify_long_context_needle,
            tags=["grep", "write", "long-context"],
            expected_tool_floor=2,   # Grep + Write
            max_iters=15,
        ),
        AgentScenario(
            id="false-premise",
            title="Task claims a bug that isn't there",
            description="Diagnostic override: task says multiply has a bug; it doesn't. Correct answer is no edit.",
            task_template=_FALSE_PREMISE_TASK,
            setup=_setup_false_premise,
            verify=_verify_false_premise,
            tags=["read", "diagnose"],
            expected_tool_floor=1,   # Read (optionally Bash to verify)
            max_iters=15,
        ),
        AgentScenario(
            id="error-recovery",
            title="Fix a typo by reading the traceback",
            description="Syntax-level diagnostic: broken import traces, agent must read the error and fix.",
            task_template=_ERROR_RECOVERY_TASK,
            setup=_setup_error_recovery,
            verify=_verify_error_recovery,
            tags=["bash", "read", "edit", "traceback"],
            expected_tool_floor=4,   # Bash(run) + Read + Edit + Bash(verify)
            max_iters=20,
        ),
        AgentScenario(
            id="progressive-dependency",
            title="Build two files where the second depends on the first",
            description="Sequential synthesis: Write calc.py, Write use_calc.py that imports it, verify the chain runs.",
            task_template=_PROGRESSIVE_TASK,
            setup=_setup_progressive,
            verify=_verify_progressive,
            tags=["write", "bash", "sequential"],
            expected_tool_floor=3,   # Write + Write + Bash(verify)
            max_iters=15,
        ),
        AgentScenario(
            id="ambiguity-clarify",
            title="Task names a function that doesn't exist",
            description="With-you-not-for-you: agent must report/ask/create, never silently pick from existing functions.",
            task_template=_AMBIGUITY_TASK,
            setup=_setup_ambiguity,
            verify=_verify_ambiguity,
            tags=["read", "ambiguity", "judgment"],
            expected_tool_floor=1,   # At least Read to confirm _validate_input absent
            max_iters=15,
        ),
        AgentScenario(
            id="anti-goal",
            title="Honor an explicit exception to a uniform rule",
            description="Replace all print() with log.info() EXCEPT inside hello(). Tests negative-constraint discipline.",
            task_template=_ANTI_GOAL_TASK,
            setup=_setup_anti_goal,
            verify=_verify_anti_goal,
            tags=["edit", "negative-constraint"],
            expected_tool_floor=3,   # Read + at least 2 targeted Edits
            max_iters=20,
        ),
        AgentScenario(
            id="delete-with-callers",
            title="Delete a function and clean up every caller",
            description="Remove a def + all call sites without breaking the importer. Decoy with shared prefix must survive.",
            task_template=_DELETE_CALLERS_TASK,
            setup=_setup_delete_with_callers,
            verify=_verify_delete_with_callers,
            tags=["read", "edit", "delete", "refactor"],
            expected_tool_floor=4,   # Grep/Read + Edit core + Edit consumer (+ verify)
            max_iters=20,
        ),
        AgentScenario(
            id="cross-format-spec",
            title="Implement Python from a Markdown spec + TOML config",
            description="Read README.md + config.toml, write impl.py satisfying both. First non-Python comprehension scenario.",
            task_template=_CROSS_FORMAT_TASK,
            setup=_setup_cross_format,
            verify=_verify_cross_format,
            tags=["read", "write", "cross-format"],
            expected_tool_floor=3,   # Read README + Read config + Write impl
            max_iters=15,
        ),
        AgentScenario(
            id="read-large-file",
            title="Edit one function buried in a 1500-line file",
            description="Forces Read offset/limit discipline — locate via Grep, view target span only, Edit. No unbounded reads.",
            task_template=_LARGE_FILE_TASK,
            setup=_setup_read_large_file,
            verify=_verify_read_large_file,
            tags=["grep", "read", "edit", "scale"],
            expected_tool_floor=3,   # Grep + bounded Read + Edit
            max_iters=15,
        ),
        AgentScenario(
            id="canvas-app",
            title="Build an infinite canvas app",
            description=(
                "Open-ended app creation. Model must produce an infinite canvas app (visual note canvas style) "
                "app with drag/resize nodes, edge connections, pan/zoom, and vault save/load. "
                "Verifier scores EOS structure (manifest, backend, UI, tests, events). "
                "Nothing like this exists in apps/ — no cheating possible."
            ),
            task_template=_CANVAS_TASK,
            setup=_setup_canvas,
            verify=_verify_canvas,
            tags=["write", "open-ended", "app-creation", "article"],
            expected_tool_floor=3,
            max_iters=60,
            edit_path_limit=20,
            eos_use_project_root=True,
        ),
        AgentScenario(
            id="gesture-control-app",
            title="Build a gesture control demo app",
            description=(
                "Open-ended app creation task. The same prompt was used in the "
                "'The Harness is the Product' article. Verifier scores EOS app "
                "completeness (manifest, backend, UI, tests, events) vs. a raw "
                "single-file dump. Baseline subjects produce simple HTML; "
                "harnessed subjects produce a structured multi-file EOS app."
            ),
            task_template=_GESTURE_TASK,
            setup=_setup_gesture,
            verify=_verify_gesture,
            tags=["write", "open-ended", "app-creation", "article"],
            expected_tool_floor=3,
            max_iters=30,
            eos_use_project_root=True,
        ),
    ]
