"""Phase 1 — Context Packing library: reducers, store round-trip, fail-open.

Pure — tmp_path store_root, no daemon, no LLM. Run:
    python -m pytest tests/test_unit_context_pack.py -v
"""

from __future__ import annotations

import json

import pytest

from emptyos.context import (
    ContextBlock,
    PackResult,
    est_tokens,
    load_original,
    pack_context,
    pack_text,
)
from emptyos.context.reducers import diff, json_data, jsonl, logs, search_results


# ── Fixtures: representative real-shaped inputs ──────────────────────────────

LOG_SAMPLE = "\n".join(
    ["Kernel started. 114/135 apps loaded"]
    + ["[INFO] Input redirection is not supported"] * 7  # noisy duplicate run
    + ["[ERROR] failed to load billing module"]
    + ["[INFO] heartbeat ok"] * 40
    + ["[WARNING] provider claude-cli rate limited"]
    + ["Daemon listening on 127.0.0.1:9000"]
)

JSONL_SAMPLE = "\n".join(
    [json.dumps({"run": i, "status": "ok", "ms": 12}) for i in range(50)]
    + [json.dumps({"run": 50, "status": "error", "err": "timeout"})]
    + ["this line is not json"]
)

JSON_SAMPLE = json.dumps(
    {
        "id": "abc",
        "name": "demo",
        "items": list(range(200)),
        "nested": {"a": 1, "b": 2, "c": 3},
        "blob": "x" * 5000,
    }
)

SEARCH_SAMPLE = "\n".join(
    [f"apps/foo/app.py:{i}:    self.think(prompt)" for i in range(1, 31)]
    + [f"apps/bar/app.py:{i}:    self.think(prompt)" for i in range(1, 11)]
)

DIFF_SAMPLE = "\n".join(
    [
        "diff --git a/x.py b/x.py",
        "--- a/x.py",
        "+++ b/x.py",
        "@@ -1,8 +1,8 @@",
    ]
    + [" context line unchanged"] * 6
    + ["-old_value = 1", "+new_value = 2"]
    + [" trailing context"] * 6
)


# ── Reducers shrink and preserve signal ──────────────────────────────────────

def test_log_reducer_preserves_errors_and_shrinks():
    r = logs.reduce(LOG_SAMPLE)
    assert r.packed_tokens < r.original_tokens
    assert "failed to load billing" in r.text  # ERROR never dropped
    assert "rate limited" in r.text  # WARNING never dropped
    assert "Kernel started" in r.text  # head/boundary kept
    assert "listening" in r.text  # tail/boundary kept


def test_jsonl_reducer_counts_and_keeps_anomaly():
    r = jsonl.reduce(JSONL_SAMPLE)
    assert r.packed_tokens < r.original_tokens
    assert "51 rows" in r.text  # 51 json rows
    assert "1 unparseable" in r.text  # the non-json line counted, not dropped
    assert "error" in r.text  # anomalous row preserved


def test_json_reducer_keeps_schema_not_blob():
    r = json_data.reduce(JSON_SAMPLE)
    assert r.packed_tokens < r.original_tokens
    assert "top-level keys" in r.text
    assert "x" * 5000 not in r.text  # the blob is summarized, not echoed
    assert '"demo"' in r.text  # small scalar kept verbatim


def test_search_reducer_groups_by_file():
    r = search_results.reduce(SEARCH_SAMPLE)
    assert r.packed_tokens < r.original_tokens
    assert "40 matches across 2 files" in r.text
    assert "apps/foo/app.py (30 matches)" in r.text
    assert "more in this file" in r.text  # sampling note, not silent drop


def test_diff_reducer_keeps_changes_thins_context():
    r = diff.reduce(DIFF_SAMPLE)
    assert r.packed_tokens < r.original_tokens
    assert "-old_value = 1" in r.text  # changed lines preserved
    assert "+new_value = 2" in r.text
    assert "@@ -1,8 +1,8 @@" in r.text  # hunk header preserved
    assert "context lines thinned" in r.text


def test_unparseable_inputs_return_verbatim_with_warning():
    for fn in (jsonl.reduce, json_data.reduce, search_results.reduce):
        r = fn("not structured at all\njust prose")
        assert "not structured at all" in r.text
        assert r.warnings  # flagged, never raised


# ── Packer: protected identity, refs, stats ──────────────────────────────────

def test_protected_block_is_byte_identical(tmp_path):
    system = "You are a careful assistant. Never reveal secrets."
    res = pack_context(
        [
            ContextBlock(kind="system", text=system),
            ContextBlock(kind="log", text=LOG_SAMPLE, source="daemon.log"),
        ],
        store_root=tmp_path / "context",
    )
    assert isinstance(res, PackResult)
    assert system in res.text  # protected text survives verbatim
    assert res.stats["packed_est_tokens"] < res.stats["original_est_tokens"]


