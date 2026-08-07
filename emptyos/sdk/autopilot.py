"""Autopilot grants — per-actor + per-verb + per-scope explicit trust.

Spec: ``.claude/rules/autopilot-grants.md``. This is the *store*; consumers
(rooms gate, voice-assistant gate) call ``match()`` before persisting a
pending action to decide whether to skip the gate and auto-apply.

Grants live at ``data/autopilot/grants.json``; eligibility policy at
``data/autopilot/policy.json`` (which verbs may ever be granted). Both files
are per-machine, gitignored, and shared across every grant consumer in the
daemon. Eligibility is a *floor* — no grant can override a non-eligible verb.

Today's first consumer is voice-assistant's session toggle ("auto-accept for
an hour"). The rooms consumer (per the rule's main spec) lands on its own
timeline; both read the same files.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

GRANTS_FILE = "grants.json"
HOLDS_FILE = "holds.json"
BUDGETS_FILE = "budgets.json"
POLICY_FILE = "policy.json"
AUDIT_FILE = "audit.jsonl"
AUDIT_KEY_FILE = "audit.key"
# Genesis "previous HMAC" — 64 zeros so the first entry has a well-defined prev.
GENESIS_PREV = "0" * 64

# Default eligibility list — verbs whose payload shape is stable enough that
# the user no longer benefits from per-instance review. Free-form content
# verbs (note.create, rooms.write_note), outbound external messages
# (notifications.send), and irreversible publishes (publish.deploy) are
# DELIBERATELY ABSENT — they are never autopilot-eligible per the rule.
DEFAULT_ELIGIBLE_VERBS: list[str] = [
    "task.add",
    "task.list_today",
    "task.list_due",
    "task.list_recent",
    "capture.add",
    # kb.tag removed 2026-06-07: it was a phantom — listed here but implemented
    # nowhere (no `tag`/`voice_tag` method, no voice intent). The unified verb
    # registry surfaced it (no app declares it), so it leaves the floor when the
    # registry-derived floor is active. Re-add only alongside a real impl.
    "journal.add_entry",
    "video-digest.queue",
    "radio.play",
    "radio.skip",
    "guideline.show",
    "guideline.random",
    "rooms.list",
    "rooms.open",
    "reader.open",
    "aura.remember",
    "aura.forget",
    "aura.recall",
    # Rooms team mode — stable-payload coordination verbs (id + short string).
    # A lead managing its own room's shared task list is the canonical
    # "predictable enough to skip per-action review" case. Worker [DO:] to
    # OTHER apps stays gated. See .claude/rules/autopilot-grants.md.
    "rooms.team_add_task",
    "rooms.team_assign",
    "rooms.team_set_status",
]


def _store_root(data_dir: Path) -> Path:
    p = Path(data_dir) / "autopilot"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_policy(data_dir: Path) -> dict:
    """Return the eligibility policy. Seeds defaults on first read, and
    merges any new DEFAULT_ELIGIBLE_VERBS into existing policies so that
    shipping a new always-eligible verb doesn't require operator action.
    """
    root = _store_root(data_dir)
    pol_path = root / POLICY_FILE
    pol = _load_json(pol_path, None)
    if pol is None:
        pol = {"eligible_verbs": list(DEFAULT_ELIGIBLE_VERBS)}
        _write_json(pol_path, pol)
        return pol
    existing = list(pol.get("eligible_verbs") or [])
    missing = [v for v in DEFAULT_ELIGIBLE_VERBS if v not in existing]
    if missing:
        pol["eligible_verbs"] = existing + missing
        _write_json(pol_path, pol)
    return pol


def is_eligible(data_dir: Path, verb: str, *, eligible: set[str] | None = None) -> bool:
    """Hard floor — eligibility cannot be overridden by a grant.

    When ``eligible`` is provided (the registry-derived effective set computed
    by ``Kernel.autopilot_eligible_set()``), it is authoritative — this is the
    drift-resistant path: the set is recomputed fresh each call from
    ``[[provides.verbs]] eligibility="stable"`` and never persisted, so a
    ``stable -> gated`` flip revokes immediately.

    When ``eligible`` is ``None`` (legacy call sites, or the verb-registry
    feature flag is off), fall back to the persisted ``policy.json``
    ``eligible_verbs`` list seeded from ``DEFAULT_ELIGIBLE_VERBS``. This keeps
    today's behaviour byte-for-byte when nothing injects a set.
    """
    if eligible is not None:
        return verb in eligible
    pol = load_policy(data_dir)
    return verb in (pol.get("eligible_verbs") or [])


def _operator_overrides(data_dir: Path) -> tuple[set[str], set[str]]:
    """Operator-authored eligibility deltas from ``policy.json`` — distinct from
    the auto-seeded ``eligible_verbs`` legacy list. ``operator_eligible`` adds
    verbs on top of the registry-derived floor; ``operator_removed`` subtracts
    (subtraction wins, so an operator can pull a verb the registry re-adds).
    Both default empty and are never auto-seeded.
    """
    pol = load_policy(data_dir)
    return (
        set(pol.get("operator_eligible") or []),
        set(pol.get("operator_removed") or []),
    )


def effective_eligible(data_dir: Path, derived: set[str] | list[str]) -> set[str]:
    """Compute the effective autopilot floor from a registry-derived set.

    ``effective = (derived ∪ operator_eligible) − operator_removed``. The caller
    (``Kernel.autopilot_eligible_set()``) passes the result as ``eligible=`` to
    ``match`` / ``is_eligible`` / ``save_grant``. Recomputed fresh, never
    persisted — that is what makes a ``stable -> gated`` manifest flip revoke.
    """
    op_el, op_rm = _operator_overrides(data_dir)
    return (set(derived) | op_el) - op_rm


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    """Parse an ISO timestamp into an aware UTC datetime, or None.

    Naive timestamps are assumed UTC — every writer in this module uses
    ``_now_iso()`` (aware), so this only matters for hand-edited records.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_expired(grant: dict) -> bool:
    exp = _parse_iso(grant.get("expires_at"))
    return exp is not None and exp <= datetime.now(timezone.utc)


