"""Wellbeing wheel — runtime helpers for behavioral balance scoring.

The 8-dimension wellbeing wheel is a silent design lens. This module
gathers *behavioral* signals — what the user actually did — and exposes
a `planner_context()` fragment that steers LLM-driven planners (briefing,
assistant, reflect) toward thin dimensions without naming the wheel.

Service, not supply: a dimension with 0 apps but recent activity scores
higher than a dimension with 10 apps touched by nothing in 30 days.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

from emptyos.sdk import CAPTURE_LINE_RE, dimensions

_JOURNAL_SECTION_RE = re.compile(r"(?:^|\n)###\s*Journal\s*\n(.*?)(?=\n###\s|\n##\s|\Z)", re.DOTALL)


def _habits_log_path(kernel) -> Path:
    # habits data lives inside the healing app's data dir (merged 2026-04-19)
    base = Path(kernel.config.path).parent / "data" / "apps"
    merged = base / "healing" / "habits.json"
    if merged.exists():
        return merged
    # fallback: legacy standalone habits app path
    return base / "personal" / "habits" / "habits.json"


def _scan_journal_body(text: str) -> str:
    """Return just the '### Journal' section, or full text if no section."""
    m = _JOURNAL_SECTION_RE.search(text)
    return m.group(1) if m else text


def collect(kernel, window_days: int = 30) -> dict:
    """Aggregate dimension signals across vault + habits over window.

    Returns: {signals, total, journal_days_scanned, window_days}.
    """
    signals = dimensions.empty_counts()
    today = date.today()
    window_start = today - timedelta(days=window_days)
    journal_days_scanned = 0

    vault = getattr(kernel.config, "notes_path", None)
    vault_path = Path(vault) if vault else None
    if vault_path and vault_path.exists():
        for i in range(window_days):
            d = today - timedelta(days=i)
            jp = vault_path / "50_Journal" / str(d.year) / f"{d.isoformat()}.md"
            try:
                text = jp.read_text(encoding="utf-8", errors="ignore")
            except (FileNotFoundError, OSError):
                continue
            journal_days_scanned += 1
            body = _scan_journal_body(text)
            for dim in dimensions.extract(body):
                signals[dim] += 2
            for dim, n in dimensions.scan_text(body).items():
                if n > 0:
                    signals[dim] += min(n, 3)

        cap = vault_path / "00_Inbox" / "_captures.md"
        try:
            ctext = cap.read_text(encoding="utf-8", errors="ignore")
        except (FileNotFoundError, OSError):
            ctext = ""
        if ctext:
            cutoff = window_start.isoformat()
            for line in ctext.splitlines():
                m = CAPTURE_LINE_RE.match(line.strip())
                if not m or m.group(1)[:10] < cutoff:
                    continue
                line_text, tag = m.group(2), (m.group(3) or "")
                dim = dimensions.resolve(tag)
                if not dim:
                    found = dimensions.extract(line_text)
                    dim = found[0] if found else None
                if not dim:
                    nl = dimensions.scan_text(line_text)
                    top = max(nl.items(), key=lambda kv: kv[1], default=(None, 0))
                    if top[1] > 0:
                        dim = top[0]
                if dim:
                    signals[dim] += 1

    try:
        hd_path = _habits_log_path(kernel)
        hd = json.loads(hd_path.read_text(encoding="utf-8"))
        dim_map = {h["id"]: h.get("dimension", "") for h in hd.get("habits", [])}
        cutoff = window_start.isoformat()
        for day, entries in hd.get("log", {}).items():
            if day < cutoff:
                continue
            for hid, count in entries.items():
                dim = dim_map.get(hid, "")
                if dim in signals and count > 0:
                    signals[dim] += 1
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass

    return {
        "signals": signals,
        "total": sum(signals.values()),
        "journal_days_scanned": journal_days_scanned,
        "window_days": window_days,
    }


def collect_signals(kernel, window_days: int = 30) -> dict[str, int]:
    """Backwards-compatible thin wrapper — returns just {dim: count}."""
    return collect(kernel, window_days)["signals"]


_CTX_CACHE: dict[tuple, tuple[float, str]] = {}
_CTX_TTL_SECONDS = 180


def planner_context(kernel, window_days: int = 30) -> str:
    """Prompt-fragment steering LLM planners toward thin-dimension actions.

    Cached per (date, window_days) for 3 minutes. The cache is per-process
    and invalidates whenever the date changes, so it's safe even if signals
    shift during the day — the LLM only sees a slightly stale reading.
    """
    key = (date.today(), window_days)
    now = time.monotonic()
    cached = _CTX_CACHE.get(key)
    if cached and now - cached[0] < _CTX_TTL_SECONDS:
        return cached[1]

    result = collect(kernel, window_days)
    signals = result["signals"]
    if result["total"] == 0:
        _CTX_CACHE[key] = (now, "")
        return ""
    reading = dimensions.balance_score(signals)
    thin = reading.get("thin") or []
    dominant = reading.get("dominant") or []
    if not thin:
        _CTX_CACHE[key] = (now, "")
        return ""

    thin_str = ", ".join(f"{d} ({signals[d]})" for d in thin)
    dom_str = ", ".join(f"{d} ({signals[d]})" for d in dominant) if dominant else "none"

    fragment = (
        "\n\n# Wellbeing Context (silent — do not mention dimensions to the user)\n"
        f"Last {window_days}d signal density across the 8-dimension life wheel:\n"
        f"- Thin areas: {thin_str}\n"
        f"- Dominant areas: {dom_str}\n"
        "When suggesting what to do or how to plan, gently bias toward "
        "concrete actions that feed the thin areas. Translate into specific "
        "real-world acts (e.g. 'text a friend', 'ten minutes outside', "
        "'log this week's spend', 'sit quietly before bed'). Never use the "
        "dimension names as jargon. Balance is the direction, not the topic."
    )
    _CTX_CACHE[key] = (now, fragment)
    return fragment


def invalidate_cache():
    """Clear the planner_context TTL cache — call from event handlers when
    a dimension-bearing signal is emitted (capture:saved, journal:entry, etc)."""
    _CTX_CACHE.clear()


# Action templates — dimension-appropriate real-world suggestions.
# Never mention the dimension name to the user; these translate the wheel into acts.
_THIN_ACTIONS = {
    "physical": {"action": "Step outside for a 10-minute walk", "url": "/healing/"},
    "social": {"action": "Message someone you haven't talked to this week", "url": "/contacts/"},
    "emotional": {"action": "Write three lines about how you're feeling", "url": "/journal/"},
    "spiritual": {"action": "Sit quietly for five minutes", "url": "/meditation/"},
    "environmental": {"action": "Tidy one surface in your space", "url": "/hub/"},
    "financial": {"action": "Check this week's spending", "url": "/finance/"},
    "intellectual": {"action": "Read one page of something substantive", "url": "/hub/"},
    "occupational": {"action": "Finish one small task", "url": "/task/"},
}


def nudge_for_thin(kernel, window_days: int = 30) -> dict | None:
    """Silent wheel-informed nudge. Returns {action, url} for the thinnest
    dimension, or None if the wheel is balanced enough that no steer is needed.

    The caller surfaces only `action` + `url` — never the dim key. This keeps
    the wheel a design lens, not a user-visible feature.
    """
    result = collect(kernel, window_days)
    if result["total"] == 0:
        return None
    reading = dimensions.balance_score(result["signals"])
    thin = reading.get("thin") or []
    if not thin:
        return None
    # Prefer the thinnest (lowest signal count) among the tagged-thin dims.
    thinnest = min(thin, key=lambda d: result["signals"].get(d, 0))
    tmpl = _THIN_ACTIONS.get(thinnest)
    if not tmpl:
        return None
    return {"action": tmpl["action"], "url": tmpl["url"]}