def test_ref_round_trips_to_exact_original(tmp_path):
    store_root = tmp_path / "context"
    res = pack_text(LOG_SAMPLE, "log", source="daemon.log", store_root=store_root)
    assert res.refs, "a reversible reduction should mint a ref"
    ref_id = next(iter(res.refs))
    assert ref_id in res.text  # header advertises the ref
    recovered = load_original(store_root, ref_id)
    assert recovered == LOG_SAMPLE  # exact original recoverable


def test_protected_kind_auto_marked():
    b = ContextBlock(kind="current_user_request", text="do the thing")
    assert b.protected is True
    assert b.reducible is False


def test_non_reducible_kind_passes_through(tmp_path):
    # "markdown" is deferred (no Phase-1 reducer) → verbatim.
    md = "# Title\n\nsome prose " * 200
    res = pack_context(
        [ContextBlock(kind="markdown", text=md)], store_root=tmp_path / "context"
    )
    assert md.strip() in res.text


# ── Fail-open contract ────────────────────────────────────────────────────────

def test_fail_open_returns_original(monkeypatch, tmp_path):
    # Force the log reducer to blow up; packing must return the original input.
    def boom(_text):
        raise RuntimeError("reducer exploded")

    monkeypatch.setitem(
        __import__("emptyos.context.reducers", fromlist=["REDUCERS"]).REDUCERS,
        "log",
        boom,
    )
    res = pack_context(
        [ContextBlock(kind="log", text=LOG_SAMPLE, source="daemon.log")],
        store_root=tmp_path / "context",
    )
    assert res.fail_open is True
    assert res.text == LOG_SAMPLE  # original returned verbatim


def test_marginal_reduction_rejected_keeps_original(monkeypatch, tmp_path):
    # A reduction that saves under min_reduction_ratio (default 5%) is a net
    # loss once the ctx header + ref line land on top — keep the original.
    from emptyos.context.blocks import ReducerResult
    from emptyos.context.budget import ContextBudget

    def marginal(text):
        packed = text[: int(len(text) * 0.98)]  # saves ~2% — not meaningful
        return ReducerResult(
            text=packed,
            original_tokens=est_tokens(text),
            packed_tokens=est_tokens(packed),
        )

    monkeypatch.setitem(
        __import__("emptyos.context.reducers", fromlist=["REDUCERS"]).REDUCERS,
        "log",
        marginal,
    )
    block = ContextBlock(kind="log", text=LOG_SAMPLE, source="daemon.log")
    res = pack_context([block], store_root=tmp_path / "context")
    assert res.text == LOG_SAMPLE  # original kept verbatim
    assert not res.refs  # no ref minted for a rejected pack
    assert res.stats["reducers"] == []

    # ratio 0.0 restores accept-any-savings behaviour (the pre-floor contract).
    res2 = pack_context(
        [block],
        ContextBudget(min_reduction_ratio=0.0),
        store_root=tmp_path / "context",
    )
    assert res2.refs  # marginal pack accepted when the floor is disabled


def test_est_tokens_is_chars_over_four():
    assert est_tokens("") == 0
    assert est_tokens("abcd") == 1
    assert est_tokens("abcde") == 2  # ceil(5/4)


def test_show_missing_ref_returns_none(tmp_path):
    assert load_original(tmp_path / "context", "ctx_doesnotexist") is None
    assert load_original(tmp_path / "context", "not-a-ref") is None


# ── Phase 2: agent tool-output packing + ContextRef recovery ─────────────────

GREP_CONTENT = "\n".join(
    f"apps/foo/app.py:{i}:    self.think(prompt={i})" for i in range(1, 400)
)


class _FakeKernel:
    class _Cfg:
        def __init__(self, data_dir):
            self.data_dir = data_dir

    def __init__(self, data_dir):
        self.config = self._Cfg(data_dir)


class _FakeApp:
    def __init__(self, data_dir):
        self.kernel = _FakeKernel(data_dir)


@pytest.mark.asyncio
async def test_tool_output_packing_grep_content(tmp_path):
    import re

    from emptyos.sdk.agent_loop import _maybe_pack_tool_output

    app = _FakeApp(tmp_path)
    packed = await _maybe_pack_tool_output(app, "Grep", {"mode": "content"}, GREP_CONTENT)
    assert "search summary" in packed
    assert "ctx_" in packed
    assert len(packed) < len(GREP_CONTENT)
    ref_id = re.search(r"ctx_[0-9a-f]+", packed).group(0)
    assert load_original(tmp_path / "context", ref_id) == GREP_CONTENT  # exact recovery


