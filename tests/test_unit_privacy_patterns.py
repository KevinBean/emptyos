"""Pattern-coverage tests for .eos-personal + outbound_scan.

Two failure modes the rest of the privacy stack can't catch on its own:

  1. Someone edits a regex in `.eos-personal` and accidentally breaks
     what it matches (drops a `\\s+`, changes `\\b` placement, etc.).
     check-personal.py keeps passing because the broken regex still
     parses — it just no longer hits anything in tracked files.

  2. outbound_scan loses its `.eos-personal` integration (e.g. a refactor
     removes `_personal_patterns()` from the scan path). Cloud calls would
     silently lose personal-data detection.

The example strings are the personal data itself, so they live in
`tests/personal/privacy_examples.py`, which no public snapshot carries; neither
does `.eos-personal`. In a public clone the personal cases skip and only the
secret-pattern check runs. Until 2026-09-24 the examples were literals in this
file, which shipped them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from emptyos.capabilities.outbound_scan import scan_outbound
from emptyos.sdk.personal_patterns import load


REPO_ROOT = Path(__file__).resolve().parent.parent
PATTERNS_FILE = REPO_ROOT / ".eos-personal"
EXAMPLES_FILE = REPO_ROOT / "tests" / "personal" / "privacy_examples.py"


def _load_examples():
    if not (PATTERNS_FILE.exists() and EXAMPLES_FILE.exists()):
        return None
    spec = importlib.util.spec_from_file_location("privacy_examples", EXAMPLES_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EX = _load_examples()
needs_private = pytest.mark.skipif(
    EX is None,
    reason=".eos-personal / tests/personal/ are private and absent from public clones",
)


def _patterns():
    patterns = load(PATTERNS_FILE)
    # A lower bound catches "someone deleted half the file".
    assert len(patterns) >= EX.MIN_PATTERNS, (
        f"Expected at least {EX.MIN_PATTERNS} patterns in .eos-personal, got {len(patterns)}"
    )
    return patterns


@pytest.mark.api
@needs_private
class TestPatternCoverage:
    """Each high-confidence pattern matches its representative example."""

    @pytest.mark.parametrize(
        "text", [t for _, t in EX.MATCHES] if EX else [],
        ids=[label for label, _ in EX.MATCHES] if EX else [],
    )
    def test_example_matches(self, text):
        assert any(p.search(text) for p in _patterns()), f"No pattern matched: {text!r}"

    @pytest.mark.parametrize(
        "text", [t for _, t in EX.NON_MATCHES] if EX else [],
        ids=[label for label, _ in EX.NON_MATCHES] if EX else [],
    )
    def test_control_does_not_match(self, text):
        assert not any(p.search(text) for p in _patterns()), f"False positive: {text!r}"

    def test_generic_strings_do_not_match(self):
        for s in ("Hello, world", "import re", "Path: /opt/emptyos/data"):
            assert not any(p.search(s) for p in _patterns()), f"False positive: {s!r}"


@pytest.mark.api
class TestOutboundScanIntegration:
    """outbound_scan still wires .eos-personal patterns into its findings."""

    @needs_private
    def test_finds_personal_via_outbound_scan(self):
        """Mixed string: personal + non-personal. Personal pattern surfaces."""
        names = [f.pattern_name for f in scan_outbound(EX.OUTBOUND_PERSONAL)]
        assert any("Personal data" in n for n in names), (
            f"Expected at least one 'Personal data' finding, got: {names}"
        )

    def test_finds_secret_via_outbound_scan(self):
        """Secret patterns still work (regression guard for the wider scanner)."""
        # check-secrets: ignore — synthetic fixture; asserting the pattern matches
        text = "Authorization: Bearer sk-proj-abcdefghijklmnopqrstuvwxyz1234"
        names = [f.pattern_name for f in scan_outbound(text)]
        assert any("OpenAI" in n or "Bearer" in n for n in names), (
            f"Expected secret-pattern finding, got: {names}"
        )

    @needs_private
    def test_clean_text_yields_no_findings(self):
        """A city name alone is deliberately not a pattern."""
        findings = scan_outbound(EX.OUTBOUND_CLEAN)
        assert findings == [], f"Expected zero findings, got: {findings}"