def load_grants(data_dir: Path) -> list[dict]:
    """Return all non-expired grants. Expired ones are reaped on read."""
    root = _store_root(data_dir)
    raw = _load_json(root / GRANTS_FILE, [])
    if not isinstance(raw, list):
        return []
    alive = [g for g in raw if not _is_expired(g)]
    if len(alive) != len(raw):
        _write_json(root / GRANTS_FILE, alive)
    return alive


def save_grant(
    data_dir: Path,
    *,
    actor_type: str,
    actor_id: str,
    verb_pattern: str,
    scope: str,
    ttl_seconds: int | None = None,
    rationale: str = "",
    eligible: set[str] | None = None,
) -> dict:
    """Persist a new grant. Refuses non-eligible verbs (eligibility floor).

    ``verb_pattern`` accepts an exact verb (``task.add``) or a namespace
    wildcard (``task.*``). Bare ``*`` is forbidden — every grant must name
    at least an app.

    ``eligible`` mirrors ``match`` / ``is_eligible``: when provided it is the
    registry-derived effective floor, so the save-time check agrees with the
    fire-time check. When ``None``, the legacy ``policy.json`` floor applies.
    """
    if not verb_pattern or verb_pattern == "*":
        raise ValueError("verb_pattern is required and may not be bare '*'")

    # Cross-app patterns (``*.send``) are forbidden — every grant names exactly
    # one app. Reject before consulting the policy so the error is precise.
    if "." not in verb_pattern or verb_pattern.split(".", 1)[0] in ("", "*"):
        raise ValueError(
            f"verb_pattern {verb_pattern!r} must name an app — "
            f"cross-app patterns like '*.send' are not allowed"
        )

    # Eligibility floor: per-call ``match()`` re-checks ``is_eligible(verb)``
    # so a glob pattern can't sneak in non-eligible verbs at fire time. We
    # also validate at save-time so a dead-on-arrival grant is rejected loudly:
    #   - exact verb: must itself be in eligible_verbs
    #   - glob:       at least one eligible verb must match the pattern
    if "*" not in verb_pattern:
        if not is_eligible(data_dir, verb_pattern, eligible=eligible):
            raise ValueError(f"verb {verb_pattern!r} is not autopilot-eligible")
    else:
        eligible_set = (
            eligible
            if eligible is not None
            else set(load_policy(data_dir).get("eligible_verbs") or [])
        )
        if not any(_verb_matches(verb_pattern, v) for v in eligible_set):
            raise ValueError(
                f"verb_pattern {verb_pattern!r} matches no eligible verbs "
                f"(extend policy.json first)"
            )

    grant = {
        "id": "grant-" + uuid.uuid4().hex[:10],
        "actor": {"type": actor_type, "id": actor_id},
        "verb_pattern": verb_pattern,
        "scope": scope,
        "created_at": _now_iso(),
        "expires_at": (
            (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
            if ttl_seconds
            else None
        ),
        "granted_by": "user",
        "rationale": rationale,
    }
    grants = load_grants(data_dir)
    grants.append(grant)
    _write_json(_store_root(data_dir) / GRANTS_FILE, grants)
    return grant


def revoke_grant(data_dir: Path, grant_id: str) -> bool:
    """Remove a grant by id. Returns True if a grant was removed."""
    grants = load_grants(data_dir)
    kept = [g for g in grants if g.get("id") != grant_id]
    if len(kept) == len(grants):
        return False
    _write_json(_store_root(data_dir) / GRANTS_FILE, kept)
    return True


def revoke_scope(data_dir: Path, scope: str) -> int:
    """Remove every grant whose ``scope`` matches. Returns count removed."""
    grants = load_grants(data_dir)
    kept = [g for g in grants if g.get("scope") != scope]
    n = len(grants) - len(kept)
    if n:
        _write_json(_store_root(data_dir) / GRANTS_FILE, kept)
    return n


def replace_namespace_grants(
    data_dir: Path,
    *,
    scope: str,
    actors: Sequence[tuple[str, str]],
    eligible: set[str],
    ttl_seconds: int,
    rationale: str = "",
) -> list[dict]:
    """Clean-toggle a scope: revoke its grants, then re-issue one ``<ns>.*``
    grant per (actor × eligible namespace). Returns the grants issued.

    This is the "⚡ auto-accept" toggle body — one click covering *every*
    eligible verb for *every* actor in a scope. ``actors`` is a sequence of
    ``(actor_type, actor_id)``; an empty ``actor_id`` means "any actor of this
    type", which is what ``match()`` reads as the auto-accept-everyone pattern.
    Namespaces are derived from ``eligible``, so the floor decides the reach —
    a namespace whose verbs are all non-eligible raises in ``save_grant`` and is
    skipped rather than aborting the sweep.

    **Destructive first step, by design.** ``revoke_scope`` runs before any
    grant is issued so a re-toggle replaces rather than stacks. Callers that
    only want to revoke should call :func:`revoke_scope` directly instead of
    passing an empty ``actors``.

    Not for: single-verb grants (call :func:`save_grant`), or holds (the
    pause-auto sibling — one caller today, so it stays inline in rooms).
    """
    namespaces = sorted({v.split(".", 1)[0] for v in eligible})
    revoke_scope(data_dir, scope)
    issued: list[dict] = []
    for actor_type, actor_id in actors:
        for ns in namespaces:
            try:
                issued.append(
                    save_grant(
                        data_dir,
                        actor_type=actor_type,
                        actor_id=actor_id,
                        verb_pattern=f"{ns}.*",
                        scope=scope,
                        ttl_seconds=ttl_seconds,
                        rationale=rationale,
                        eligible=eligible,
                    )
                )
            except ValueError:
                # Namespace wildcard matched no eligible verb — skip it.
                continue
    return issued


def _verb_matches(pattern: str, verb: str) -> bool:
    """Pattern grammar: ``<app>.<verb_glob>``.

    - ``<app>`` half must be exact — no cross-app patterns like ``*.send``
      (keeps the "every grant names exactly one app" spec invariant).
    - ``<verb_glob>`` half may be exact (``add``), bare wildcard (``*``),
      or any ``fnmatch`` pattern (``send_*``, ``list_*``, ``*_memory``).

    Examples that match::

        _verb_matches("task.add",        "task.add")        # exact
        _verb_matches("task.*",          "task.add")        # namespace
        _verb_matches("email.send_*",    "email.send_html") # verb-prefix glob
        _verb_matches("aura.*_memory",   "aura.clear_memory")

    Examples that don't::

        _verb_matches("task.add",        "task.delete")     # different verb
        _verb_matches("email.*",         "task.add")        # different app
        _verb_matches("*.send",          "email.send")      # cross-app forbidden
    """
    if pattern == verb:
        return True
    if "." not in pattern or "." not in verb:
        return False
    p_app, p_verb = pattern.split(".", 1)
    v_app, v_verb = verb.split(".", 1)
    if p_app != v_app:
        return False
    return fnmatch.fnmatchcase(v_verb, p_verb)


def match(
    data_dir: Path,
    *,
    actor_type: str,
    actor_id: str,
    verb: str,
    scope_candidates: list[str],
    eligible: set[str] | None = None,
) -> dict | None:
    """Return the active grant covering this (actor, verb, scope), or None.

    ``eligible`` is the registry-derived effective floor (see ``is_eligible``);
    when ``None`` the legacy ``policy.json`` floor applies. Threading it here is
    what makes the drift-killer real — a verb whose manifest eligibility flipped
    to ``gated`` drops out of ``eligible`` and stops matching even if a grant
    still names it.

    A match requires:
      - eligibility floor (``is_eligible(verb) == True``)
      - actor.type matches
      - actor.id matches (or actor.id is empty in the grant, meaning any
        actor of that type — used for "auto-accept everyone in this session")
      - verb_pattern matches verb exactly or via ``<ns>.*``
      - grant.scope is in scope_candidates (e.g. ``["session:room-x",
        "room:room-x", "global"]``)
      - grant has not expired (reaped on read by ``load_grants``)
    """
    if not is_eligible(data_dir, verb, eligible=eligible):
        return None
    for g in load_grants(data_dir):
        actor = g.get("actor") or {}
        if actor.get("type") != actor_type:
            continue
        gid = actor.get("id") or ""
        if gid and gid != actor_id:
            continue
        if not _verb_matches(g.get("verb_pattern") or "", verb):
            continue
        if g.get("scope") not in scope_candidates:
            continue
        return g
    return None


# ── Holds — the inverse of a grant (pivot 2026-06-07) ───────────────────
#
# Post-pivot the default for a `stable`/reversible verb is AUTO-RUN, not
# review. A **hold** is how the user re-gates one: "pause auto for this
# stable verb in this scope right now." Same (actor, verb_pattern, scope)
# shape as a grant, opposite effect. No eligibility check — you may hold
# any verb (holding a `gated`/`never` verb is a harmless no-op since those
# already gate). Holds win over grants: an explicit hold gates even if a
# grant also names the verb.


def load_holds(data_dir: Path) -> list[dict]:
    """Return all non-expired holds. Expired ones are reaped on read."""
    root = _store_root(data_dir)
    raw = _load_json(root / HOLDS_FILE, [])
    if not isinstance(raw, list):
        return []
    alive = [h for h in raw if not _is_expired(h)]
    if len(alive) != len(raw):
        _write_json(root / HOLDS_FILE, alive)
    return alive


def save_hold(
    data_dir: Path,
    *,
    actor_type: str,
    actor_id: str,
    verb_pattern: str,
    scope: str,
    ttl_seconds: int | None = None,
    rationale: str = "",
) -> dict:
    """Persist a hold (re-gate a normally-auto stable verb). Same pattern
    grammar as ``save_grant`` (``<app>.<verb_glob>``, no bare ``*``, no
    cross-app), but NO eligibility floor — a hold only ever *adds* friction,
    so it can never widen access."""
    if not verb_pattern or verb_pattern == "*":
        raise ValueError("verb_pattern is required and may not be bare '*'")
    if "." not in verb_pattern or verb_pattern.split(".", 1)[0] in ("", "*"):
        raise ValueError(
            f"verb_pattern {verb_pattern!r} must name an app — "
            f"cross-app patterns like '*.send' are not allowed"
        )
    hold = {
        "id": "hold-" + uuid.uuid4().hex[:10],
        "actor": {"type": actor_type, "id": actor_id},
        "verb_pattern": verb_pattern,
        "scope": scope,
        "created_at": _now_iso(),
        "expires_at": (
            (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
            if ttl_seconds
            else None
        ),
        "created_by": "user",
        "rationale": rationale,
    }
    holds = load_holds(data_dir)
    holds.append(hold)
    _write_json(_store_root(data_dir) / HOLDS_FILE, holds)
    return hold


def revoke_hold(data_dir: Path, hold_id: str) -> bool:
    """Remove a hold by id (resume auto). Returns True if one was removed."""
    holds = load_holds(data_dir)
    kept = [h for h in holds if h.get("id") != hold_id]
    if len(kept) == len(holds):
        return False
    _write_json(_store_root(data_dir) / HOLDS_FILE, kept)
    return True


def revoke_hold_scope(data_dir: Path, scope: str) -> int:
    """Remove every hold whose ``scope`` matches (resume auto for a whole
    scope). Returns count removed. The hold-side mirror of ``revoke_scope``."""
    holds = load_holds(data_dir)
    kept = [h for h in holds if h.get("scope") != scope]
    n = len(holds) - len(kept)
    if n:
        _write_json(_store_root(data_dir) / HOLDS_FILE, kept)
    return n


def is_held(
    data_dir: Path,
    *,
    actor_type: str,
    actor_id: str,
    verb: str,
    scope_candidates: list[str],
) -> dict | None:
    """Return the active hold covering this (actor, verb, scope), or None.

    Same matching semantics as ``match`` (actor type + id-or-any, verb
    pattern, scope membership) minus the eligibility floor.
    """
    for h in load_holds(data_dir):
        actor = h.get("actor") or {}
        if actor.get("type") != actor_type:
            continue
        hid = actor.get("id") or ""
        if hid and hid != actor_id:
            continue
        if not _verb_matches(h.get("verb_pattern") or "", verb):
            continue
        if h.get("scope") not in scope_candidates:
            continue
        return h
    return None


# ── Per-actor budget caps — the runaway-cost guardrail ──────────────────
#
# Borrow #3 from the Paperclip mining (2026-06-09): the one primitive EmptyOS
# had no equivalent for. Grants/holds + the eligibility floor decide *whether*
# a verb may auto-run; a budget cap is the orthogonal ceiling on *how much* an
# actor may spend doing so. An autonomously running loop (staff, fix-drain,
# dogfood) can otherwise burn cloud spend without bound — this trips it to
# `gate` (a soft hold) once the actor crosses its monthly cap.
#
# Store: data/autopilot/budgets.json ->
#   {"actors": {<actor_id>: {monthly_cap_usd, spent_usd, window_start}}}
# A `None`/absent cap means "track spend but never gate". The window is a
# calendar month (UTC); spend rolls to zero lazily on the first read/record in
# a new month, so no cron is needed. Spend is fed by `record_spend()` — the
# attribution wiring (who pays for which cloud call) is the documented
# follow-up; this module owns the ledger + the decision, not the metering.


def _blank_budget() -> dict:
    """A fresh, uncapped budget record for an actor seen for the first time."""
    return {"monthly_cap_usd": None, "spent_usd": 0.0, "window_start": _now_iso()}


def _month_key(iso: str | None) -> str:
    """Calendar-month bucket key (UTC ``YYYY-MM``) for a stored window_start.
    Empty/unparseable -> current month, so a fresh record never looks stale."""
    if iso:
        try:
            dt = datetime.fromisoformat(iso)
            return dt.strftime("%Y-%m")
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _rolled(rec: dict) -> dict:
    """Return ``rec`` with its window rolled to the current month if stale
    (spend reset to 0, window_start bumped). Pure — caller persists."""
    now_key = datetime.now(timezone.utc).strftime("%Y-%m")
    if _month_key(rec.get("window_start")) != now_key:
        return {
            "monthly_cap_usd": rec.get("monthly_cap_usd"),
            "spent_usd": 0.0,
            "window_start": _now_iso(),
        }
    return rec


def _load_budgets(data_dir: Path) -> dict:
    raw = _load_json(_store_root(data_dir) / BUDGETS_FILE, {})
    if not isinstance(raw, dict):
        return {"actors": {}}
    raw.setdefault("actors", {})
    return raw


def set_budget(data_dir: Path, actor_id: str, monthly_cap_usd: float | None) -> dict:
    """Set (or clear, with ``None``/``<=0``) an actor's monthly USD cap.

    Preserves the actor's accrued ``spent_usd`` for the current window — raising
    or lowering a cap mid-month doesn't reset what was already spent. Returns the
    actor's budget record.
    """
    if not actor_id:
        raise ValueError("actor_id is required")
    budgets = _load_budgets(data_dir)
    actors = budgets["actors"]
    rec = _rolled(actors.get(actor_id) or _blank_budget())
    cap = None if (monthly_cap_usd is None or monthly_cap_usd <= 0) else float(monthly_cap_usd)
    rec = {**rec, "monthly_cap_usd": cap}
    actors[actor_id] = rec
    _write_json(_store_root(data_dir) / BUDGETS_FILE, budgets)
    return rec


def record_spend(data_dir: Path, actor_id: str, amount_usd: float) -> dict:
    """Add ``amount_usd`` to an actor's current-window spend (rolling the window
    first if a new month started). Tracks spend even for actors with no cap, so
    a cap set later in the month sees real history. Returns the updated record.
    Non-positive amounts are a no-op read."""
    if not actor_id:
        raise ValueError("actor_id is required")
    budgets = _load_budgets(data_dir)
    actors = budgets["actors"]
    rec = _rolled(actors.get(actor_id) or _blank_budget())
    if amount_usd and amount_usd > 0:
        rec = {**rec, "spent_usd": round(float(rec.get("spent_usd") or 0.0)
                                         + float(amount_usd), 6)}
    actors[actor_id] = rec
    _write_json(_store_root(data_dir) / BUDGETS_FILE, budgets)
    return rec


def within_budget(data_dir: Path, actor_id: str) -> bool:
    """True iff the actor may still auto-spend this month: no record, no cap,
    or current-window spend is strictly below the cap. Window rolled on read."""
    rec = _load_budgets(data_dir)["actors"].get(actor_id)
    if not rec:
        return True
    rec = _rolled(rec)
    cap = rec.get("monthly_cap_usd")
    if cap is None or cap <= 0:
        return True
    return float(rec.get("spent_usd") or 0.0) < float(cap)


def budget_status(data_dir: Path, actor_id: str) -> dict:
    """One actor's budget snapshot for an audit/hub panel. ``remaining_usd`` and
    ``over`` are ``None``/``False`` when no cap is set."""
    rec = _rolled(_load_budgets(data_dir)["actors"].get(actor_id) or _blank_budget())
    cap = rec.get("monthly_cap_usd")
    spent = float(rec.get("spent_usd") or 0.0)
    return {
        "actor_id": actor_id,
        "monthly_cap_usd": cap,
        "spent_usd": spent,
        "remaining_usd": (None if cap is None else round(float(cap) - spent, 6)),
        "window_start": rec.get("window_start"),
        "over": (cap is not None and cap > 0 and spent >= float(cap)),
    }


def all_budgets(data_dir: Path) -> list[dict]:
    """Budget snapshots for every tracked actor (panel feed)."""
    return [budget_status(data_dir, aid)
            for aid in _load_budgets(data_dir)["actors"].keys()]


def decide(
    data_dir: Path,
    *,
    actor_type: str,
    actor_id: str,
    verb: str,
    scope_candidates: list[str],
    eligible: set[str] | None = None,
    auto_stable: bool = False,
    enforce_budget: bool = False,
) -> dict:
    """The single auto-vs-gate decision (pivot 2026-06-07).

    Returns ``{"action": "auto"|"gate", "reason": str, "grant_id": str|None,
    "hold_id": str|None}``.

    Logic, in order:

    1. **Stable-default** (only when ``auto_stable`` is True): if the verb is
       autopilot-eligible (``stable``), AUTO-RUN unless an active **hold**
       re-gates it. This is the post-pivot default — no grant needed.
    2. **Grant**: otherwise, a matching grant (covering a ``gated`` verb in
       scope) auto-runs it. ``never`` verbs never match (eligibility floor in
       ``match``), and the caller is expected to special-case free-form /
       irreversible verbs *before* calling ``decide`` anyway.
    3. **Gate**: no stable-default, no grant → human review.
    4. **Budget ceiling** (only when ``enforce_budget`` is True): any ``auto``
       outcome from 1–2 is overridden to ``gate`` (reason ``over-budget``) when
       the actor has crossed its monthly cap. Orthogonal to grants/holds — a
       grant says *may run*, the budget says *can still afford to*.

    When both ``auto_stable`` and ``enforce_budget`` are False the function is
    byte-for-byte the pre-pivot behaviour (grant-or-gate), so both pivots ship
    dark until their flags flip.
    """
    if auto_stable and is_eligible(data_dir, verb, eligible=eligible):
        held = is_held(
            data_dir, actor_type=actor_type, actor_id=actor_id,
            verb=verb, scope_candidates=scope_candidates,
        )
        if held is not None:
            result = {"action": "gate", "reason": "held",
                      "grant_id": None, "hold_id": held["id"]}
        else:
            result = {"action": "auto", "reason": "stable-default",
                      "grant_id": None, "hold_id": None}
    else:
        g = match(
            data_dir, actor_type=actor_type, actor_id=actor_id,
            verb=verb, scope_candidates=scope_candidates, eligible=eligible,
        )
        if g is not None:
            result = {"action": "auto", "reason": "grant",
                      "grant_id": g["id"], "hold_id": None}
        else:
            result = {"action": "gate", "reason": "no-grant",
                      "grant_id": None, "hold_id": None}

    if (enforce_budget and result["action"] == "auto"
            and not within_budget(data_dir, actor_id)):
        return {"action": "gate", "reason": "over-budget",
                "grant_id": result.get("grant_id"), "hold_id": None}
    return result


# ── Tamper-evident audit chain ──────────────────────────────────────────
#
# Every action the daemon auto-applies (without a per-instance Apply click)
# lands here. Each entry's HMAC-SHA256 is computed over the previous entry's
# HMAC plus the canonical JSON of the new entry, producing a git-commit-style
# chain. Tampering with any past line breaks the chain from there forward;
# truncation is detected the same way. `verify_audit` walks the file and
# reports the indices of broken lines.
#
# The key lives at `data/autopilot/audit.key` (32 random bytes, mode 0600 on
# POSIX). It's auto-generated on first append. Rotating the key starts a new
# chain segment — verification reports a break at the rotation point, which
# is the intended signal (the operator knows when they rotated).
#
# Args are stored inline (truncated at ~1000 chars per entry) — single-user
# system, no privacy concern, and a greppable audit trail is the whole point.


def _canonical(payload: dict) -> bytes:
    """Canonical JSON encoding used for HMAC input. Sorted keys + tight
    separators so the writer and verifier reproduce identical bytes."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _audit_key(data_dir: Path) -> bytes:
    """Return the chain's HMAC key, lazily generating + persisting on first use."""
    key_path = _store_root(data_dir) / AUDIT_KEY_FILE
    if key_path.exists():
        return key_path.read_bytes()
    key = secrets.token_bytes(32)
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass  # Windows; key file ACL not adjusted here
    return key


def _last_hmac(audit_path: Path) -> str:
    """Read the last entry's hmac from the audit log, or GENESIS_PREV if empty.

    Walks the file backwards a block at a time so we don't slurp a huge log
    just to read the tail.
    """
    if not audit_path.exists():
        return GENESIS_PREV
    try:
        with audit_path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return GENESIS_PREV
            block = 4096
            buf = b""
            pos = size
            while pos > 0:
                step = min(block, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                if b"\n" in buf.rstrip(b"\n"):
                    break
            last_line = buf.strip().split(b"\n")[-1]
            entry = json.loads(last_line.decode("utf-8"))
            return entry.get("hmac") or GENESIS_PREV
    except Exception:
        return GENESIS_PREV


def append_audit(
    data_dir: Path,
    *,
    actor: dict,
    app: str,
    method: str,
    args: dict,
    grant_id: str | None,
    ok: bool,
    error: str | None = None,
) -> str:
    """Append one HMAC-chained audit entry. Returns the new entry's hmac.

    Failures inside this function should be swallowed by callers — audit
    logging must never break the action path. The chain self-detects gaps
    on next verify.
    """
    root = _store_root(data_dir)
    audit_path = root / AUDIT_FILE
    key = _audit_key(data_dir)
    prev = _last_hmac(audit_path)

    args_repr = json.dumps(args or {}, ensure_ascii=False, default=str)
    if len(args_repr) > 1000:
        args_repr = args_repr[:1000] + "...[truncated]"

    entry = {
        "ts": _now_iso(),
        "actor": actor or {},
        "app": app,
        "method": method,
        "args": args_repr,
        "grant_id": grant_id,
        "ok": bool(ok),
        "error": error,
        "prev": prev,
    }
    sig = hmac.new(
        key, prev.encode("ascii") + _canonical(entry), hashlib.sha256,
    ).hexdigest()
    entry["hmac"] = sig

    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(line)
    return sig


def verify_audit(data_dir: Path) -> dict:
    """Walk audit.jsonl, recompute the HMAC chain, return integrity status.

    Returns::

        {
            "ok": bool,           # True iff no tampering detected
            "lines_checked": int, # number of non-blank lines walked
            "tampered": [int...], # 0-indexed line numbers that failed
            "reason": str | None, # only on read errors
        }

    Tampered lines do NOT abort the walk — every line is checked. ``prev``
    chain breaks (line N's ``prev`` doesn't match line N-1's hmac) and HMAC
    recomputation mismatches both count as tampered.
    """
    audit_path = _store_root(data_dir) / AUDIT_FILE
    if not audit_path.exists():
        return {"ok": True, "lines_checked": 0, "tampered": [], "reason": None}
    try:
        raw_lines = audit_path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return {
            "ok": False, "lines_checked": 0, "tampered": [],
            "reason": f"read failed: {e!s:.200s}",
        }
    key = _audit_key(data_dir)
    prev = GENESIS_PREV
    tampered: list[int] = []
    n = 0
    for i, raw in enumerate(raw_lines):
        if not raw.strip():
            continue
        n += 1
        try:
            entry = json.loads(raw)
        except Exception:
            tampered.append(i)
            continue
        sig_claimed = entry.pop("hmac", "")
        if entry.get("prev") != prev:
            tampered.append(i)
            continue
        sig_recomputed = hmac.new(
            key, prev.encode("ascii") + _canonical(entry), hashlib.sha256,
        ).hexdigest()
        if sig_recomputed != sig_claimed:
            tampered.append(i)
            continue
        prev = sig_claimed
    return {
        "ok": len(tampered) == 0, "lines_checked": n,
        "tampered": tampered, "reason": None,
    }


def review_grants(
    data_dir: Path,
    *,
    stale_after_days: int = 14,
    expiring_within_hours: int = 72,
    now: datetime | None = None,
) -> dict:
    """Periodic re-review (定期复审) of the grant store — read-only.

    Aggregates per-grant usage from ``audit.jsonl`` (grants carry no fire
    counters by design) and classifies each active grant:

    - ``expiring`` — expires within ``expiring_within_hours``
    - ``stale``    — persistent (no expiry), older than ``stale_after_days``,
                     and not fired within that window: consider revoking
    - ``fresh``    — persistent, created within the window, never fired
                     (too new to judge)
    - ``active``   — everything else

    Also returns a trailing-7-day roll-up over ALL audit entries (including
    ``grant_id: null`` stable-default fires and denials) plus over-budget
    actors. No HMAC verification here — that stays ``verify_audit``'s job.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    grants = load_grants(data_dir)
    holds = load_holds(data_dir)

    # ── Aggregate the audit log: per-grant counters + 7-day roll-up ──
    # future: tail-cap this read at ~5000 lines once the log grows.
    audit_path = _store_root(data_dir) / AUDIT_FILE
    per_grant: dict[str, dict] = {}
    week = {"fired": 0, "ok": 0, "failed": 0, "by_verb": {}, "by_actor": {}}
    week_start = now - timedelta(days=7)
    audit_lines = 0
    if audit_path.exists():
        try:
            raw_lines = audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            raw_lines = []
        for raw in raw_lines:
            if not raw.strip():
                continue
            try:
                e = json.loads(raw)
            except ValueError:
                continue  # corrupt line — verify_audit's problem, not review's
            audit_lines += 1
            ts = _parse_iso(e.get("ts"))
            gid = e.get("grant_id")
            if gid:
                rec = per_grant.setdefault(
                    gid, {"fire_count": 0, "last_fired_at": None})
                rec["fire_count"] += 1
                if ts and (rec["last_fired_at"] is None
                           or ts > rec["last_fired_at"]):
                    rec["last_fired_at"] = ts
            if ts and ts >= week_start:
                week["fired"] += 1
                week["ok" if e.get("ok") else "failed"] += 1
                verb = f"{e.get('app', '?')}.{e.get('method', '?')}"
                week["by_verb"][verb] = week["by_verb"].get(verb, 0) + 1
                actor = e.get("actor") or {}
                actor_str = f"{actor.get('type', '?')}/{actor.get('id', '?')}"
                week["by_actor"][actor_str] = (
                    week["by_actor"].get(actor_str, 0) + 1)

    # ── Classify each grant ──
    stale_cutoff = now - timedelta(days=stale_after_days)
    expiring_cutoff = now + timedelta(hours=expiring_within_hours)
    reviewed: list[dict] = []
    stale_ids: list[str] = []
    expiring_ids: list[str] = []
    for g in grants:
        rec = per_grant.get(g.get("id") or "", {})
        last_fired = rec.get("last_fired_at")
        fire_count = rec.get("fire_count", 0)
        expires = _parse_iso(g.get("expires_at"))
        created = _parse_iso(g.get("created_at"))
        if expires is not None and expires <= expiring_cutoff:
            status = "expiring"
            expiring_ids.append(g.get("id") or "")
        elif expires is None and created is not None and created < stale_cutoff \
                and (last_fired is None or last_fired < stale_cutoff):
            status = "stale"
            stale_ids.append(g.get("id") or "")
        elif expires is None and fire_count == 0:
            status = "fresh"
        else:
            status = "active"
        reviewed.append({
            **g,
            "fire_count": fire_count,
            "last_fired_at": last_fired.isoformat() if last_fired else None,
            "status": status,
        })

    budgets_over = [
        b.get("actor_id") for b in all_budgets(data_dir) if b.get("over")
    ]
    return {
        "generated_at": now.isoformat(),
        "grants": reviewed,
        "stale_ids": stale_ids,
        "expiring_ids": expiring_ids,
        "week": week,
        "holds_count": len(holds),
        "budgets_over": budgets_over,
        "audit_lines": audit_lines,
    }
