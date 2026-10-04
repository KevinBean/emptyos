"""Verb registry — one declaration per invokable app verb.

Spec: ``.claude/rules/verb-registry.md`` (and the plan that introduced this).

EmptyOS historically declared "which app methods are invokable" in six
independent places — voice intents, voice narration, assistant slash
commands, rooms per-agent allowlists, the MCP foundry verb list, and the
hand-maintained ``DEFAULT_ELIGIBLE_VERBS`` autopilot floor. The same verb
(``task.add``) appeared in three or four of them, and the autopilot floor
silently drifted whenever an app renamed a method.

This module is the single source of truth. An app declares::

    [[provides.verbs]]
    verb = "task.add"                 # <app>.<name>; app-half MUST equal the app id
    method = "voice_add_task"         # any public method (call_app uses getattr)
    summary = "Capture a quick task"
    args = { text = "string", due = "string?" }
    eligibility = "stable"            # stable | gated | never  -> autopilot floor
    surfaces = ["voice", "assistant", "mcp", "agent"]
    voice = { example = "add a task to call mom", always = true, card = "task-list", narrate = "narrate_after_add" }
    assistant = { slash = "/add", arg = "text", inverse = "reopen" }

The surface consumers (voice / assistant / mcp / rooms) and the autopilot
eligibility floor become *views* over this registry rather than parallel
declarations. ``AppLoader.get_verbs()`` aggregates + validates entries; this
module is pure (no kernel access) so it stays unit-testable.

**All-or-nothing per app.** If an app declares any ``[[provides.verbs]]``, the
registry is authoritative for that app across *every* surface. Each consumer
skips the app's legacy declaration when ``app_id in registry.apps()`` — never
fall back per-surface (that double-registers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# The four surfaces a verb can be exposed to. Membership is "offered to",
# not "granted" — the agent surface in particular is gated downstream by the
# per-agent server_actions allowlist, and the mcp surface by the eligibility
# floor + autopilot grants.
VALID_SURFACES: frozenset[str] = frozenset({"voice", "assistant", "mcp", "agent"})

# Eligibility is the autopilot floor classifier:
#   stable -> payload shape predictable enough to be autopilot-grant-eligible
#   gated  -> always reviewable; may be invoked but never auto-applied
#   never  -> free-form / irreversible / outbound; never grantable, ever
VALID_ELIGIBILITY: frozenset[str] = frozenset({"stable", "gated", "never"})

# App-half allows hyphens (``video-digest``); method-half is underscore-only,
# matching the rooms [DO:] parser regex (pending.py: ``[\w-]+\.\w+``).
_VERB_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.[a-z0-9_]+$")


@dataclass
class VerbEntry:
    """One validated ``[[provides.verbs]]`` declaration."""

    verb: str
    app_id: str
    method: str
    summary: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    eligibility: str = "gated"
    surfaces: tuple[str, ...] = ()
    voice: dict[str, Any] = field(default_factory=dict)
    assistant: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def app(self) -> str:
        return self.verb.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.verb.split(".", 1)[1]

    def method_for(self, surface: str) -> str:
        """The handler a given surface dispatches to.

        A verb's dispatch method is surface-specific: ``task.add`` runs ``add``
        for an agent ``[DO:]`` / MCP call but ``voice_add_task`` (a wrapper
        returning ``{say, card}``) for the voice surface. A surface sub-table
        may carry its own ``method``; otherwise the canonical ``method`` is used.
        """
        sub = self.voice if surface == "voice" else self.assistant if surface == "assistant" else None
        if isinstance(sub, dict):
            m = sub.get("method")
            if isinstance(m, str) and m:
                return m
        return self.method

    def dispatch_methods(self) -> list[str]:
        """Every distinct method this verb declares that must exist on the app
        instance — the canonical ``method`` plus any per-surface ``method``
        override and the voice ``narrate`` hook. Drives the drift checks.
        """
        seen: list[str] = []
        for m in (
            self.method,
            (self.voice or {}).get("method"),
            (self.voice or {}).get("narrate"),
            (self.assistant or {}).get("method"),
        ):
            if isinstance(m, str) and m and m not in seen:
                seen.append(m)
        return seen


def parse_verb_entry(
    raw: dict,
    app_id: str,
    *,
    allowed_app_halves: set[str] | None = None,
) -> tuple[VerbEntry | None, str | None]:
    """Validate one raw manifest entry. Returns ``(entry, None)`` on success or
    ``(None, error_message)`` on a hard reject. Callers warn + skip on error.

    The verb's app-half must equal the declaring app id (the cross-app guard) or
    one of ``allowed_app_halves`` — pass ``{app_id} | set(manifest.aliases)`` so
    a verb namespace that's a registered alias of the app (e.g. ``aura.*`` on the
    ``voice-assistant`` app) is permitted, while a foreign ``evil.method`` is not.
    Defaults to ``{app_id}`` when not provided.

    Validation is manifest-static only (no instance) — method existence is
    checked separately at instance-creation time, since lazy-loaded apps have
    no instance at boot.
    """
    allowed = allowed_app_halves or {app_id}
    if not isinstance(raw, dict):
        return None, "entry is not a table"

    verb = raw.get("verb")
    if not isinstance(verb, str) or not _VERB_RE.match(verb):
        return None, f"invalid verb {verb!r} (must match <app>.<method>)"
    if verb.split(".", 1)[0] not in allowed:
        return None, (
            f"verb {verb!r} app-half not in {sorted(allowed)} "
            f"(cross-app declarations are forbidden; add an alias to opt in)"
        )

    method = raw.get("method")
    if not isinstance(method, str) or not method:
        return None, f"verb {verb!r} is missing a 'method'"

    eligibility = raw.get("eligibility", "gated")
    if eligibility not in VALID_ELIGIBILITY:
        return None, (
            f"verb {verb!r} has invalid eligibility {eligibility!r} "
            f"(expected one of {sorted(VALID_ELIGIBILITY)})"
        )

    surfaces = raw.get("surfaces") or []
    if not isinstance(surfaces, list) or any(
        not isinstance(s, str) or s not in VALID_SURFACES for s in surfaces
    ):
        return None, (
            f"verb {verb!r} has invalid surfaces {surfaces!r} "
            f"(allowed: {sorted(VALID_SURFACES)})"
        )

    args = raw.get("args") or {}
    if not isinstance(args, dict):
        return None, f"verb {verb!r} 'args' must be a table"

    assistant = raw.get("assistant") or {}
    if not isinstance(assistant, dict):
        return None, f"verb {verb!r} 'assistant' must be a table"
    a_arg = assistant.get("arg")
    if a_arg is not None and a_arg not in args:
        return None, (
            f"verb {verb!r} assistant.arg {a_arg!r} is not a declared arg "
            f"(keys: {sorted(args)})"
        )

    voice = raw.get("voice") or {}
    if not isinstance(voice, dict):
        return None, f"verb {verb!r} 'voice' must be a table"

    return (
        VerbEntry(
            verb=verb,
            app_id=app_id,
            method=method,
            summary=str(raw.get("summary") or ""),
            args=args,
            eligibility=eligibility,
            surfaces=tuple(surfaces),
            voice=voice,
            assistant=assistant,
            raw=raw,
        ),
        None,
    )


class VerbRegistry:
    """An immutable view over every validated verb in the system.

    Built by ``AppLoader.get_verbs()``. Consumers filter it per surface; the
    autopilot floor reads ``eligible_verbs()``.
    """

    def __init__(self, entries: list[VerbEntry]):
        self._entries: list[VerbEntry] = list(entries)
        # Last-wins on duplicate verbs (a duplicate is a manifest bug; the
        # loader warns separately). Keeps lookups deterministic.
        self._by_verb: dict[str, VerbEntry] = {e.verb: e for e in self._entries}

    def __iter__(self):
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, verb: str) -> VerbEntry | None:
        return self._by_verb.get(verb)

    def apps(self) -> set[str]:
        """App ids that have declared at least one verb — the all-or-nothing
        migration gate each consumer checks before reading legacy declarations.
        """
        return {e.app_id for e in self._entries}

    def for_surface(self, surface: str) -> list[VerbEntry]:
        """Every verb offered to the named surface (voice/assistant/mcp/agent)."""
        return [e for e in self._entries if surface in e.surfaces]

    def apps_for_surface(self, surface: str) -> set[str]:
        """App ids that declare at least one verb on ``surface`` — the
        all-or-nothing skip set each surface consumer uses to ignore an app's
        legacy declaration once it's migrated. See .claude/rules/verb-registry.md.
        """
        return {e.app_id for e in self._entries if surface in e.surfaces}

    def eligible_verbs(self) -> set[str]:
        """Verbs whose declared eligibility is ``stable`` — the registry-derived
        autopilot floor. Recomputed fresh every call; never persisted, so a
        ``stable -> gated`` flip revokes correctly.
        """
        return {e.verb for e in self._entries if e.eligibility == "stable"}

    def menu(self, surface: str | None = None) -> list[dict]:
        """One-line-per-verb listing for picklists / debug sweeps."""
        rows = self.for_surface(surface) if surface else self._entries
        return [
            {
                "verb": e.verb,
                "app": e.app_id,
                "method": e.method,
                "summary": e.summary,
                "eligibility": e.eligibility,
                "surfaces": list(e.surfaces),
            }
            for e in rows
        ]


def merged_voice_entries(loader) -> list[dict]:
    """The one merged view of every voice-invokable verb in the system.

    Registry ``for_surface("voice")`` entries + legacy
    ``[[contributes.voice-assistant.intent]]`` contributions from apps not
    yet migrated, normalized to the legacy intent dict shape consumers
    already speak: ``{verb, method, example, description, args, always,
    card, narrate, _app_id}``. Registry wins per (app, voice) — an app with
    ANY registry voice verb has its legacy voice intents skipped
    (all-or-nothing, see .claude/rules/verb-registry.md); duplicate verbs
    keep the first occurrence.

    ``loader`` is duck-typed (needs ``.get_verbs()`` + ``.get_contributions()``)
    and each half fails soft, so a broken registry read still yields the
    legacy half and vice versa. Consumers: voice-assistant's intent table,
    the telegram bridge's phone allowlist.
    """
    out: list[dict] = []
    seen_verbs: set[str] = set()
    migrated: set[str] = set()
    try:
        registry = loader.get_verbs()
        migrated = registry.apps_for_surface("voice")
        for e in registry.for_surface("voice"):
            v = e.voice or {}
            out.append({
                "verb": e.verb,
                "method": e.method_for("voice"),
                "example": v.get("example") or "",
                "description": v.get("description") or e.summary or "",
                "args": e.args or {},
                "always": bool(v.get("always")),
                "card": v.get("card"),
                "narrate": v.get("narrate") or "",
                "_app_id": e.app_id,
            })
            seen_verbs.add(e.verb)
    except Exception:
        pass
    try:
        for entry in loader.get_contributions("voice-assistant", "intent"):
            verb = entry.get("verb")
            if not verb or verb in seen_verbs or entry.get("_app_id") in migrated:
                continue
            out.append(dict(entry))
            seen_verbs.add(verb)
    except Exception:
        pass
    return out
