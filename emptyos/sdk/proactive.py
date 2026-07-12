"""Proactive-communication dispatch — the "should I interrupt the user, now,
on which channel?" gate.

Pure module (no kernel, no I/O beyond a JSON/JSONL store under ``data_root``),
so it unit-tests headless like ``emptyos/sdk/autopilot.py``. ``BaseApp.proactive_notify``
is the only runtime consumer; the ``proactive`` app owns the policy UI + the
scanner that produces candidate nudges.

The whole point of this layer is restraint: a proactive system that over-talks
trains the user to ignore it (the same failure mode as a noisy audit). So every
nudge passes a gate — master-enabled, per-kind mute, quiet hours, a daily cap, a
minimum gap between nudges, and dedup — before any channel sees it.

**Ships dark.** ``load_policy`` defaults ``enabled: False``; ``decide`` returns
``deliver=False`` reason ``"disabled"`` until the user flips the master toggle in
the proactive app. Nothing speaks on a fresh install.

North-star fit: a nudge *to the user* is reversible/internal (a notification they
dismiss), so it auto-runs once enabled — the safety net is this gate + the audit
log, not approval-before-each-ping. Anything *outbound to a third party* is a
different verb that never routes through here.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ─── Defaults ────────────────────────────────────────────────────────────────
# Voice-first: a nudge grabs attention via the hands-free voice queue AND leaves
# a durable record via the notifications plugin. "voice" is dropped silently when
# the hands-free overlay isn't running (no one polling) — "notify" is the floor.
DEFAULT_CHANNELS: tuple[str, ...] = ("voice", "notify")
# "companion" is opt-in (not in DEFAULT_CHANNELS): apps pass channels=[...,
# "companion"] to push a soft nudge into the page-assistant rail via the
# realtime bus. See BaseApp.proactive_notify + page-assistant.js.
VALID_CHANNELS: frozenset[str] = frozenset({"voice", "notify", "companion"})

DEFAULT_POLICY: dict = {
    "enabled": False,  # master — dark by default; nothing delivers until flipped
    "default_channels": list(DEFAULT_CHANNELS),
    "quiet_start": "22:00",  # HH:MM, 24h local
    "quiet_end": "07:00",
    "daily_cap": 8,  # max nudges delivered per local day across all kinds
    "min_gap_sec": 1800,  # min seconds between any two delivered nudges (anti-burst)
    "critical_bypasses_quiet": True,  # urgency="critical" ignores quiet/gap/cap
    "kinds": {},  # per-kind overrides: {kind: {mute, channels, daily_cap, min_gap_sec}}
}

DEDUP_TTL_SEC = 24 * 3600  # a dedup_key suppresses repeats for 24h
LOG_LIMIT = 1000  # ring the audit log at this many lines

_ANY = "_any"  # state key for the cross-kind last-sent timestamp (global gap)


def _proactive_dir(data_root: Path) -> Path:
    d = Path(data_root) / "proactive"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─── Policy ──────────────────────────────────────────────────────────────────
def load_policy(data_root: Path) -> dict:
    """Load policy merged over DEFAULT_POLICY (so new default keys appear on old
    stores). Never raises — a corrupt file falls back to defaults."""
    p = _proactive_dir(data_root) / "policy.json"
    pol = dict(DEFAULT_POLICY)
    pol["kinds"] = {}
    if p.exists():
        try:
            stored = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for k, v in stored.items():
                    pol[k] = v
        except (OSError, json.JSONDecodeError):
            pass
    if not isinstance(pol.get("kinds"), dict):
        pol["kinds"] = {}
    return pol


def save_policy(data_root: Path, policy: dict) -> None:
    p = _proactive_dir(data_root) / "policy.json"
    p.write_text(json.dumps(policy, indent=2, ensure_ascii=False), encoding="utf-8")


# ─── State ───────────────────────────────────────────────────────────────────
def load_state(data_root: Path) -> dict:
    p = _proactive_dir(data_root) / "state.json"
    st = {"last_sent": {}, "day": {"date": "", "total": 0, "kinds": {}}, "dedup": {}}
    if p.exists():
        try:
            stored = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                st.update(stored)
        except (OSError, json.JSONDecodeError):
            pass
    for key, default in (("last_sent", {}), ("dedup", {})):
        if not isinstance(st.get(key), dict):
            st[key] = default
    if not isinstance(st.get("day"), dict):
        st["day"] = {"date": "", "total": 0, "kinds": {}}
    return st


def save_state(data_root: Path, state: dict) -> None:
    p = _proactive_dir(data_root) / "state.json"
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ─── Quiet hours (extracted from hands-free; the rule-9 shared form) ──────────
def in_quiet_hours(start: str, end: str, now_dt: datetime | None = None) -> bool:
    """True if ``now`` falls inside the quiet window. start/end are HH:MM, 24h
    local. Handles windows that cross midnight (22:00 → 07:00). start==end means
    no quiet window (always False), never "all day"."""
    try:
        sh, sm = (int(x) for x in str(start).split(":"))
        eh, em = (int(x) for x in str(end).split(":"))
    except (ValueError, AttributeError):
        return False
    now = (now_dt or datetime.now()).time()
    start_m, end_m = sh * 60 + sm, eh * 60 + em
    now_m = now.hour * 60 + now.minute
    if start_m == end_m:
        return False
    if start_m < end_m:
        return start_m <= now_m < end_m
    return now_m >= start_m or now_m < end_m  # crosses midnight


# ─── The gate ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Decision:
    deliver: bool
    reason: str
    channels: tuple[str, ...] = field(default_factory=tuple)


def _kind_cfg(policy: dict, kind: str) -> dict:
    raw = policy.get("kinds", {}).get(kind)
    return raw if isinstance(raw, dict) else {}


def _resolve_channels(policy: dict, kind: str, requested: list | None) -> tuple[str, ...]:
    src = requested or _kind_cfg(policy, kind).get("channels") or policy.get("default_channels")
    out = [c for c in (src or DEFAULT_CHANNELS) if c in VALID_CHANNELS]
    return tuple(out) or DEFAULT_CHANNELS


def decide(
    policy: dict,
    state: dict,
    *,
    kind: str,
    dedup_key: str | None = None,
    urgency: str = "normal",
    now: float | None = None,
    now_dt: datetime | None = None,
    requested_channels: list | None = None,
) -> Decision:
    """Decide whether a nudge of ``kind`` delivers right now. Pure — reads policy
    + state, mutates neither. Reasons are returned in priority order so the audit
    log explains exactly which gate fired."""
    now = time.time() if now is None else now
    channels = _resolve_channels(policy, kind, requested_channels)
    kcfg = _kind_cfg(policy, kind)
    critical = urgency == "critical"

    if not policy.get("enabled", False):
        return Decision(False, "disabled", channels)
    if kcfg.get("mute"):
        return Decision(False, f"muted:{kind}", channels)
    # dedup + mute are never bypassed, even by critical — a duplicate is a
    # duplicate and a muted kind is muted on purpose.
    if dedup_key:
        seen = state.get("dedup", {}).get(dedup_key)
        if seen and (now - float(seen)) < DEDUP_TTL_SEC:
            return Decision(False, f"dup:{dedup_key}", channels)

    if not (critical and policy.get("critical_bypasses_quiet", True)):
        if in_quiet_hours(policy.get("quiet_start", ""), policy.get("quiet_end", ""), now_dt):
            return Decision(False, "quiet-hours", channels)
        # daily cap
        day = state.get("day", {})
        if day.get("date") == _local_date(now_dt):
            cap = kcfg.get("daily_cap")
            if cap is not None and day.get("kinds", {}).get(kind, 0) >= int(cap):
                return Decision(False, f"daily-cap:{kind}", channels)
            gcap = policy.get("daily_cap")
            if gcap is not None and day.get("total", 0) >= int(gcap):
                return Decision(False, "daily-cap", channels)
        # min gap — per-kind first, then the global anti-burst gap
        kgap = kcfg.get("min_gap_sec")
        if kgap is not None:
            last_k = state.get("last_sent", {}).get(kind)
            if last_k and (now - float(last_k)) < float(kgap):
                return Decision(False, f"gap:{kind}", channels)
        ggap = policy.get("min_gap_sec")
        if ggap is not None:
            last_any = state.get("last_sent", {}).get(_ANY)
            if last_any and (now - float(last_any)) < float(ggap):
                return Decision(False, "gap", channels)

    return Decision(True, "ok", channels)


def _local_date(now_dt: datetime | None = None) -> str:
    return (now_dt or datetime.now()).date().isoformat()


def record_sent(
    state: dict, kind: str, dedup_key: str | None = None,
    now: float | None = None, now_dt: datetime | None = None,
) -> dict:
    """Mutate ``state`` to record a delivered nudge: bump per-kind + global
    last_sent, roll/increment the day counters, stamp the dedup key, prune old
    dedup entries. Returns the same dict for chaining."""
    now = time.time() if now is None else now
    ls = state.setdefault("last_sent", {})
    ls[kind] = now
    ls[_ANY] = now

    today = _local_date(now_dt)
    day = state.get("day") or {}
    if day.get("date") != today:
        day = {"date": today, "total": 0, "kinds": {}}
    day["total"] = int(day.get("total", 0)) + 1
    day.setdefault("kinds", {})[kind] = int(day.get("kinds", {}).get(kind, 0)) + 1
    state["day"] = day

    if dedup_key:
        dd = state.setdefault("dedup", {})
        dd[dedup_key] = now
        cutoff = now - DEDUP_TTL_SEC
        state["dedup"] = {k: v for k, v in dd.items() if float(v) >= cutoff}
    return state


# ─── Audit log (the surface the user actually reads) ─────────────────────────
def append_log(data_root: Path, entry: dict) -> None:
    """Append one audit line. Every gate decision — delivered or suppressed —
    lands here so the user can see what the system wanted to say and why it did
    or didn't. Rings at LOG_LIMIT."""
    p = _proactive_dir(data_root) / "log.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > LOG_LIMIT:
            p.write_text("\n".join(lines[-LOG_LIMIT:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def read_log(data_root: Path, limit: int = 50, *, delivered_only: bool = False) -> list[dict]:
    p = _proactive_dir(data_root) / "log.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if delivered_only and not row.get("delivered"):
            continue
        out.append(row)
    out.reverse()  # newest first
    return out[:limit]
