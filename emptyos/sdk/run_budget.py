"""RunBudget — a per-run cost ceiling for a staged pipeline.

Borrowed 2026-06-21 from OpenMontage's estimate→reserve→reconcile budget
control (idea only). Orthogonal to the *per-actor monthly* cap in
`emptyos/sdk/autopilot.py`: that is the outer ceiling ("this app may spend $X
this month"); a RunBudget is the inner ceiling ("this single generation run may
spend $Y"). The two compose — a finished run forwards its spend into the
monthly ledger via `autopilot.record_spend` (the consumer does this; the budget
stays pure).

The unit a stage works with is the async ``spend`` context manager::

    async with ctx.budget.spend("draw", provider="openai-image", n=4) as charge:
        img = await ctx.app.draw(...)
        charge(actual_usd)          # optional — defaults to the estimate

On enter it estimates the call (via the injected cost fn, default
`emptyos.capabilities.cost.estimate_cost`), classifies it against the caps, and
**reserves** the estimate. On normal exit it **reconciles** reserved→actual
(``charge``'s value, or the estimate if none given). If the wrapped call raises,
the reservation is released and nothing is billed.

Three modes:

- ``observe`` (default) — track spend, never block. A budget with no caps and
  this mode is a harmless accumulator, which is exactly what an un-budgeted
  pipeline run gets, so stages can call ``spend`` unconditionally.
- ``warn`` — record an over-budget / over-per-action note in ``warnings`` and
  proceed.
- ``cap`` — raise ``BudgetExceeded`` (run total exceeded) or
  ``BudgetApprovalRequired`` (a single call exceeds ``per_action_usd``) *before*
  the call fires.

Pure (stdlib only; the cost fn is lazy-imported and wrapped to fail open), so it
unit-tests without a daemon.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """Raised in ``cap`` mode when a call would push the run over ``total_usd``."""


class BudgetApprovalRequired(Exception):
    """Raised in ``cap`` mode when a single call exceeds ``per_action_usd`` —
    the pipeline turns this into a *pause* (resumable after approval), distinct
    from a hard error."""

    def __init__(self, message: str, *, estimate: float, label: str):
        super().__init__(message)
        self.estimate = estimate
        self.label = label


def _default_cost(capability: str, provider: str | None, **params) -> float:
    try:
        from emptyos.capabilities.cost import estimate_cost
        return estimate_cost(capability, provider, **params)
    except Exception:
        return 0.0


@dataclass
class RunBudget:
    """A per-run spend ceiling. ``total_usd``/``per_action_usd`` of None = no
    cap on that axis. ``cost_fn`` is injectable for tests; default uses the
    real capability cost table."""

    total_usd: float | None = None
    mode: str = "observe"  # observe | warn | cap
    per_action_usd: float | None = None
    spent_usd: float = 0.0
    reserved_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)
    cost_fn: Callable[..., float] | None = None

    def remaining(self) -> float | None:
        if self.total_usd is None:
            return None
        return max(0.0, self.total_usd - self.spent_usd - self.reserved_usd)

    def estimate(self, capability: str, provider: str | None = None, **params) -> float:
        fn = self.cost_fn or _default_cost
        try:
            return max(0.0, float(fn(capability, provider, **params)))
        except Exception:
            return 0.0  # fail-open: an un-priceable call never blocks

    def _classify(self, est: float) -> str:
        """ok | approval | block — pure, ignores mode (mode decides the action)."""
        if self.per_action_usd is not None and est > self.per_action_usd:
            return "approval"
        if self.total_usd is not None and (self.spent_usd + self.reserved_usd + est) > self.total_usd:
            return "block"
        return "ok"

    @contextlib.asynccontextmanager
    async def spend(self, capability: str, *, provider: str | None = None,
                    label: str | None = None, **params):
        """Reserve → run body → reconcile. See module docstring."""
        est = self.estimate(capability, provider, **params)
        label = label or capability
        verdict = self._classify(est)

        if verdict == "approval":
            msg = (f"{label}: estimated ${est:.4f} exceeds per-action cap "
                   f"${self.per_action_usd:.4f}")
            if self.mode == "cap":
                raise BudgetApprovalRequired(msg, estimate=est, label=label)
            if self.mode == "warn":
                self.warnings.append(msg)
        elif verdict == "block":
            msg = (f"{label}: estimated ${est:.4f} would exceed run budget "
                   f"${self.total_usd:.4f} (already ${self.spent_usd:.4f})")
            if self.mode == "cap":
                raise BudgetExceeded(msg)
            if self.mode == "warn":
                self.warnings.append(msg)

        self.reserved_usd += est
        box: dict = {"actual": None}

        def charge(actual: float | None = None) -> None:
            box["actual"] = est if actual is None else max(0.0, float(actual))

        try:
            yield charge
        except Exception:
            self.reserved_usd = max(0.0, self.reserved_usd - est)
            raise
        self.reserved_usd = max(0.0, self.reserved_usd - est)
        self.spent_usd += box["actual"] if box["actual"] is not None else est

    def snapshot(self) -> dict:
        """Audit view for ``run.json`` / a panel."""
        return {
            "total_usd": self.total_usd,
            "mode": self.mode,
            "per_action_usd": self.per_action_usd,
            "spent_usd": round(self.spent_usd, 6),
            "reserved_usd": round(self.reserved_usd, 6),
            "remaining_usd": self.remaining(),
            "warnings": list(self.warnings),
        }
