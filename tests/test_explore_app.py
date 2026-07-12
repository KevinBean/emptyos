from __future__ import annotations

import importlib.util

from emptyos.sdk.web_search import SourceFencer, clean_page_text, is_http_url, is_public_web_url

from helpers import app_path

# Resolve via the track-tree scanner — explore moved labs/ -> standard/ and a
# hardcoded path silently breaks collection.
APP_PATH = app_path("explore") / "app.py"
spec = importlib.util.spec_from_file_location("explore_app_under_test", APP_PATH)
explore_app = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(explore_app)


def test_clean_page_text_collapses_noise_and_limits():
    raw = " One   two\r\n\r\n\r\nthree\tfour "
    assert clean_page_text(raw, limit=100) == "One two\n\nthree four"
    assert clean_page_text("abcdef", limit=3) == "abc"


def test_is_http_url_allows_only_http_urls():
    assert is_http_url("https://example.com/a")
    assert is_http_url("http://example.com")
    assert not is_http_url("javascript:alert(1)")
    assert not is_http_url("file:///tmp/a")
    assert not is_http_url("example.com")


def test_is_public_web_url_blocks_internal_targets():
    # IP literals — no DNS involved.
    assert not is_public_web_url("http://127.0.0.1:9000/settings/", resolve_dns=False)
    assert not is_public_web_url("http://10.0.0.5/", resolve_dns=False)
    assert not is_public_web_url("http://192.168.1.1/", resolve_dns=False)
    assert not is_public_web_url("http://169.254.169.254/latest/meta-data/", resolve_dns=False)
    assert not is_public_web_url("http://[::1]/", resolve_dns=False)
    # Local-shaped hostnames.
    assert not is_public_web_url("http://localhost:8188/", resolve_dns=False)
    assert not is_public_web_url("http://nas.local/", resolve_dns=False)
    assert not is_public_web_url("http://intranet/", resolve_dns=False)
    # Public-shaped targets pass the name/IP checks.
    assert is_public_web_url("https://example.com/page", resolve_dns=False)
    assert is_public_web_url("http://93.184.216.34/", resolve_dns=False)
    # Non-http schemes are rejected outright.
    assert not is_public_web_url("file:///etc/passwd", resolve_dns=False)


def test_source_prompt_block_numbers_sources():
    block = explore_app._source_prompt_block(
        [
            {"url": "https://example.com/a", "title": "Alpha", "site": "example.com", "text": "A body"},
            {"url": "https://docs.example.com/b", "title": "Beta", "text": "B body"},
        ],
        SourceFencer(False),
    )
    assert "[1] Alpha - example.com" in block
    assert "[2] Beta - docs.example.com" in block
    assert "URL: https://example.com/a" in block


def test_source_prompt_block_fences_when_enabled():
    block = explore_app._source_prompt_block(
        [{"url": "https://example.com/a", "title": "Alpha", "site": "example.com", "text": "A body"}],
        SourceFencer(True),
    )
    assert "<<<eos:source example.com>>>" in block
    assert "A body" in block
    assert "<<<eos:end-source>>>" in block


def test_yaml_quote_flattens_newlines():
    assert explore_app._yaml_quote("line one\nline two") == '"line one line two"'
    assert explore_app._yaml_quote('say "hi"') == '"say \\"hi\\""'


def test_render_note_uses_block_tags_and_source_links():
    app = explore_app.ExploreApp.__new__(explore_app.ExploreApp)
    note = app._render_note(
        query='What is "Explore"?',
        answer="A cited answer [1].",
        comparison="Agreement:\nEnough.",
        sources=[
            {"url": "https://example.com/a", "title": "Alpha", "site": "example.com"},
        ],
    )
    assert "tags:\n  - explore\n  - web-research" in note
    assert 'query: "What is \\"Explore\\"?"' in note
    assert "## Vault comparison" in note
    assert "1. [Alpha](https://example.com/a)" in note
    assert "digested: false" in note