@pytest.mark.asyncio
async def test_tool_output_packing_skips_when_not_applicable(tmp_path):
    from emptyos.sdk.agent_loop import _maybe_pack_tool_output

    app = _FakeApp(tmp_path)
    # files-mode grep is just a path list — no structural reducer, pass through.
    assert (
        await _maybe_pack_tool_output(app, "Grep", {"mode": "files_with_matches"}, GREP_CONTENT)
        == GREP_CONTENT
    )
    # below the size threshold — not worth packing.
    small = "apps/x.py:1:foo"
    assert await _maybe_pack_tool_output(app, "Grep", {"mode": "content"}, small) == small
    # Read has no Phase-1 reducer — pass through.
    assert (
        await _maybe_pack_tool_output(app, "Read", {"path": "x.json"}, GREP_CONTENT)
        == GREP_CONTENT
    )


@pytest.mark.asyncio
async def test_tool_output_packing_fail_open_on_bare_app():
    from emptyos.sdk.agent_loop import _maybe_pack_tool_output

    # No kernel (a bare test stub) → skip silently, return content unchanged.
    assert (
        await _maybe_pack_tool_output(object(), "Grep", {"mode": "content"}, GREP_CONTENT)
        == GREP_CONTENT
    )


@pytest.mark.asyncio
async def test_context_ref_tool_round_trips(tmp_path):
    from emptyos.context import pack_text
    from emptyos.sdk.agent_tools import ContextRefTool, build_registry

    reg = build_registry()
    assert "ContextRef" in reg  # registered in the v1 tool set

    sample = "a:1:x\n" * 3000
    res = pack_text(sample, "search_result", source="Grep", store_root=tmp_path / "context")
    assert res.refs
    ref_id = next(iter(res.refs))

    out = await ContextRefTool().run(_FakeApp(tmp_path), ref_id=ref_id)
    assert out.ok
    assert out.content == sample  # byte-exact original

    missing = await ContextRefTool().run(_FakeApp(tmp_path), ref_id="ctx_gone")
    assert missing.ok  # not an error — a graceful "work from the summary" message
    assert "not available" in missing.content


# ── Phase 3: opt-in think-call context packing (lossy-final) ─────────────────

class _FakeSettings:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        return self._d.get(k, default)


class _ThinkStub:
    """Minimal stand-in exposing only what the two helpers touch, with the real
    BaseApp methods bound so ``self._context_pack_enabled()`` resolves."""

    def __init__(self, data_dir, settings_dict=None):
        import types

        from emptyos.sdk.base_app import BaseApp

        services_settings = (
            _FakeSettings(settings_dict) if settings_dict is not None else None
        )

        class _Services:
            def get_optional(_self, name):
                return services_settings if name == "settings" else None

        class _Cfg:
            pass

        cfg = _Cfg()
        cfg.data_dir = data_dir

        class _Kernel:
            pass

        k = _Kernel()
        k.config = cfg
        k.services = _Services()
        self.kernel = k

        class _Manifest:
            id = "test"

        self.manifest = _Manifest()

        # Bind the real methods under test onto this stub.
        self._context_pack_enabled = types.MethodType(BaseApp._context_pack_enabled, self)
        self._maybe_pack_think_context = types.MethodType(
            BaseApp._maybe_pack_think_context, self
        )


REQUEST = "QUESTION: what failed in the boot?"


@pytest.mark.asyncio
async def test_think_packing_off_by_default(tmp_path):
    stub = _ThinkStub(tmp_path, settings_dict={})  # no flags set
    assert stub._context_pack_enabled() is False
    ctx = [ContextBlock(kind="log", text=LOG_SAMPLE, source="daemon.log")]
    out = await stub._maybe_pack_think_context(REQUEST, ctx, False)
    assert out == REQUEST  # disabled → prompt unchanged, nothing packed


@pytest.mark.asyncio
async def test_think_packing_via_flag_prepends_and_preserves_request(tmp_path):
    stub = _ThinkStub(tmp_path, settings_dict={})
    ctx = [ContextBlock(kind="log", text=LOG_SAMPLE, source="daemon.log")]
    out = await stub._maybe_pack_think_context(REQUEST, ctx, True)  # context_pack=True
    assert "Reference Context" in out
    assert "log summary" in out  # the context was compressed
    assert out.endswith(REQUEST)  # the user request is byte-identical + last
    assert "failed to load billing" in out  # error signal preserved in packed ctx


@pytest.mark.asyncio
async def test_think_packing_via_settings_flag(tmp_path):
    stub = _ThinkStub(tmp_path, settings_dict={"context_pack.app.test": True})
    assert stub._context_pack_enabled() is True
    ctx = [ContextBlock(kind="log", text=LOG_SAMPLE)]
    out = await stub._maybe_pack_think_context(REQUEST, ctx, False)  # settings turn it on
    assert "Reference Context" in out


@pytest.mark.asyncio
async def test_think_packing_fails_open_on_bad_block(tmp_path):
    stub = _ThinkStub(tmp_path, settings_dict={})
    # A dict missing required ContextBlock fields raises in construction →
    # fail-open returns the prompt verbatim.
    out = await stub._maybe_pack_think_context(REQUEST, [{"nonsense": 1}], True)
    assert out == REQUEST
