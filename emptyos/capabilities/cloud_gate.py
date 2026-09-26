"""The one check for code that picks a provider itself instead of going
through the capability chain: a settings-chosen or pinned provider,
``pinned_execute``, the agent loop, an app's own provider picker.

It asks the same two questions the chain asks — the monthly spend cap
(spend_cap.py) and whether this app may send the learner's notes to a cloud
provider (note_scope.py) — so a provider skipped by the chain cannot be
reached by naming it directly.
"""

from __future__ import annotations


def check(kernel, provider, capability: str = "think", app_id: str | None = None) -> str | None:
    """None when `provider` may run; otherwise "spend_cap" or "notes"."""
    cap = getattr(kernel, "spend_cap", None)
    if cap is not None and cap.blocks(capability, provider):
        return "spend_cap"
    scope = getattr(kernel, "note_scope", None)
    if scope is not None and scope.blocks(provider, app_id):
        return "notes"
    return None


def error(kernel, which: str, capability: str, where: str) -> RuntimeError:
    """The exception for a blocked call, with the prefix AI-offline handling
    recognises."""
    prefix = f"No available provider for capability '{capability}' ({where}): "
    if which == "spend_cap":
        from emptyos.capabilities.spend_cap import SpendCapReached

        return SpendCapReached(prefix + kernel.spend_cap.reason())
    from emptyos.capabilities.note_scope import NotesToCloudOff

    return NotesToCloudOff(prefix + kernel.note_scope.reason())