def test_render_note_digest_leads_and_keeps_original_answer():
    app = explore_app.ExploreApp.__new__(explore_app.ExploreApp)
    note = app._render_note(
        query="IEC 60287",
        answer="Raw answer [1].",
        sources=[{"url": "https://github.com/x/y", "title": "Repo", "site": "github.com", "channel": "github"}],
        digest="## Summary\nDistilled.",
    )
    assert "digested: true" in note
    assert note.index("Distilled.") < note.index("## Original answer")
    assert "Raw answer [1]." in note
    assert "github.com · github" in note


def test_query_terms_extracts_discriminating_tokens():
    terms = explore_app.ExploreApp._query_terms("what is the IEC 60287 standard for")
    # Stopwords drop; digit-bearing terms sort first.
    assert terms[0] == "60287"
    assert "IEC" in terms and "standard" in terms
    assert "what" not in [t.lower() for t in terms]
    assert "the" not in [t.lower() for t in terms]


def test_channels_normalizes_csv_and_unknown_ids():
    app = explore_app.ExploreApp.__new__(explore_app.ExploreApp)
    assert app._channels(None) == ["web"]
    assert app._channels("github, arxiv") == ["github", "arxiv"]
    assert app._channels(["web", "bogus", "hn"]) == ["web", "hn"]
    assert app._channels(["bogus"]) == ["web"]


def test_resolve_channels_explicit_passthrough_and_auto_fallback():
    import asyncio

    app = explore_app.ExploreApp.__new__(explore_app.ExploreApp)
    # Explicit valid channels pass through without any think call.
    assert asyncio.run(app._resolve_channels("q", ["github", "hn"])) == ["github", "hn"]
    assert asyncio.run(app._resolve_channels("q", "arxiv,web")) == ["arxiv", "web"]
    # Explicit-but-unknown ids: no surprise think call, just web.
    assert asyncio.run(app._resolve_channels("q", ["bogus"])) == ["web"]
    # Auto with no think provider (bare instance) fails soft to web.
    assert asyncio.run(app._resolve_channels("q", None)) == ["web"]
    assert asyncio.run(app._resolve_channels("q", ["auto"])) == ["web"]


def test_resolve_channels_auto_uses_model_reply():
    import asyncio

    app = explore_app.ExploreApp.__new__(explore_app.ExploreApp)

    async def fake_think(prompt, **kwargs):
        return 'Here you go:\n```json\n["github", "arxiv", "bogus"]\n```'

    app.think = fake_think
    assert asyncio.run(app._resolve_channels("best CadQuery libraries", None)) == ["github", "arxiv"]


def test_history_upsert_inserts_updates_and_caps():
    items = explore_app._history_upsert([], {"id": "a", "query": "q1", "answer": "first"})
    assert [r["id"] for r in items] == ["a"]
    # Update merges over the existing record and moves it to the front.
    items = explore_app._history_upsert(
        [{"id": "b", "query": "q2"}, {"id": "a", "query": "q1", "answer": "first"}],
        {"id": "a", "stance": {"applicable": True}},
    )
    assert items[0]["id"] == "a"
    assert items[0]["answer"] == "first" and items[0]["stance"] == {"applicable": True}
    # Cap enforcement.
    many = [{"id": f"r{i}"} for i in range(5)]
    out = explore_app._history_upsert(many, {"id": "new"}, limit=3)
    assert len(out) == 3 and out[0]["id"] == "new"


def test_effective_top_effort_presets_and_explicit_top_wins():
    app = explore_app.ExploreApp.__new__(explore_app.ExploreApp)
    app.setting = lambda key, default=None: default  # bare instance — no kernel
    assert app._effective_top(None, "low") == 3
    assert app._effective_top(None, "medium") == 5
    assert app._effective_top(None, "HIGH") == 9
    assert app._effective_top(8, "low") == 8          # explicit top wins
    assert app._effective_top(None, "bogus") == explore_app.DEFAULT_TOP_N
    assert app._effective_top(None, None) == explore_app.DEFAULT_TOP_N


