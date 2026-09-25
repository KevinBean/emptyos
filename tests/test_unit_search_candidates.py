"""Unit tests for the embedding candidate-selection order in the search app.

Pins the 2026-07-10 truncation bug: `_embed_search` walked `vault.rglob("*.md")`
and broke at `_MAX_EMBED_NOTES`, so on a 12.6k-note vault the candidate set was
whatever the filesystem yielded first — `10_Projects/` was never reached while
`40_Archive/` was indexed in full.

Pure: no kernel, no daemon, no vault. Imports the module-level helper directly.
"""

from __future__ import annotations

import pytest

from helpers import load_app_module

# Loaded through the shared fixture, which registers `apps.search` in
# sys.modules so the app's relative imports resolve without booting the kernel
# (.claude/rules/multi-module-apps.md § Test fixtures). This file used to
# spec-load app.py standalone, on the stated assumption that "search has no
# relative imports" — true when written, false once search/app.py gained
# `from . import indexer`. It then failed at COLLECTION, which aborts the whole
# suite rather than just this module, so the blast radius of that assumption
# going stale was every test in the repo.
search_app = load_app_module("search", "app")


def _order(rels_with_mtime):
    return [rel for rel, _ in search_app._embed_candidate_order(rels_with_mtime)]


class TestEmbedCandidateOrder:
    def test_active_folders_rank_before_cold_archive(self):
        """The regression. Archive notes must not crowd out active projects."""
        ordered = _order([
            ("40_Archive/old-thing.md", 9_000.0),   # newest by mtime, but cold
            ("10_Projects/live/live.md", 1.0),      # oldest by mtime, but active
        ])
        assert ordered[0] == "10_Projects/live/live.md"
        assert ordered[1] == "40_Archive/old-thing.md"

    def test_within_a_band_newest_first(self):
        ordered = _order([
            ("10_Projects/stale.md", 100.0),
            ("10_Projects/fresh.md", 900.0),
            ("20_Areas/mid.md", 500.0),
        ])
        assert ordered == [
            "10_Projects/fresh.md",
            "20_Areas/mid.md",
            "10_Projects/stale.md",
        ]

    def test_every_cold_prefix_is_demoted(self):
        cold = [f"{p}note.md" for p in search_app._EMBED_COLD_PREFIXES]
        ordered = _order([(c, 9_000.0) for c in cold] + [("10_Projects/a.md", 0.0)])
        assert ordered[0] == "10_Projects/a.md"
        assert len(ordered) == len(cold) + 1

    def test_case_insensitive_prefix_match(self):
        ordered = _order([("40_ARCHIVE/x.md", 9_000.0), ("00_Inbox/y.md", 0.0)])
        assert ordered[0] == "00_Inbox/y.md"

    def test_is_pure_and_does_not_mutate_input(self):
        rels = [("40_Archive/a.md", 1.0), ("10_Projects/b.md", 2.0)]
        before = list(rels)
        search_app._embed_candidate_order(rels)
        assert rels == before

    def test_empty_input(self):
        assert search_app._embed_candidate_order([]) == []

    @pytest.mark.parametrize("cap", [1, 3, 10])
    def test_capping_the_ranked_order_keeps_active_notes(self, cap):
        """Simulates what _embed_search does: rank, then take the first `cap`.

        Under the old walk-then-break this test's archive notes would fill the
        budget whenever the filesystem yielded them first.
        """
        rels = [(f"40_Archive/a{i}.md", 500.0) for i in range(20)]
        rels += [(f"10_Projects/p{i}.md", 1.0) for i in range(3)]
        selected = _order(rels)[:cap]
        active = [r for r in selected if r.startswith("10_Projects/")]
        assert len(active) == min(cap, 3), (
            f"cap={cap} spent its budget on archive: {selected}"
        )
