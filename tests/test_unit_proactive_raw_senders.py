"""Proactive gate — senders count a send correctly, and none bypass the gate.

Pins the 2026-09-15 migration recorded in `.claude/rules/proactive-comms.md`:

* ``counts_as_sent`` is the one place a sender decides whether a result means
  the message reached the user. ``disabled`` counts only when the raw fallback
  really sent — a result without ``raw_sent`` is the case that recorded
  reminders as sent on an install with no notifications service.
* No app or plugin looks up the ``notifications`` or ``telegram`` service to
  send directly, except the two exemptions the rule's table names. Reactor used
  to forward held nudges to the phone this way.
* The migrated senders use kinds the user can mute; the notices nothing retries
  and that must not wait are critical, and a critical nudge does not spend the
  cap or gap the reminders behind it rely on; reactor no longer repeats a
  reminder; health's connector_down alerts stay off the phone.

What the scan catches is the service *lookup* by its literal name, and the three
variable names the tree used for the result. It cannot see a lookup through a
constant or variable (``self.service(NOTIF)``), ``getattr(svc, "send")``, or a
service object handed in from elsewhere — a review still has to.

Static: reads source files, imports only the pure gate module.
"""

from __future__ import annotations

import ast
import collections
import re
from datetime import datetime
from pathlib import Path

import pytest

from emptyos.sdk import proactive as pro
from emptyos.sdk.proactive import counts_as_sent

REPO = Path(__file__).resolve().parents[1]

# ── counts_as_sent ───────────────────────────────────────────────────


@pytest.mark.parametrize("result,expected", [
    ({"delivered": True, "reason": "ok"}, True),
    ({"delivered": False, "reason": "dup:reminder:r1:x"}, True),
    ({"delivered": False, "reason": "disabled", "raw_sent": True}, True),
    ({"delivered": False, "reason": "disabled", "raw_sent": False}, False),
    ({"delivered": False, "reason": "disabled"}, False),
    ({"delivered": False, "reason": "quiet-hours"}, False),
    ({"delivered": False, "reason": "daily-cap"}, False),
    ({"delivered": False, "reason": "gap:reminder"}, False),
    ({"delivered": False, "reason": "muted:reminder"}, False),
    ({"delivered": False, "reason": "error: boom"}, False),
    # raw_sent only matters for a disabled verdict — a held nudge never went out.
    ({"delivered": False, "reason": "quiet-hours", "raw_sent": True}, False),
    (None, False),
    ({}, False),
])
def test_counts_as_sent(result, expected):
    assert counts_as_sent(result) is expected


# ── a critical nudge does not spend the budget ───────────────────────


def _fresh_state():
    return {"last_sent": {}, "day": {"date": "", "total": 0, "kinds": {}}, "dedup": {}}


def test_a_critical_nudge_records_only_its_dedup_key():
    state = _fresh_state()
    pro.record_sent(state, "bookme", "bookme:b1:booked", now=10_000.0, spends_budget=False)
    assert state["day"]["total"] == 0 and state["last_sent"] == {}
    assert "bookme:b1:booked" in state["dedup"]


def test_a_reminder_is_not_held_behind_a_critical_booking():
    policy = dict(pro.DEFAULT_POLICY, enabled=True, quiet_start="00:00", quiet_end="00:00")
    state = _fresh_state()
    pro.record_sent(state, "bookme", "bookme:b1:booked", now=10_000.0, spends_budget=False)
    assert pro.decide(policy, state, kind="reminder", now=10_001.0).deliver


def test_a_reminder_is_not_capped_behind_critical_bookings():
    # Same-day state so the daily-cap branch really runs: with a cap of 1, a
    # critical booking that spent the budget would hold the reminder.
    policy = dict(pro.DEFAULT_POLICY, enabled=True, quiet_start="00:00", quiet_end="00:00",
                  daily_cap=1, min_gap_sec=None)
    now_dt = datetime(2026, 9, 15, 12, 0)
    state = _fresh_state()
    pro.record_sent(state, "bookme", "bookme:b1:booked", now=10_000.0, now_dt=now_dt,
                    spends_budget=False)
    assert pro.decide(policy, state, kind="reminder", now=10_001.0, now_dt=now_dt).deliver


def test_only_critical_with_the_policy_flag_bypasses_the_budget():
    on = dict(pro.DEFAULT_POLICY, critical_bypasses_quiet=True)
    off = dict(pro.DEFAULT_POLICY, critical_bypasses_quiet=False)
    assert pro.bypasses_budget(on, "critical") is True
    assert pro.bypasses_budget(off, "critical") is False
    assert pro.bypasses_budget(on, "high") is False
    assert pro.bypasses_budget(on, "normal") is False