def test_looks_blocked_detects_challenge_pages():
    from emptyos.sdk.web_search import looks_blocked

    assert looks_blocked("Access Denied", "x" * 2000)
    assert looks_blocked("Just a moment...", "")
    assert looks_blocked("Oh noes!", "Anubis weighs your soul")
    assert looks_blocked("", "Please verify you are human to continue.")
    # Long real articles that merely mention captcha are NOT blocked.
    assert not looks_blocked("CAPTCHA history", "c" * 2000)
    assert not looks_blocked("Cable ampacity study", "Real article text " * 100)


def test_parse_stance_reply_validates_numbers_and_values():
    reply = (
        'Sure:\n```json\n{"applicable": true, "claim": "X causes Y",\n'
        ' "stances": [{"n": 1, "stance": "supports"}, {"n": 2, "stance": "against"},\n'
        '  {"n": 2, "stance": "mixed"}, {"n": 9, "stance": "supports"},\n'
        '  {"n": 3, "stance": "banana"}]}\n```'
    )
    out = explore_app._parse_stance_reply(reply, source_count=3)
    assert out["applicable"] is True
    assert out["claim"] == "X causes Y"
    # Duplicate n=2 keeps the first; n=9 out of range; "banana" invalid.
    assert out["stances"] == {1: "supports", 2: "against"}
    assert out["counts"]["supports"] == 1 and out["counts"]["against"] == 1


def test_parse_stance_reply_inapplicable_and_garbage():
    out = explore_app._parse_stance_reply('{"applicable": false, "claim": "", "stances": []}', 4)
    assert out["applicable"] is False and out["stances"] == {}
    out = explore_app._parse_stance_reply("not json at all", 4)
    assert out["applicable"] is False
    # applicable true but zero valid stances → not applicable in practice.
    out = explore_app._parse_stance_reply('{"applicable": true, "stances": []}', 4)
    assert out["applicable"] is False


def test_search_web_interleaves_and_dedupes_channels():
    import asyncio

    app = explore_app.ExploreApp.__new__(explore_app.ExploreApp)

    async def fake_channel(channel, query, top):
        data = {
            "web": [
                {"url": "https://a.com/1", "title": "A1", "channel": "web"},
                {"url": "https://shared.com/x", "title": "Shared", "channel": "web"},
            ],
            "github": [
                {"url": "https://github.com/r/1", "title": "G1", "channel": "github"},
                {"url": "https://shared.com/x", "title": "Shared dup", "channel": "github"},
            ],
        }
        return data.get(channel, [])

    app._search_one_channel = fake_channel
    out = asyncio.run(app._search_web("q", 8, ["web", "github"]))
    urls = [s["url"] for s in out]
    assert urls == ["https://a.com/1", "https://github.com/r/1", "https://shared.com/x"]
    assert out[1]["channel"] == "github"


# Source dedup moved to emptyos.sdk.deep_loop.dedupe_sources — covered by
# tests/test_sdk_deep_loop.py (explore injects it into deepen()).


def _make_app_for_run(rounds_by_query, followups_by_prior, depth, breadth=3):
    """Build an ExploreApp with the I/O methods stubbed so _run's loop control
    flow can be tested without web/cloud. rounds_by_query maps a query → the
    round dict _run_round should return; followups_by_prior is consulted in order."""
    import asyncio

    app = explore_app.ExploreApp.__new__(explore_app.ExploreApp)
    calls = {"rounds": [], "followups": 0, "synthesized": []}

    async def fake_resolve(query, raw=None):
        return ["web"]

    async def fake_round(query, top, chans):
        calls["rounds"].append(query)
        return rounds_by_query[query]

    async def fake_followups(query, prior):
        idx = calls["followups"]
        calls["followups"] += 1
        return followups_by_prior[idx] if idx < len(followups_by_prior) else []

    async def fake_synth(query, union):
        calls["synthesized"].append([s["url"] for s in union])
        return "MERGED ANSWER"

    async def fake_emit(*a, **k):
        return None

    app._resolve_channels = fake_resolve
    app._run_round = fake_round
    app._followups = fake_followups
    app._synthesize = fake_synth
    app._deep_depth = lambda: depth
    app._deep_breadth = lambda: breadth
    app._history_write = lambda rec: "hist-1"
    app.log_activity = lambda rec: None
    app.emit = fake_emit
    app.last_provenance = lambda: {}
    return app, calls, asyncio


