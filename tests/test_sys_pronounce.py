"""System tests for the pronunciation pipeline.

Covers three layers without requiring the 1.2GB wav2vec2 model to be
downloaded:
  1. Service-level: g2p (cmudict), alignment, summarize — pure functions.
  2. Capability-level: `BaseApp.pronounce()` routes through the pronounce
     capability and raises cleanly when no provider is wired.
  3. App-level: shadowing + voice-review degrade gracefully when
     pronounce is unavailable (the heuristic path still runs).

Tests that require the model are marked with `@pytest.mark.llm` so the
default suite stays fast and offline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Service module lives outside the package tree; import it explicitly.
SERVICE_DIR = Path(__file__).parent.parent / "services" / "pronounce"
sys.path.insert(0, str(SERVICE_DIR))


@pytest.fixture(scope="module")
def server_module():
    import server  # noqa: E402
    return server


# ── Layer 1: pure-function tests on the service helpers ─────────────────────


class TestServiceHelpers:
    def test_g2p_known_word(self, server_module):
        words = server_module.text_to_reference_phones("the quick brown fox")
        assert len(words) == 4
        assert [w["word"] for w in words] == ["the", "quick", "brown", "fox"]
        # cmudict has all four — none should fall through to OOV
        assert not any(w["oov"] for w in words)
        # cmudict returns ARPABET — "quick" → K W IH K
        assert words[1]["phones_arpabet"] == ["K", "W", "IH", "K"]

    def test_g2p_oov_word(self, server_module):
        words = server_module.text_to_reference_phones("xyzfoo bar")
        assert words[0]["oov"] is True
        # xyzfoo → X→K Y→IY Z→Z F→F O→AA O→AA via letter fallback
        assert len(words[0]["phones_arpabet"]) >= 4
        assert words[1]["oov"] is False  # bar is in cmudict

    def test_g2p_strips_punctuation(self, server_module):
        words = server_module.text_to_reference_phones("Hello, world!")
        assert [w["word"] for w in words] == ["Hello,", "world!"]
        # Words list keeps the raw token; lookup uses the stripped form.
        assert not any(w["oov"] for w in words)

    def test_alignment_match(self, server_module):
        ref = ["DH", "AH", "K"]
        hyp = [
            {"phone": "DH", "start": 0.0, "end": 0.05, "confidence": 0.9},
            {"phone": "AH", "start": 0.05, "end": 0.10, "confidence": 0.9},
            {"phone": "K",  "start": 0.10, "end": 0.15, "confidence": 0.9},
        ]
        rows = server_module._align_phones(ref, hyp)
        assert all(r["op"] == "match" for r in rows)
        assert len(rows) == 3

    def test_alignment_substitution(self, server_module):
        ref = ["DH", "AH"]
        hyp = [
            {"phone": "D",  "start": 0.0, "end": 0.05, "confidence": 0.71},
            {"phone": "AH", "start": 0.05, "end": 0.10, "confidence": 0.91},
        ]
        rows = server_module._align_phones(ref, hyp)
        assert rows[0]["op"] == "sub"
        assert rows[0]["ref"] == "DH" and rows[0]["hyp"] == "D"
        assert rows[1]["op"] == "match"

    def test_alignment_deletion(self, server_module):
        ref = ["K", "W", "IH", "K"]
        hyp = [
            {"phone": "K",  "start": 0.0,  "end": 0.05, "confidence": 0.9},
            {"phone": "W",  "start": 0.05, "end": 0.10, "confidence": 0.9},
            {"phone": "IH", "start": 0.10, "end": 0.15, "confidence": 0.9},
        ]
        rows = server_module._align_phones(ref, hyp)
        # Last K is deleted
        deletions = [r for r in rows if r["op"] == "del"]
        assert len(deletions) == 1
        assert deletions[0]["ref"] == "K"
        assert deletions[0]["hyp"] is None

    def test_summary_weak_phones(self, server_module):
        alignment = [
            {"ref": "DH", "hyp": "D",  "op": "sub",   "confidence": 0.7, "start": 0.0, "end": 0.05},
            {"ref": "DH", "hyp": None, "op": "del",   "confidence": 0.0, "start": None, "end": None},
            {"ref": "AH", "hyp": "AH", "op": "match", "confidence": 0.9, "start": 0.05, "end": 0.10},
        ]
        summary = server_module._summarize(alignment, [])
        assert "DH" in summary["weak_phones"]
        assert summary["phone_accuracy"] < 1.0
        assert summary["phones_total"] == 3
        assert summary["phones_matched"] == 1


# ── Layer 2: capability wiring ──────────────────────────────────────────────


class TestPronounceCapability:
    """The capability is registered in `setup.py` with no providers by
    default — the plugin appends a local provider at boot. With no provider
    wired, `execute()` should raise RuntimeError("No available provider for
    capability 'pronounce' (...)"). Apps catch that and fall back."""

    def test_capability_registered(self):
        from emptyos.capabilities import CapabilityRegistry
        from emptyos.capabilities.types import PronounceCapability

        # Direct registry test — `build_capabilities` requires a real Config
        # which is overkill here. The contract under test is just that the
        # capability *type* exists and registers cleanly with no providers.
        reg = CapabilityRegistry()
        reg.register("pronounce", PronounceCapability())
        assert reg.has("pronounce")
        assert reg.get("pronounce").providers == []

    @pytest.mark.asyncio
    async def test_capability_raises_when_no_provider(self):
        from emptyos.capabilities.types import PronounceCapability

        cap = PronounceCapability()
        cap.name = "pronounce"
        with pytest.raises(RuntimeError, match="No available provider"):
            await cap.execute(audio=b"\x00" * 100, reference_text="hi")


# ── Layer 3: app-level graceful degradation (smoke-only) ────────────────────


class TestShadowingDegradation:
    """When pronounce is offline, shadowing.score_attempt must still return
    a sensible entry — just without the `pronounce` field, or with the
    unavailable-marker shape."""

    def test_score_entry_shape_when_no_audio(self):
        # Without an audio_path, pronounce() is never called, so the entry
        # must not carry a `pronounce` field at all (avoids confusing the UI
        # into rendering an empty alignment strip).
        from apps.personal.shadowing.app import ShadowingApp  # noqa: E402  (path-dependent import)

        # We can't easily construct a real ShadowingApp without a kernel, so
        # this stays an import-only smoke test. Full integration is covered
        # by the existing test_sys_shadowing.py once the plugin is loaded.
        assert ShadowingApp.__name__ == "ShadowingApp"


# ── Layer 4: KB-backed pronunciation-guide round-trip ───────────────────────


class TestKbPronounceRoundTrip:
    """KB now owns the persistence layer for pronunciation guides. The
    contract: render a KB body via shadowing helpers, push it through
    kb.upsert_note → kb.get_note, and the parsed payload should round-trip
    the same fields the modal expects.

    These tests are *pure-Python* — they exercise only the parsing helpers
    on ShadowingApp without booting a kernel or hitting KB. The live KB
    round-trip is verified manually after restart per the plan's verification
    section."""

    def _shadowing_class(self):
        from apps.personal.shadowing.app import ShadowingApp
        return ShadowingApp

    def test_slug_shapes(self):
        S = self._shadowing_class()
        assert S._pair_slug("DH", "S", "sub") == "pair-dh-s"
        assert S._pair_slug("K", "", "del") == "phone-k"
        assert S._pair_slug("", "AH", "ins") == "avoid-ah"

    def test_render_kb_body_then_reparse(self):
        S = self._shadowing_class()
        gen = {
            "how_to_make": "Place your tongue between your teeth and turn voice on.",
            "target_examples": ["this", "breathe", "weather", "mother"],
            "confusable_examples": ["sip", "busy", "kiss", "boss"],
            "minimal_pairs": [["this", "sis"], ["they", "say"], ["breathe", "breeze"]],
            "common_mistake": "Speakers from S-default languages swap /ð/ for /s/.",
        }
        # Render a pair body, then re-parse via the modal projector.
        # Use a bare instance without invoking __init__ since we only need
        # the bound methods, not kernel state.
        inst = S.__new__(S)
        body = inst._render_kb_body("DH", "S", "sub", gen)
        fake_note = {
            "body": body,
            "properties": {"title": "Pronouncing DH vs S"},
            "slug": "pair-dh-s",
            "path": "30_Resources/EmptyOS/kb/sources/pair-dh-s.md",
        }
        payload = inst._note_to_modal_payload(fake_note, "DH", "S", "sub")
        assert payload["how_to_make"].startswith("Place your tongue")
        assert payload["target_examples"] == ["this", "breathe", "weather", "mother"]
        assert payload["confusable_examples"] == ["sip", "busy", "kiss", "boss"]
        assert payload["minimal_pairs"] == [["this", "sis"], ["they", "say"], ["breathe", "breeze"]]
        assert "swap /ð/" in payload["common_mistake"]
        # See-also wikilinks parsed out of the body
        slugs = {w["slug"] for w in payload["wikilinks"]}
        assert "phone-dh" in slugs
        assert "phone-s" in slugs
        # The tutorials URL is built from ref/hyp/op, not from the note body
        assert "DH" in payload["tutorials_url"] and "S" in payload["tutorials_url"]

    def test_extra_sections_preserved(self):
        """If the user adds a custom `## My notes` section to the vault file,
        the modal payload surfaces it under extra_sections — the projector
        doesn't silently drop user content."""
        S = self._shadowing_class()
        inst = S.__new__(S)
        body = (
            "# Pronouncing DH vs S\n\n"
            "## How to make it\nTongue between teeth.\n\n"
            "## My notes\nI keep forgetting the voicing.\n"
        )
        fake_note = {"body": body, "properties": {}, "slug": "pair-dh-s", "path": ""}
        payload = inst._note_to_modal_payload(fake_note, "DH", "S", "sub")
        assert "My notes" in payload["extra_sections"]
        assert "voicing" in payload["extra_sections"]["My notes"]
