"""The monthly spend cap on paid model calls (emptyos/capabilities/spend_cap.py).

A hosted learner daemon has no billing app, so the kernel meters paid `think`
calls and stops metered cloud providers once the month's cap is spent. Pinned:

  - config: off unless ``spend.monthly_cap_usd`` is a positive number; a cap
    switched off clears the one an earlier run left in the ledger;
  - meter: ``think:executed`` cost lands in the ledger; junk and zero do not;
    a metered provider reporting no cost is warned about once;
  - gate: only ``think``, only metered cloud providers (claude-cli and codex
    bill a subscription), only once the month is spent; a failing ledger allows;
  - the capability chain: over the cap a local provider still answers; with
    none, ``SpendCapReached`` — whose message keeps the "No available
    provider" prefix — and only when the cap is what kept a ready provider
    out (not a provider that is down anyway, not billing's daily cap);
  - the paths that call a provider directly honour it too: a settings-chosen
    or pinned provider, a pinned stream, ``pinned_execute``, the agent loop;
  - the real Kernel wires it, and a ledger failure does not stop boot;
  - the server's AI-offline reply and the apps say it is the limit.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from emptyos.capabilities import Capability, Provider
from emptyos.capabilities.consent import CloudConsentManager
from emptyos.capabilities.spend_cap import ACTOR_ID, SpendCap, SpendCapReached
from emptyos.sdk import autopilot


class _Config:
    def __init__(self, data_dir, value):
        self.data_dir = Path(data_dir)
        self._value = value

    def get(self, key, default=None):
        return self._value if key == "spend.monthly_cap_usd" else default


class _Prov(Provider):
    def __init__(self, name, *, cloud, up=True, metered=True):
        self.name = name
        self.trust = "service" if cloud else "owned"
        self.up = up
        self.metered = metered
        self.calls = 0

    async def available(self):
        return self.up

    async def execute(self, **kw):
        self.calls += 1
        return f"answer from {self.name}"

    async def execute_stream(self, **kw):
        self.calls += 1
        yield f"answer from {self.name}"


def _cap(tmp_path, usd=0.25, spent=0.0, **kw) -> SpendCap:
    cap = SpendCap(tmp_path, usd, **kw)
    if spent:
        cap.record(spent)
    return cap


CLOUD = lambda: _Prov("openrouter", cloud=True)  # noqa: E731


# ── config ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "", "0", 0, "-1", "abc"])
def test_off_unless_a_positive_cap_is_set(tmp_path, value):
    assert SpendCap.from_config(_Config(tmp_path, value)) is None


def test_a_positive_cap_is_recorded_in_the_ledger(tmp_path):
    cap = SpendCap.from_config(_Config(tmp_path, "0.25"))
    assert cap is not None and cap.monthly_cap_usd == 0.25
    assert autopilot.budget_status(tmp_path, ACTOR_ID)["monthly_cap_usd"] == 0.25


def test_restarting_keeps_the_month_already_spent(tmp_path):
    _cap(tmp_path, spent=0.10)
    again = _cap(tmp_path, 0.30)             # a daemon restart, cap raised
    assert again.status()["spent_usd"] == pytest.approx(0.10)


def test_switching_the_cap_off_clears_the_stale_one(tmp_path):
    _cap(tmp_path)
    assert SpendCap.clear_stale(tmp_path) is True
    assert autopilot.budget_status(tmp_path, ACTOR_ID)["monthly_cap_usd"] is None
    assert SpendCap.clear_stale(tmp_path) is False     # nothing left to clear


def test_clear_stale_without_a_ledger_writes_nothing(tmp_path):
    assert SpendCap.clear_stale(tmp_path) is False
    assert not (tmp_path / "autopilot").exists()


def test_the_ledger_key_cannot_be_an_app_id():
    # Billing keys its spend by app id in the same ledger; ":" is not legal there.
    assert ":" in ACTOR_ID


# ── meter ─────────────────────────────────────────────────────────────────

def test_think_executed_cost_is_recorded(tmp_path):
    cap = _cap(tmp_path)
    cap.on_think_executed(SimpleNamespace(data={"cost": 0.004, "provider": "openrouter"}))
    cap.on_think_executed(SimpleNamespace(data={"cost": 0.006}))
    assert cap.status()["spent_usd"] == pytest.approx(0.010)


@pytest.mark.parametrize("cost", [None, 0, -1, "free", {}])
def test_junk_or_zero_cost_is_not_recorded(tmp_path, cost):
    cap = _cap(tmp_path)
    assert cap.record(cost) is False
    assert cap.status()["spent_usd"] == 0


def test_a_metered_provider_reporting_no_cost_is_warned_about_once(tmp_path):
    warnings = []
    providers = {"openrouter": CLOUD(), "claude-cli": _Prov("claude-cli", cloud=True, metered=False)}
    cap = _cap(tmp_path, warn=warnings.append, find_provider=providers.get)
    for _ in range(2):
        cap.on_think_executed(SimpleNamespace(data={"provider": "openrouter", "cost": 0}))
    cap.on_think_executed(SimpleNamespace(data={"provider": "claude-cli", "cost": 0}))
    assert len(warnings) == 1 and "openrouter" in warnings[0]


# ── gate ──────────────────────────────────────────────────────────────────

def test_blocks_only_once_the_month_is_spent(tmp_path):
    cap = _cap(tmp_path)
    assert cap.blocks("think", CLOUD()) is False
    cap.record(0.30)          # spent and cap differ, so the text can't mix them up
    assert cap.blocks("think", CLOUD()) is True
    assert cap.reason() == "monthly AI limit reached ($0.3000 of $0.25)"


def test_only_think_is_gated(tmp_path):
    assert _cap(tmp_path, spent=1.0).blocks("speak", _Prov("edge-tts", cloud=True)) is False


def test_subscription_and_local_providers_are_not_gated(tmp_path):
    cap = _cap(tmp_path, spent=1.0)
    assert cap.blocks("think", _Prov("claude-cli", cloud=True, metered=False)) is False
    assert cap.blocks("think", _Prov("ollama", cloud=False)) is False


def test_real_subscription_providers_declare_themselves_unmetered():
    from emptyos.capabilities.providers.claude_cli import ClaudeCLIThinkProvider
    from emptyos.capabilities.providers.codex_cli import CodexCLIThinkProvider
    from emptyos.capabilities.spend_cap import is_metered

    for cls in (ClaudeCLIThinkProvider, CodexCLIThinkProvider):
        assert cls.metered is False and not is_metered(cls.__new__(cls))


def test_a_failing_ledger_allows_and_says_so(tmp_path, monkeypatch):
    warnings = []
    cap = _cap(tmp_path, spent=1.0, warn=warnings.append)
    monkeypatch.setattr(cap, "allows", lambda: (_ for _ in ()).throw(OSError("disk")))
    assert cap.blocks("think", CLOUD()) is False
    assert warnings and "allowing" in warnings[0]


# ── the capability chain ──────────────────────────────────────────────────

def _think(tmp_path, *providers, spent=0.0, consent=None):
    think = Capability(list(providers))
    think.name = "think"
    think.spend_cap = _cap(tmp_path, spent=spent)
    think.consent_manager = consent
    return think


def test_under_the_cap_the_cloud_provider_answers(tmp_path):
    result = asyncio.run(_think(tmp_path, CLOUD()).execute(prompt="hi"))
    assert result.value == "answer from openrouter"


def test_over_the_cap_a_local_provider_still_answers(tmp_path):
    cloud, local = CLOUD(), _Prov("ollama", cloud=False)
    result = asyncio.run(_think(tmp_path, cloud, local, spent=1.0).execute(prompt="hi"))
    assert result.value == "answer from ollama" and cloud.calls == 0


def test_over_the_cap_a_subscription_provider_still_answers(tmp_path):
    cli = _Prov("claude-cli", cloud=True, metered=False)
    result = asyncio.run(_think(tmp_path, CLOUD(), cli, spent=1.0).execute(prompt="hi"))
    assert result.value == "answer from claude-cli"


def test_over_the_cap_with_nothing_else_raises_spend_cap_reached(tmp_path):
    cloud = CLOUD()
    with pytest.raises(SpendCapReached) as exc:
        asyncio.run(_think(tmp_path, cloud, spent=1.0).execute(prompt="hi"))
    assert cloud.calls == 0
    # The prefix existing "AI unavailable" handlers match on is kept.
    assert str(exc.value).startswith("No available provider for capability 'think'")
    assert "monthly AI limit reached" in str(exc.value)


def test_streaming_also_raises_spend_cap_reached(tmp_path):
    async def drain():
        async for _ in _think(tmp_path, CLOUD(), spent=1.0).execute_stream(prompt="hi"):
            pass

    with pytest.raises(SpendCapReached):
        asyncio.run(drain())


def test_a_provider_that_is_down_anyway_is_not_blamed_on_the_cap(tmp_path):
    down = _Prov("openrouter", cloud=True, up=False)
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(_think(tmp_path, down, spent=1.0).execute(prompt="hi"))
    assert not isinstance(exc.value, SpendCapReached)


def test_billings_daily_cap_is_not_reported_as_the_monthly_one(tmp_path):
    cm = CloudConsentManager(policy="always")

    async def daily(provider, capability):
        return "daily cloud spend cap reached"

    cm.set_spend_guard(daily)
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(_think(tmp_path, CLOUD(), spent=0.0, consent=cm).execute(prompt="hi"))
    assert not isinstance(exc.value, SpendCapReached)


# ── paths that call a provider directly ───────────────────────────────────

def _kernel_with(tmp_path, *providers, spent=1.0):
    think = _think(tmp_path, *providers, spent=spent)
    return SimpleNamespace(
        spend_cap=think.spend_cap,
        capability=lambda name: think,
        services=SimpleNamespace(get_optional=lambda name: None),
        events=SimpleNamespace(emit=_noop),
    ), think


async def _noop(*a, **k):
    return None


class _App:
    def __init__(self, kernel):
        self.kernel = kernel
        self.manifest = SimpleNamespace(id="test-app")

    async def _emit_think_executed(self, *a, **k):
        return None

    def _apply_localization(self, *a, **k):
        return None


def test_a_settings_chosen_provider_is_skipped_when_capped(tmp_path):
    from emptyos.sdk import base_app_think

    cloud = CLOUD()
    kernel, _ = _kernel_with(tmp_path, cloud)
    got = asyncio.run(base_app_think._think_with_provider(_App(kernel), "openrouter", "hi", None, {}))
    assert got is None and cloud.calls == 0


def _domain_only_kernel(tmp_path, cloud):
    # The provider sits only in a domain chain (e.g. "text"), not the main one.
    kernel, think = _kernel_with(tmp_path)
    think.add_domain("text", [cloud])
    return kernel


def test_a_settings_chosen_domain_provider_is_skipped_when_capped(tmp_path):
    from emptyos.sdk import base_app_think

    cloud = CLOUD()
    kernel = _domain_only_kernel(tmp_path, cloud)
    got = asyncio.run(base_app_think._think_with_provider(_App(kernel), "openrouter", "hi", None, {}))
    assert got is None and cloud.calls == 0


def test_pinned_execute_skips_a_capped_domain_provider(tmp_path):
    from emptyos.sdk.base_app import BaseApp

    cloud = CLOUD()
    kernel = _domain_only_kernel(tmp_path, cloud)
    with pytest.raises(RuntimeError):
        asyncio.run(BaseApp.pinned_execute(_App(kernel), "think", "openrouter", prompt="hi"))
    assert cloud.calls == 0


def test_a_settings_chosen_provider_runs_under_the_cap(tmp_path):
    from emptyos.sdk import base_app_think

    kernel, _ = _kernel_with(tmp_path, CLOUD(), spent=0.0)
    got = asyncio.run(base_app_think._think_with_provider(_App(kernel), "openrouter", "hi", None, {}))
    assert got == "answer from openrouter"


def test_a_pinned_stream_falls_back_to_the_capped_chain(tmp_path):
    from emptyos.sdk import base_app_think

    cloud = CLOUD()
    kernel, _ = _kernel_with(tmp_path, cloud)

    async def drain():
        async for _ in base_app_think.think_stream(_App(kernel), "hi", provider="openrouter"):
            pass

    with pytest.raises(SpendCapReached):
        asyncio.run(drain())
    assert cloud.calls == 0


def test_pinned_execute_falls_back_to_the_capped_chain(tmp_path):
    from emptyos.sdk.base_app import BaseApp

    cloud = CLOUD()
    kernel, _ = _kernel_with(tmp_path, cloud)
    with pytest.raises(SpendCapReached):
        asyncio.run(BaseApp.pinned_execute(_App(kernel), "think", "openrouter", prompt="hi"))
    assert cloud.calls == 0


class _ToolProvider:
    name = "openrouter"
    kind = "openai"
    is_cloud = True
    metered = True

    def __init__(self):
        self.calls = 0

    async def execute_tools(self, **kw):
        self.calls += 1
        raise LookupError("reached the provider")


def _run_turn(app_ref, provider):
    from emptyos.sdk.agent_loop import AgentSession, run_turn

    return asyncio.run(run_turn(session=AgentSession(id="t"), user_text="hi", provider=provider,
                                tools={}, tool_consent=None, events=None, app_ref=app_ref))


def test_the_agent_loop_stops_at_the_cap(tmp_path):
    kernel, _ = _kernel_with(tmp_path, spent=1.0)
    provider = _ToolProvider()
    with pytest.raises(SpendCapReached):
        _run_turn(SimpleNamespace(kernel=kernel), provider)
    assert provider.calls == 0


def test_the_agent_loop_runs_under_the_cap(tmp_path):
    kernel, _ = _kernel_with(tmp_path, spent=0.0)
    provider = _ToolProvider()
    with pytest.raises(LookupError):
        _run_turn(SimpleNamespace(kernel=kernel), provider)
    assert provider.calls == 1


# ── the real kernel wiring ────────────────────────────────────────────────

def _kernel(tmp_path, cap_line):
    from emptyos.kernel import Kernel

    (tmp_path / "vault").mkdir(exist_ok=True)
    toml = tmp_path / "emptyos.toml"
    toml.write_text(
        f'[os]\ndata_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'[notes]\npath = "{(tmp_path / "vault").as_posix()}"\n{cap_line}',
        encoding="utf-8",
    )
    return Kernel(str(toml))


def test_kernel_wires_the_cap_into_think_and_meters_it(tmp_path):
    k = _kernel(tmp_path, "[spend]\nmonthly_cap_usd = 0.25\n")
    assert k.spend_cap is not None
    assert k.capabilities.get("think").spend_cap is k.spend_cap
    asyncio.run(k.events.emit("think:executed", {"cost": 0.3}, source="test"))
    assert k.spend_cap.status()["spent_usd"] == pytest.approx(0.3)


def test_kernel_without_the_cap_clears_a_stale_one(tmp_path):
    _cap(tmp_path / "data")
    k = _kernel(tmp_path, "")
    assert k.spend_cap is None
    assert k.capabilities.get("think").spend_cap is None
    assert autopilot.budget_status(tmp_path / "data", ACTOR_ID)["monthly_cap_usd"] is None


def test_kernel_boots_when_the_ledger_cannot_be_written(tmp_path, monkeypatch):
    errors = []

    def boom(*a, **k):
        raise OSError("read-only data dir")

    monkeypatch.setattr(SpendCap, "from_config", classmethod(lambda cls, *a, **k: boom()))
    from emptyos.kernel import syslog as syslog_mod

    real_error = syslog_mod.SystemLog.error
    monkeypatch.setattr(syslog_mod.SystemLog, "error",
                        lambda self, src, msg, **kw: (errors.append(msg), real_error(self, src, msg, **kw)))
    k = _kernel(tmp_path, "[spend]\nmonthly_cap_usd = 0.25\n")
    assert k.spend_cap is None
    assert any("NOT active" in m for m in errors)


# ── what users are told ───────────────────────────────────────────────────

def test_the_ai_offline_reply_names_the_limit():
    from emptyos.web.server import _ai_offline_body

    capped = _ai_offline_body(SpendCapReached(
        "No available provider for capability 'think' (chain=text): monthly AI limit reached"))
    assert capped["error"] == "ai_offline" and capped["reason"] == "spend_cap"
    assert "limit" in capped["message"]
    plain = _ai_offline_body(RuntimeError("No available provider for capability 'think' (chain=x)"))
    assert "reason" not in plain and "offline" in plain["message"]
    assert _ai_offline_body(RuntimeError("something else")) is None


def test_dictionary_lookup_reports_the_limit(tmp_path):
    from helpers import load_app_module

    try:
        lk = load_app_module("dictionary", "lookup", preload=("vocab_schema", "shared"))  # release-filter: optional
    except FileNotFoundError:
        from helpers import public_snapshot
        if not public_snapshot():
            raise
        pytest.skip("dictionary app absent (public snapshot)")
    think_calls = []

    class App:
        _definition_pack = lambda self: None
        lookup = lk.lookup
        api_lookup = lk.api_lookup

        def _track_lookup(self, word):
            pass

        async def _read_vault_word(self, word):
            return None

        async def think(self, prompt, **kw):
            think_calls.append(prompt)
            raise SpendCapReached("No available provider for capability 'think': monthly AI limit reached")

    got = asyncio.run(App().api_lookup(SimpleNamespace(query_params={"word": "luck"})))
    assert got["limit_reached"] is True and "AI limit" in got["message"]
    assert got["error"] == got["message"]      # the shared popup reads `error`
    assert len(think_calls) == 1               # no second, basic-prompt attempt


def test_learn_quiz_reports_the_limit(tmp_path):
    from helpers import load_app_module

    srs = load_app_module("learn", "srs")

    class App:
        _generate_quiz_for_slug = srs._generate_quiz_for_slug
        _quiz_cache_dir = tmp_path

        async def _resolve_lesson_source(self, slug):
            return {"title": "T", "body_md": "some lesson text"}

        async def think(self, prompt, **kw):
            raise SpendCapReached("monthly AI limit reached")

    got = asyncio.run(App()._generate_quiz_for_slug("lesson-1"))
    assert got["limit_reached"] is True and "AI limit" in got["error"]


def test_a_capability_registered_after_the_cap_gets_it(tmp_path):
    from emptyos.capabilities import CapabilityRegistry

    registry = CapabilityRegistry()
    cap = _cap(tmp_path)
    registry.set_spend_cap(cap)
    registry.register("think", Capability([]))
    assert registry.get("think").spend_cap is cap


def test_think_status_does_not_count_a_capped_provider(tmp_path):
    from emptyos.web.server import _think_status_body

    local = _Prov("ollama", cloud=False)
    both = _think(tmp_path, CLOUD(), local, spent=1.0)
    got = asyncio.run(_think_status_body(both))
    assert got["available"] is True and [p["name"] for p in got["providers"]] == ["ollama"]

    only_cloud = _think(tmp_path / "b", CLOUD(), spent=1.0)
    got = asyncio.run(_think_status_body(only_cloud))
    assert got == {"available": False, "reason": "spend_cap",
                   "message": "This month's AI limit is reached."}

    nothing = _think(tmp_path / "c", _Prov("ollama", cloud=False, up=False))
    assert asyncio.run(_think_status_body(nothing))["reason"] != "spend_cap"


def test_compare_skips_a_capped_provider(tmp_path):
    cloud, local = CLOUD(), _Prov("ollama", cloud=False)
    rows = asyncio.run(_think(tmp_path, cloud, local, spent=1.0).execute_compare(prompt="hi"))
    by_name = {r["provider"]: r for r in rows}
    assert cloud.calls == 0
    assert "monthly AI limit reached" in by_name["openrouter"]["error"]
    assert by_name["ollama"]["response"] == "answer from ollama"


def test_the_reading_layer_does_not_pick_a_capped_provider(tmp_path):
    from helpers import load_app_module

    try:
        reading = load_app_module("dictionary", "reading", preload=("vocab_schema", "shared"))  # release-filter: optional
    except FileNotFoundError:
        from helpers import public_snapshot
        if not public_snapshot():
            raise
        pytest.skip("dictionary app absent (public snapshot)")
    local = _Prov("ollama", cloud=False)        # Provider.auth_mode is "local" when not cloud
    assert local.auth_mode == "local"
    kernel, think = _kernel_with(tmp_path, CLOUD(), local, spent=1.0)
    think.providers_for = lambda domain=None, **kw: [CLOUD(), local]
    app = SimpleNamespace(kernel=kernel, manifest=SimpleNamespace(id="dictionary"))
    picked = reading._reading_providers(app, cloud=None)
    assert [p.name for p in picked] == ["ollama"]


def test_budgets_json_is_plain_json(tmp_path):
    # The stale-cap check reads the ledger without importing the SDK; pin the
    # layout it relies on.
    _cap(tmp_path)
    data = json.loads((tmp_path / "autopilot" / "budgets.json").read_text(encoding="utf-8"))
    assert ACTOR_ID in data["actors"]