def test_run_single_pass_when_deep_disabled():
    rounds = {"q": {"ok": True, "query": "q", "answer": "A1",
                    "sources": [{"url": "https://a/1", "text": "t"}], "skipped": []}}
    app, calls, asyncio = _make_app_for_run(rounds, [], depth=0)
    out = asyncio.run(app._run("q", 4, None))
    assert out["ok"] and out["rounds"] == 1
    assert out["answer"] == "A1"                    # first-round answer, no merge
    assert calls["followups"] == 0                   # loop never entered
    assert calls["synthesized"] == []                # no merge synthesis
    assert "text" not in out["sources"][0]           # stripped for transport
    assert out["source_texts"][0]["text"] == "t"     # full text retained
    assert out["followups"] == []                    # single pass surfaces no path


def test_run_deep_loop_fans_out_dedupes_and_merges():
    rounds = {
        "q":  {"ok": True, "query": "q",  "answer": "A1",
               "sources": [{"url": "https://a/1", "text": "t"}], "skipped": ["s"]},
        "f1": {"ok": True, "query": "f1", "answer": "AF1",
               "sources": [{"url": "https://a/1", "text": "t"},      # dup of round-1
                           {"url": "https://b/2", "text": "t2"}], "skipped": []},
        "f2": {"ok": True, "query": "f2", "answer": "AF2",
               "sources": [{"url": "https://c/3", "text": "t3"}], "skipped": []},
    }
    # follow-ups include the original query echoed back ("q") → must be filtered out
    app, calls, asyncio = _make_app_for_run(rounds, [["q", "f1", "f2"]], depth=1, breadth=3)
    out = asyncio.run(app._run("q", 4, None))
    assert out["ok"] and out["rounds"] == 3          # first + f1 + f2 (q filtered)
    assert "q" not in calls["rounds"][1:]            # echoed original not re-run
    assert out["answer"] == "MERGED ANSWER"
    # merge synthesized over the deduped union, in first-seen order, no dup url
    assert calls["synthesized"] == [["https://a/1", "https://b/2", "https://c/3"]]
    # research path surfaces each asked follow-up + the sources it pulled
    # (echoed "q" filtered before asking, so only f1/f2 appear)
    assert out["followups"] == [
        {"question": "f1", "sources": 2},
        {"question": "f2", "sources": 1},
    ]


def test_run_deep_loop_respects_breadth_cap():
    rounds = {q: {"ok": True, "query": q, "answer": q.upper(),
                  "sources": [{"url": f"https://{q}", "text": "t"}], "skipped": []}
              for q in ("q", "f1", "f2", "f3", "f4")}
    app, calls, asyncio = _make_app_for_run(rounds, [["f1", "f2", "f3", "f4"]], depth=1, breadth=2)
    out = asyncio.run(app._run("q", 4, None))
    assert out["rounds"] == 3                         # first + only 2 follow-ups (breadth cap)
    assert calls["rounds"] == ["q", "f1", "f2"]


def test_run_returns_error_payload_when_first_round_fails():
    rounds = {"q": {"ok": False, "error": "No web results found.", "query": "q", "sources": []}}
    app, calls, asyncio = _make_app_for_run(rounds, [["f1"]], depth=2)
    out = asyncio.run(app._run("q", 4, None))
    assert out["ok"] is False and out["error"] == "No web results found."
    assert calls["followups"] == 0                    # never looped past a failed first round
