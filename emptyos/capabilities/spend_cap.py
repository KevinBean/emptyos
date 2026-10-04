"""Monthly spend cap on paid model calls — one daemon, one ledger.

A hosted learner build runs one daemon per learner, so "this learner may spend
$X a month on AI" is "this daemon may". The cap is off until
``[spend] monthly_cap_usd`` (env ``EOS_SPEND_MONTHLY_CAP_USD``) is set above 0.
The hosted control plane sets that env per plan (``plans.plan_cap_usd``).

Two halves, both on existing machinery:

- **meter** — the cost of every paid call lands in the month's spend in the
  budget ledger (``emptyos.sdk.autopilot``: calendar-month window in UTC,
  rolled over lazily). ``think`` reports it on its ``think:executed`` event;
  every other paid provider (``speak``, ``listen``, ``draw``) reports it itself
  through ``charge()`` right after the call it paid for, so the chain, a pinned
  call and an app's own provider picker are all counted.
- **gate** — ``blocks(capability, provider)``, consulted for every capability:
  by the capability chain (``Capability.spend_cap``, set by the registry),
  through ``cloud_gate.check`` on the paths that pick a ``think`` provider
  themselves, and by each paid provider in ``check_paid_call()`` before it
  sends, which covers every path that reaches it. Once the month's spend
  reaches the cap, metered providers are skipped. A local, free or
  subscription provider can still answer; when none can, the call raises
  ``SpendCapReached`` so an app can say "monthly AI limit reached".

What is not covered:

- **Providers that declare ``metered = False``.** claude-cli, codex and
  claude-design bill a subscription; edge-tts and the stock-footage services
  are free. They add nothing to this spend and are never stopped by it.
- **A metered provider that cannot price its call** (an unknown model, a
  response without usage). It is still stopped at the cap but adds nothing to
  it, and the cap says so once per provider.
- **A cloud provider that declares nothing.** Metered is the default, so it is
  treated as paid and stopped at the cap (fail closed) — but it never calls
  ``charge()``, so it adds nothing and is never warned about. Today that is a
  rented ComfyUI, or voice-api / pronounce on a host you do not own; declare
  ``metered = False`` on one that costs nothing per call.
- **``think_compare`` spend.** It skips capped providers, but emits no cost
  event, so what it spends while under the cap is not counted.

The cap is checked before a call and the cost recorded after it, so calls
already running when the cap is reached still complete: the overshoot is the
cost of the calls in flight at that moment.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

# The ledger key: one daemon serves one user, so the daemon is the spender.
# Namespaced so it cannot collide with an app id, which the billing app uses
# as its actor key in the same ledger.
ACTOR_ID = "kernel:spend-cap"
_BUDGETS_FILE = Path("autopilot") / "budgets.json"


class SpendCapReached(RuntimeError):
    """No provider could run because the monthly spend cap kept the paid ones out.

    The message still starts with "No available provider for capability", so
    code that recognises an unavailable model (the server's AI-offline reply,
    apps that degrade gracefully) keeps working; apps that catch this class
    can say precisely why.
    """


def is_metered(provider) -> bool:
    """True for a provider whose calls cost money per call."""
    return bool(getattr(provider, "is_cloud", False)) and getattr(provider, "metered", True)


class SpendCap:
    def __init__(self, data_dir: Path, monthly_cap_usd: float,
                 warn: Callable[[str], None] | None = None,
                 find_provider: Callable[[str], object] | None = None):
        from emptyos.sdk import autopilot  # noqa: PLC0415 — lazy: the kernel imports this module

        self._autopilot = autopilot
        self.data_dir = Path(data_dir)
        self.monthly_cap_usd = float(monthly_cap_usd)
        self._warn = warn or (lambda message: None)
        # Name -> provider object, to tell a metered provider from a free one.
        self._find_provider = find_provider or (lambda name: None)
        self._warned_unpriced: set[str] = set()
        # Keeps the month's spend already recorded; only the cap changes.
        autopilot.set_budget(self.data_dir, ACTOR_ID, self.monthly_cap_usd)

    @classmethod
    def from_config(cls, config, warn: Callable[[str], None] | None = None,
                    find_provider: Callable[[str], object] | None = None) -> SpendCap | None:
        """A cap when ``spend.monthly_cap_usd`` is a positive number, else None."""
        raw = config.get("spend.monthly_cap_usd")
        if isinstance(raw, str) and not raw.strip():
            # A blank env override (compose's `${VAR:-}` on a container the
            # control plane did not set it for) is "not set", never "no cap":
            # fall back to the config file's own value.
            raw = config.get_section("spend").get("monthly_cap_usd")
        try:
            cap = float(raw) if raw not in (None, "") else 0.0
        except (TypeError, ValueError):
            return None
        return cls(config.data_dir, cap, warn=warn, find_provider=find_provider) if cap > 0 else None

    @staticmethod
    def clear_stale(data_dir: Path) -> bool:
        """With the cap switched off, clear a cap an earlier run left in the
        ledger, so nothing reports a limit that is no longer enforced.

        Reads the file first so a daemon that never had a cap imports nothing
        and writes nothing.
        """
        path = Path(data_dir) / _BUDGETS_FILE
        try:
            actors = json.loads(path.read_text(encoding="utf-8")).get("actors", {})
        except (OSError, ValueError, AttributeError):
            return False
        if not (actors.get(ACTOR_ID) or {}).get("monthly_cap_usd"):
            return False
        from emptyos.sdk import autopilot  # noqa: PLC0415

        autopilot.set_budget(Path(data_dir), ACTOR_ID, None)
        return True

    def allows(self) -> bool:
        """True while this month's spend is under the cap."""
        return self._autopilot.within_subscriber_quota(self.data_dir, ACTOR_ID)

    def blocks(self, capability: str, provider) -> bool:
        """True when `provider` must not run for `capability` now. Fail-open:
        a ledger that cannot be read allows the call."""
        if not is_metered(provider):
            return False
        try:
            return not self.allows()
        except Exception as e:
            self._warn(f"spend cap could not read its ledger, allowing: {e}")
            return False

    def reason(self) -> str:
        spent = self.status().get("spent_usd") or 0.0
        return f"monthly AI limit reached (${spent:.4f} of ${self.monthly_cap_usd:.2f})"

    def record(self, cost) -> bool:
        """Add a paid call's cost to the month; returns whether anything was added."""
        try:
            amount = float(cost)
        except (TypeError, ValueError):
            return False
        if amount <= 0:
            return False
        self._autopilot.record_spend(self.data_dir, ACTOR_ID, amount)
        return True

    def on_think_executed(self, event) -> None:
        """EventBus handler for ``think:executed``.

        A cloud call reporting no cost is recorded as nothing, which would let
        the cap never fill; say so once per provider rather than silently.
        """
        data = getattr(event, "data", None) or {}
        if self.record(data.get("cost")):
            return
        provider = str(data.get("provider") or "")
        if not provider or provider in self._warned_unpriced:
            return
        obj = self._find_provider(provider)
        if obj is not None and is_metered(obj):
            self._warned_unpriced.add(provider)
            self._warn(f"cloud think call via {provider!r} reported no cost; "
                       f"the spend cap cannot count it")

    def charge(self, capability: str, provider, cost) -> bool:
        """Record what a paid non-``think`` call cost; warn once per provider
        when it could not say (the cap would otherwise never fill).

        Only a metered provider is charged: the same class pointed at a
        self-hosted, OpenAI-compatible server is local, and pricing it at
        OpenAI's rates would fill the cap with money nobody spent."""
        if not is_metered(provider):
            return False
        if self.record(cost):
            return True
        name = str(getattr(provider, "name", "") or "")
        if name and name not in self._warned_unpriced:
            self._warned_unpriced.add(name)
            self._warn(f"paid {capability} call via {name!r} could not price itself; "
                       f"the spend cap cannot count it")
        return False

    def status(self) -> dict:
        return self._autopilot.budget_status(self.data_dir, ACTOR_ID)


