"""openai_compat bills the cost the endpoint reports, not a guess from the model name.

OpenRouter returns the charged amount in `usage.cost`. The provider used to
price every call from `PRICING`, a table keyed by bare OpenAI model names, so
any OpenRouter slug ("deepseek/deepseek-v4-flash", "openai/gpt-5.4-mini")
billed as $0. /billing/ and the autopilot budget caps both read that figure,
so the spend was invisible. Found 2026-09-24.

Daemon-free: HTTP is replaced with a fake session.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import emptyos.capabilities.providers.openai_compat as mod
from emptyos.capabilities.providers.openai_compat import (
    OpenAICompatThinkProvider,
    _reported_cost,
)

SOURCE = Path(mod.__file__)


# ── The pure rule ────────────────────────────────────────────────────


@pytest.mark.parametrize("usage, expected", [
    ({"cost": 3.15e-05}, 3.15e-05),      # OpenRouter's figure, kept exact
    ({"cost": 0}, 0.0),                   # a genuinely free call is still "reported"
    ({"cost": 2}, 2.0),
    ({}, None),                           # OpenAI direct: no cost field
    ({"cost": None}, None),
    ({"cost": "0.01"}, None),             # a string is not a number we trust
    ({"cost": True}, None),               # bool is an int in Python; not a price
    ({"cost": -1.0}, None),
    ({"cost": float("nan")}, None),
    ({"cost": float("inf")}, None),
])
def test_reported_cost(usage, expected):
    assert _reported_cost(usage) == expected


# ── Through the real code paths ──────────────────────────────────────


class _Resp:
    def __init__(self, lines=None, body=None):
        self._lines = lines or []
        self._body = body
        self.status = 200

    def raise_for_status(self):
        pass

    async def json(self):
        return self._body

    @property
    def content(self):
        async def gen():
            for line in self._lines:
                yield line.encode("utf-8")
        return gen()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_http(monkeypatch, resp):
    class _Session:
        def post(self, *a, **kw):
            return resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(mod.aiohttp, "ClientSession", lambda *a, **kw: _Session())


def _provider(monkeypatch, host, model):
    monkeypatch.setenv("TEST_KEY", "k")
    return OpenAICompatThinkProvider(
        host=host, model=model, api_key_env="TEST_KEY", provider_name="p",
    )


def _sse(usage):
    return [
        'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
        "data: " + json.dumps({"choices": [], "usage": usage}) + "\n",
        "data: [DONE]\n",
    ]


USAGE_WITH_COST = {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17, "cost": 0.000123}
USAGE_NO_COST = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000, "total_tokens": 2_000_000}


@pytest.mark.asyncio
async def test_stream_bills_the_reported_cost_for_an_unpriced_slug(monkeypatch):
    _patch_http(monkeypatch, _Resp(lines=_sse(USAGE_WITH_COST)))
    p = _provider(monkeypatch, "https://openrouter.ai/api", "deepseek/deepseek-v4-flash")
    async for _ in p.execute_stream(prompt="hi"):
        pass
    assert p.last_usage["cost"] == 0.000123


@pytest.mark.asyncio
async def test_stream_without_a_reported_cost_still_uses_the_table(monkeypatch):
    # OpenAI direct sends no cost; gpt-5.4-mini is $0.75 in / $4.50 out per 1M.
    _patch_http(monkeypatch, _Resp(lines=_sse(USAGE_NO_COST)))
    p = _provider(monkeypatch, "https://api.openai.com", "gpt-5.4-mini")
    async for _ in p.execute_stream(prompt="hi"):
        pass
    assert p.last_usage["cost"] == 5.25


@pytest.mark.asyncio
async def test_execute_bills_the_reported_cost(monkeypatch):
    body = {"choices": [{"message": {"content": "ok"}}], "usage": USAGE_WITH_COST}
    _patch_http(monkeypatch, _Resp(body=body))
    p = _provider(monkeypatch, "https://openrouter.ai/api", "openai/gpt-5.4-mini")
    assert await p.execute(prompt="hi") == "ok"
    assert p.last_usage["cost"] == 0.000123


def test_no_call_site_prices_from_the_table_directly():
    # The tool path (execute_tools) is not exercised above; this pins it and
    # any future site. Only _usage_cost itself may call the table pricer.
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    callers = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("_calc_cost_with_cache", "_calc_cost")):
                    callers.add(fn.name)
    assert callers == {"_usage_cost"}
    usage_cost_sites = sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_usage_cost"
    )
    assert usage_cost_sites == 3
