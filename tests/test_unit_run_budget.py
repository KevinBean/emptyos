"""Unit tests for the per-run cost budget (emptyos.sdk.run_budget) and the
cost table (emptyos.capabilities.cost). Pure — no daemon, no cloud.
"""

from __future__ import annotations

import pytest

from emptyos.capabilities.cost import estimate_cost
from emptyos.sdk.run_budget import (
    BudgetApprovalRequired,
    BudgetExceeded,
    RunBudget,
)

# --- cost table ------------------------------------------------------------

def test_local_provider_is_free():
    assert estimate_cost("speak", "edge-tts", chars=10000) == 0.0
    assert estimate_cost("draw", "comfyui", n=8) == 0.0
    assert estimate_cost("think", "ollama", tokens=100000) == 0.0
    assert estimate_cost("think", "claude-cli", tokens=100000) == 0.0  # subscription


def test_cloud_provider_priced():
    assert estimate_cost("draw", "openai-image", n=4) == pytest.approx(0.16)
    assert estimate_cost("speak", "openai-tts", chars=1000) == pytest.approx(0.015)
    assert estimate_cost("think", "openai-mini", tokens=10000) == pytest.approx(0.005)


def test_unknown_provider_on_cloud_capability_uses_conservative_default():
    # an un-named draw must NOT bill $0 — that would make a cap useless
    assert estimate_cost("draw", None, n=2) == pytest.approx(0.08)
    assert estimate_cost("speak", None, chars=2000) > 0


def test_unknown_capability_is_free():
    assert estimate_cost("footage", "pexels", query="x") == 0.0
    assert estimate_cost("totally-made-up", "whoever") == 0.0


def test_think_estimates_tokens_from_chars():
    # ~4 chars/token; 4000 chars ≈ 1000 tokens × $0.005/1k = $0.005
    assert estimate_cost("think", "openai", text="x" * 4000) == pytest.approx(0.005, rel=0.01)


# --- RunBudget: observe (default) ------------------------------------------

@pytest.mark.asyncio
async def test_observe_tracks_but_never_blocks():
    b = RunBudget(total_usd=0.01, mode="observe", per_action_usd=0.001,
                  cost_fn=lambda *a, **k: 5.0)
    async with b.spend("draw"):  # way over both caps
        pass
    assert b.spent_usd == pytest.approx(5.0)
    assert b.warnings == []  # observe stays silent


@pytest.mark.asyncio
async def test_default_no_cap_budget_is_harmless():
    b = RunBudget(cost_fn=lambda *a, **k: 2.0)
    async with b.spend("speak"):
        pass
    assert b.spent_usd == pytest.approx(2.0)
    assert b.remaining() is None  # no total → unbounded


# --- RunBudget: warn -------------------------------------------------------

@pytest.mark.asyncio
async def test_warn_records_overrun_but_proceeds():
    b = RunBudget(total_usd=1.0, mode="warn", cost_fn=lambda *a, **k: 2.5)
    async with b.spend("draw", label="img"):
        pass
    assert b.spent_usd == pytest.approx(2.5)  # still spent
    assert any("img" in w and "exceed run budget" in w for w in b.warnings)


# --- RunBudget: cap --------------------------------------------------------

@pytest.mark.asyncio
async def test_cap_blocks_over_total_before_call():
    b = RunBudget(total_usd=1.0, mode="cap", cost_fn=lambda *a, **k: 2.0)
    ran = False
    with pytest.raises(BudgetExceeded):
        async with b.spend("draw"):
            ran = True  # body must NOT run
    assert ran is False
    assert b.spent_usd == 0.0  # nothing billed for a blocked call


@pytest.mark.asyncio
async def test_cap_allows_under_total():
    b = RunBudget(total_usd=1.0, mode="cap", cost_fn=lambda *a, **k: 0.3)
    async with b.spend("draw"):
        pass
    async with b.spend("draw"):
        pass
    assert b.spent_usd == pytest.approx(0.6)
    assert b.remaining() == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_per_action_cap_raises_approval():
    b = RunBudget(total_usd=100.0, mode="cap", per_action_usd=0.5,
                  cost_fn=lambda *a, **k: 0.9)
    with pytest.raises(BudgetApprovalRequired) as ei:
        async with b.spend("draw", label="big-image"):
            pass
    assert ei.value.estimate == pytest.approx(0.9)
    assert ei.value.label == "big-image"


# --- reconcile + release ---------------------------------------------------

@pytest.mark.asyncio
async def test_charge_reconciles_actual_over_estimate():
    b = RunBudget(cost_fn=lambda *a, **k: 1.0)
    async with b.spend("draw") as charge:
        charge(0.25)  # actual came in cheaper than the estimate
    assert b.spent_usd == pytest.approx(0.25)
    assert b.reserved_usd == 0.0


@pytest.mark.asyncio
async def test_failed_call_releases_reservation_bills_nothing():
    b = RunBudget(total_usd=10.0, cost_fn=lambda *a, **k: 3.0)
    with pytest.raises(ValueError):
        async with b.spend("draw"):
            raise ValueError("provider blew up")
    assert b.spent_usd == 0.0
    assert b.reserved_usd == 0.0
    assert b.remaining() == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_snapshot_shape():
    b = RunBudget(total_usd=5.0, mode="cap", per_action_usd=1.0,
                  cost_fn=lambda *a, **k: 0.5)
    async with b.spend("speak"):
        pass
    snap = b.snapshot()
    assert snap["spent_usd"] == pytest.approx(0.5)
    assert snap["remaining_usd"] == pytest.approx(4.5)
    assert snap["mode"] == "cap"
    assert snap["total_usd"] == 5.0