def test_decide_honours_the_policy_flag_for_critical():
    late = datetime(2026, 9, 15, 23, 0)  # inside the default 22:00-07:00 quiet window
    off = dict(pro.DEFAULT_POLICY, enabled=True, critical_bypasses_quiet=False)
    on = dict(off, critical_bypasses_quiet=True)
    assert pro.decide(off, _fresh_state(), kind="bookme", urgency="critical",
                      now_dt=late).reason == "quiet-hours"
    assert pro.decide(on, _fresh_state(), kind="bookme", urgency="critical", now_dt=late).deliver


def test_an_ordinary_nudge_still_spends_the_budget():
    policy = dict(pro.DEFAULT_POLICY, enabled=True, quiet_start="00:00", quiet_end="00:00")
    state = _fresh_state()
    pro.record_sent(state, "reminder", "reminder:r1", now=10_000.0)
    assert pro.decide(policy, state, kind="deadline", now=10_001.0).reason == "gap"


# ── no direct sends outside the rule's exemptions ────────────────────

RAW_SEND = re.compile(
    r"""(service|get_optional|require|services\.get)\(\s*["'](notifications|telegram)["']\s*[,)]"""
    r"""|\b_telegram\("""
    r"""|\b(notif|notifier|tg)\.send\("""
)


@pytest.mark.parametrize("source", [
    'self.service("notifications")',
    "self.kernel.services.get_optional('telegram')",
    'self.kernel.services.get_optional("telegram", None)',
    'self.require("notifications")',
    'self.kernel.services.get("notifications")',
    'self.kernel.services.get("notifications", default)',
    'tg = self.service(\n    "telegram"\n)',      # split across lines
    "await self._telegram(msg)",
    "await notif.send(msg)",
    "await notifier.send(msg, priority='info')",
    "await tg.send(msg)",
])
def test_pattern_catches_each_raw_send_shape(source):
    assert RAW_SEND.search(source), source


@pytest.mark.parametrize("source", [
    'await self.proactive_notify_or_raw("system", msg)',
    "await self._send_telegram(message, emoji)",   # the service's own sender
    'self.app_config("telegram_on_new_friction", True)',
    'self.require("telegram-bridge")',
    'await self.kernel.events.emit("notification:sent", {})',
    'self.kernel.config.get("telegram")',
    'os.environ.get("notifications")',
])
def test_pattern_ignores_legitimate_code(source):
    assert not RAW_SEND.search(source), source


# Every entry is named in the rule's "Known raw senders" table, or is the gate /
# the service itself. The number is how many raw-send matches the file holds
# today, so a new direct send added inside an exempt file still fails. Adding a
# path here means adding a row to that table.
ALLOWED = {
    "emptyos/sdk/base_app.py": 3,                 # the gate's own delivery + the _or_raw fallback
    "plugins/notifications/plugin.py": 1,         # the service itself
    "apps/public/standard/runbook/engine.py": 2,  # interactive runs: the user just clicked Apply
    "plugins/health/plugin.py": 2,                # a plugin has no BaseApp helper
}

SKIP_PARTS = {"__pycache__", "_retired", "node_modules"}


def _source_files():
    for root in ("apps", "plugins", "emptyos"):
        for path in (REPO / root).rglob("*.py"):
            rel = path.relative_to(REPO)
            if SKIP_PARTS.intersection(rel.parts):
                continue
            yield rel.as_posix(), path.read_text(encoding="utf-8", errors="replace")


def _raw_send_hits():
    """(rel, lineno, matched text) per match, searching whole files so a call
    split across lines is still found."""
    for rel, text in _source_files():
        for m in RAW_SEND.finditer(text):
            yield rel, text.count("\n", 0, m.start()) + 1, m.group(0)


def test_the_scan_sees_real_sources():
    """Guard against a vacuous pass: the allowlisted senders must still match,
    or the scan has stopped recognising a raw send at all."""
    hits = {rel for rel, _, _ in _raw_send_hits()}
    assert {"apps/public/standard/runbook/engine.py", "plugins/health/plugin.py"} <= hits


def test_no_raw_sends_outside_the_exemptions():
    offenders = [f"{rel}:{lineno}: {text}"
                 for rel, lineno, text in _raw_send_hits() if rel not in ALLOWED]
    assert not offenders, (
        "send through proactive_notify_or_raw instead, or add a row to the "
        "raw-senders table in .claude/rules/proactive-comms.md:\n" + "\n".join(offenders)
    )


