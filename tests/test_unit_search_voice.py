"""Unit tests for SearchApp.voice_search — the voice/Aura wrapper added when
`search.find` was registered as a verb across all four surfaces (2026-07-11).

Pins the spoken-result contract: a `{say, card, link}` dict, the empty-query and
no-results guards, and the search-page deep link with the query URL-encoded.
Pure: stubs `_search`, no kernel, no daemon.
"""

from __future__ import annotations

import asyncio

from helpers import load_app_module

# See test_unit_search_candidates.py for why this goes through the shared
# fixture: a bare spec-load cannot resolve the app's relative imports, and the
# resulting failure is at collection time, which aborts the whole suite.
search = load_app_module("search", "app")


class _FakeSearch(search.SearchApp):
    def __init__(self, results):
        self._results = results

    async def _search(self, query, top=15):
        return list(self._results)[:top]


def _run(results, query):
    return asyncio.run(_FakeSearch(results).voice_search(query))


class TestVoiceSearch:
    def test_returns_say_card_link_on_hits(self):
        out = _run(
            ["10_Projects/cables/cable-ratings.md", "30_Resources/iec-60287.md"],
            "cable ratings",
        )
        assert "2 notes" in out["say"]
        assert "cable-ratings" in out["say"]  # top result name, no .md, no path
        assert out["card"]["renderer"] == "task-list"
        assert [r["text"] for r in out["card"]["data"]] == ["cable-ratings", "iec-60287"]
        assert out["link"]["href"] == "/search/?q=cable%20ratings"

    def test_singular_phrasing_for_one_hit(self):
        out = _run(["a/one.md"], "one")
        assert "1 note " in out["say"] and "notes" not in out["say"]

    def test_no_results_speaks_a_miss_and_no_card(self):
        out = _run([], "nonexistent thing")
        assert "didn't find" in out["say"]
        assert "card" not in out
        assert "link" not in out

    def test_empty_query_prompts_instead_of_searching(self):
        out = _run(["should/not/matter.md"], "   ")
        assert "search your vault for" in out["say"].lower()
        assert "card" not in out

    def test_link_url_encodes_special_chars(self):
        out = _run(["x/y.md"], "a & b/c")
        # every reserved char encoded (safe='') so the query round-trips
        assert out["link"]["href"] == "/search/?q=a%20%26%20b%2Fc"

    def test_caps_at_five_names(self):
        out = _run([f"d/n{i}.md" for i in range(9)], "many")
        assert len(out["card"]["data"]) == 5  # _search(top=5) bound