# ── The provider-side half ──────────────────────────────────────────────────
#
# A paid provider meters and gates itself here, at the point it spends money,
# because not every path to it goes through the capability chain (pinned
# calls, reader's own voice picker, a direct ``provider.execute``). The kernel
# registers its cap once at boot; with no cap configured both calls are no-ops.

_active: SpendCap | None = None


def activate(cap: SpendCap | None) -> None:
    """Make *cap* the one the paid providers in this process report to."""
    global _active
    _active = cap


def check_paid_call(provider, capability: str) -> None:
    """Raise ``SpendCapReached`` when *provider* may not spend now.

    The chain has already skipped a capped provider; this catches the paths
    that call it directly."""
    cap = _active
    if cap is not None and cap.blocks(capability, provider):
        name = getattr(provider, "name", "?")
        raise SpendCapReached(
            f"No available provider for capability '{capability}' ({name}): {cap.reason()}"
        )


def charge(provider, capability: str, cost) -> None:
    """Add a paid call's cost (USD, or None when unknown) to the month. Never
    raises: the call has already been paid for and its result must reach the
    caller."""
    cap = _active
    if cap is None:
        return
    try:
        cap.charge(capability, provider, cost)
    except Exception as e:  # a ledger that cannot be written must not fail the call
        cap._warn(f"spend cap could not record a {capability} call: {e}")