def test_exempt_files_hold_exactly_their_known_sends():
    counts = collections.Counter(rel for rel, _, _ in _raw_send_hits())
    assert {rel: counts.get(rel, 0) for rel in ALLOWED} == ALLOWED


# ── kinds, urgency, routing, and the reactor duplicate ───────────────


def _tree(rel: str) -> ast.Module:
    return ast.parse((REPO / rel).read_text(encoding="utf-8"))


def _kinds_catalog() -> set[str]:
    for node in ast.walk(_tree("apps/public/standard/proactive/app.py")):
        target = getattr(node, "target", None)
        if isinstance(node, ast.AnnAssign) and isinstance(target, ast.Name) and target.id == "KINDS":
            return {k.value for k in node.value.keys}
    raise AssertionError("KINDS not found in the proactive app")


MIGRATED = [
    "apps/public/standard/bookme/app.py",
    "apps/extension/dev/promote/app.py",  # release-filter: optional
    "apps/extension/dev/promote/distribution.py",  # release-filter: optional
    "apps/extension/dev/pattern-harvester/app.py",  # release-filter: optional
    "apps/extension/dev/dogfood-agent/friction.py",  # release-filter: optional
]


def _notify_kinds(rel: str) -> list[str]:
    kinds = []
    for node in ast.walk(_tree(rel)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("proactive_notify", "proactive_notify_or_raw")
                and node.args and isinstance(node.args[0], ast.Constant)):
            kinds.append(node.args[0].value)
    return kinds


@pytest.mark.parametrize("rel", MIGRATED)
def test_migrated_senders_use_a_mutable_kind(rel):
    from helpers import public_snapshot

    if not (REPO / rel).exists() and public_snapshot():
        pytest.skip(f"{rel} absent (public snapshot)")
    kinds = _notify_kinds(rel)
    assert kinds, f"{rel} no longer calls the gate"
    missing = [k for k in kinds if k not in _kinds_catalog()]
    assert not missing, f"{rel} uses kinds the policy page cannot list or mute: {missing}"


def _calls_in(rel: str, func: str, method: str) -> list[dict[str, str]]:
    """Keyword arguments (as source text) of every ``.<method>(...)`` call inside
    function ``func`` in ``rel``."""
    fn = next(n for n in ast.walk(_tree(rel))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func)
    return [{kw.arg: ast.unparse(kw.value) for kw in n.keywords}
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == method]


def test_new_booking_notice_is_critical():
    calls = _calls_in("apps/public/standard/bookme/app.py", "_after_book", "proactive_notify_or_raw")
    assert calls and all(c.get("urgency") == "'critical'" for c in calls)


def test_agent_self_modification_is_critical_under_its_own_kind():
    calls = _calls_in("apps/public/standard/reactor/reactions_system.py",
                      "on_agent_self_modified", "_notify")
    assert calls and all(c.get("urgency") == "'critical'" for c in calls)
    assert all(c.get("kind") == "'self-modification'" for c in calls)
    assert "self-modification" in _kinds_catalog()


def test_failed_test_runs_share_one_daily_key():
    calls = _calls_in("apps/public/standard/reactor/reactions_system.py",
                      "on_tests_run_completed", "_notify")
    assert calls and all(c.get("dedup_key") == "self._daily_dedup('tests-failed')" for c in calls)


def test_reactor_notify_passes_urgency_through():
    calls = _calls_in("apps/public/standard/reactor/app.py", "_notify", "proactive_notify_or_raw")
    assert calls and all(c.get("urgency") == "urgency" for c in calls)


def test_the_gate_does_not_charge_a_bypassing_critical_nudge():
    calls = _calls_in("emptyos/sdk/base_app.py", "proactive_notify", "record_sent")
    assert calls and all(
        c.get("spends_budget") == "not _pro.bypasses_budget(policy, urgency)" for c in calls)


def test_connector_down_alerts_stay_off_the_phone():
    calls = _calls_in("plugins/health/plugin.py", "_report", "send")
    assert calls and all(c.get("telegram") == "problem['type'] != 'connector_down'" for c in calls)


def test_reactor_does_not_repeat_a_fired_reminder():
    handler = next(n for n in ast.walk(_tree("apps/public/standard/reactor/reactions_life.py"))
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_reminder_fired")
    calls = {n.func.attr for n in ast.walk(handler)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"_notify", "proactive_notify", "proactive_notify_or_raw"}
