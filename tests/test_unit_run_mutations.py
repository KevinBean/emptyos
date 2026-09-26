"""The mutation runner restores its target byte for byte, whatever the line endings.

`.claude/skills/eos-mutation-verify/run_mutations.py` read and wrote the target
as text. On Windows that turned every restored LF file into CRLF, so git showed
it modified and a hash taken before the run no longer matched — and the runner's
own restore check compared two strings that had been through the same newline
translation, so it reported success. A tool whose job is temporarily breaking
source files must hand back exactly the bytes it took.

Daemon-free. Each case runs the real runner against a throwaway module and test
in a temporary directory, with a multi-line anchor so a CRLF target also proves the anchor
translation (an untranslated anchor is a NO-OP, which fails `rc == 0`).
"""

from __future__ import annotations

import importlib.util
import pathlib
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / ".claude" / "skills" / "eos-mutation-verify" / "run_mutations.py"

pytestmark = pytest.mark.skipif(not RUNNER.exists(), reason="mutation runner not present")

_TEST_BODY = (
    "import importlib.util\n"
    "import pathlib\n"
    "\n"
    "\n"
    "def test_value_is_one():\n"
    "    p = pathlib.Path(__file__).with_name('target_mod.py')\n"
    "    spec = importlib.util.spec_from_file_location('target_mod', p)\n"
    "    m = importlib.util.module_from_spec(spec)\n"
    "    spec.loader.exec_module(m)\n"
    "    assert m.value() == 1\n"
)


def _runner():
    spec = importlib.util.spec_from_file_location("run_mutations_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_the_target_is_restored_byte_for_byte(newline):
    # A plain temporary directory, not the tmp_path fixture: this test runs the
    # runner, and the runner cannot pass --basetemp to the pytest it launches.
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "target_mod.py"
        target.write_bytes(newline.join(["def value():", "    return 1", ""]).encode("utf-8"))
        tests = pathlib.Path(tmp) / "test_target_mod.py"
        tests.write_bytes(_TEST_BODY.encode("utf-8"))
        before = target.read_bytes()

        rc = _runner().verify(
            str(target), str(tests),
            [("returns two", "def value():\n    return 1", "def value():\n    return 2",
              "value_is_one")],
        )

        assert rc == 0, "the mutation must apply and be caught"
        assert target.read_bytes() == before


def test_an_anchor_matching_twice_is_ambiguous_not_a_survivor(capsys):
    # `return 1` appears in two functions and the test only calls the second;
    # mutating the first match would read as SURVIVED for a line never touched.
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "target_mod.py"
        target.write_bytes(b"def other():\n    return 1\n\n\ndef value():\n    return 1\n")
        tests = pathlib.Path(tmp) / "test_target_mod.py"
        tests.write_bytes(_TEST_BODY.encode("utf-8"))
        before = target.read_bytes()

        rc = _runner().verify(str(target), str(tests),
                              [("returns two", "    return 1", "    return 2", "value_is_one")])
        out = capsys.readouterr().out
        assert "AMBIGUOUS" in out and "SURVIVED" not in out, out
        assert rc == 1 and target.read_bytes() == before

        # A CRLF target with a multi-line anchor matching three times: the count
        # must be taken after the anchor is translated to CRLF, or it reads 0
        # and the first match is mutated anyway.
        target.write_bytes(b"def a():\r\n    return 1\r\n\r\n\r\ndef b():\r\n    return 1\r\n\r\n\r\n"
                           b"def value():\r\n    return 1\r\n")
        crlf_before = target.read_bytes()
        rc = _runner().verify(str(target), str(tests),
                              [("returns two", "():\n    return 1", "():\n    return 2", "value_is_one")])
        out = capsys.readouterr().out
        assert "AMBIGUOUS" in out and "matches 3 places" in out, out
        assert rc == 1 and target.read_bytes() == crlf_before
        target.write_bytes(before)

        # Widened to the one occurrence, the same mutation is caught.
        rc = _runner().verify(
            str(target), str(tests),
            [("returns two", "def value():\n    return 1", "def value():\n    return 2",
              "value_is_one")])
        assert rc == 0 and target.read_bytes() == before


def test_overlapping_anchor_matches_are_counted():
    # str.count skips overlaps: 'a=1\na=1' occurs twice in three repeated lines.
    occ = _runner()._occurrences
    assert occ("a=1\na=1\na=1", "a=1\na=1") == 2
    assert occ("x = 1\n", "x = 1") == 1
    assert occ("nothing here", "x") == 0
