"""Pattern-coverage tests for .eos-personal + outbound_scan.

Two failure modes the rest of the privacy stack can't catch on its own:

  1. Someone edits a regex in `.eos-personal` and accidentally breaks
     what it matches (drops a `\\s+`, changes `\\b` placement, etc.).
     check-personal.py keeps passing because the broken regex still
     parses — it just no longer hits anything in tracked files.

  2. outbound_scan loses its `.eos-personal` integration (e.g. a refactor
     removes `_personal_patterns()` from the scan path). Cloud calls would
     silently lose personal-data detection.

These tests assert the patterns we expect catch representative example
strings. Failure here means a real regression in the protection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from emptyos.capabilities.outbound_scan import scan_outbound
from emptyos.sdk.personal_patterns import load


REPO_ROOT = Path(__file__).resolve().parent.parent
PATTERNS_FILE = REPO_ROOT / ".eos-personal"


@pytest.mark.api
class TestPatternCoverage:
    """Each high-confidence pattern matches its representative example."""

    def setup_method(self):
        self.patterns = load(PATTERNS_FILE)
        # Sanity: file exists and parses to >= the count we shipped with.
        # Hardcoding the lower bound catches "someone deleted half the file".
        assert len(self.patterns) >= 11, (
            f"Expected at least 11 patterns in .eos-personal, got {len(self.patterns)}"
        )

    def _matches(self, text: str) -> bool:
        return any(p.search(text) for p in self.patterns)

    # --- Identity ---

    def test_kevin_bian_matches(self):
        assert self._matches("Hi I'm Kevin Bian, nice to meet you")
        assert self._matches("Kevin  Bian")  # multiple spaces

    def test_bian_bin_matches(self):
        # Original Chinese-romanized name order — used on visa docs.
        assert self._matches("BIAN, Bin (Kevin)")
        assert self._matches("name: Bian Bin")

    def test_bianbin_email_matches(self):
        assert self._matches("contact: bianbin@gmail.com")
        assert self._matches("send to bianbin@example.com")

    def test_bianbin_alias_matches(self):
        assert self._matches("bianbin+test@gmail.com")

    def test_enerven_employer_matches(self):
        assert self._matches("started at Enerven in 2024")
        assert self._matches("Enerven")
        # Word boundary should NOT match a substring inside another word
        assert not self._matches("renenerventure")  # 'enerven' inside word

    # --- Paths ---

    def test_main_vault_path_matches(self):
        assert self._matches("D:/Main Vault/journal.md")
        assert self._matches(r"D:\Main Vault\journal.md")

    def test_users_kevin_path_matches(self):
        assert self._matches("C:/Users/Kevin/.claude/settings.json")
        assert self._matches(r"C:\Users\Kevin\AppData")

    # --- Coords ---

    def test_adelaide_coords_match(self):
        assert self._matches("lat: -34.928")
        assert self._matches("lon: 138.600")

    # --- Date contexts ---

    def test_visa_dates_match(self):
        assert self._matches("expiry = date(2029, 7, 1)")
        assert self._matches("issued = date(2025, 11, 25)")
        # Bare dates without context should NOT match (intentional — the
        # pattern is `date(YYYY, ...)` shape, not the digits themselves).
        assert not self._matches("2029-07-01")
        assert not self._matches("July 1, 2029")

    # --- Non-personal control: must not false-positive ---

    def test_generic_strings_do_not_match(self):
        controls = [
            "Hello, world",
            "import re",
            "lat: -33.8688, lon: 151.2093",  # Sydney coords, NOT in patterns
            "Path: /opt/emptyos/data",
            "Kevin",  # bare first name alone shouldn't trigger Kevin\s+Bian
            "Bian",  # bare last name alone shouldn't trigger either
            "Adelaide",  # bare city — deliberately NOT in patterns
            "Sydney",  # bare city — deliberately NOT in patterns
            "binbian.net",  # Kevin's domain — legitimate in code
        ]
        for s in controls:
            assert not self._matches(s), f"False positive on control: {s!r}"


@pytest.mark.api
class TestOutboundScanIntegration:
    """outbound_scan still wires .eos-personal patterns into its findings."""

    def test_finds_personal_via_outbound_scan(self):
        """Mixed string: personal + non-personal. Personal pattern surfaces."""
        text = (
            "Sending a prompt to OpenAI. Context: my name is Kevin Bian. "
            "Please summarize this article."
        )
        findings = scan_outbound(text)
        names = [f.pattern_name for f in findings]
        assert any("Personal data" in n for n in names), (
            f"Expected at least one 'Personal data' finding, got: {names}"
        )

    def test_finds_secret_via_outbound_scan(self):
        """Secret patterns still work (regression guard for the wider scanner)."""
        text = "Authorization: Bearer sk-proj-abcdefghijklmnopqrstuvwxyz1234"
        findings = scan_outbound(text)
        names = [f.pattern_name for f in findings]
        assert any("OpenAI" in n or "Bearer" in n for n in names), (
            f"Expected secret-pattern finding, got: {names}"
        )

    def test_clean_text_yields_no_findings(self):
        """Generic text produces no findings — sanity check on false-positives."""
        text = "What's the weather forecast for tomorrow in Adelaide?"
        findings = scan_outbound(text)
        # Adelaide is intentionally NOT in patterns (would false-positive on
        # weather/geocode demos). If this fails, someone added \bAdelaide\b.
        assert findings == [], (
            f"Expected zero findings on generic weather text, got: {findings}"
        )
