"""dictionary — pronunciation-error analysis — weak phones + confusion pairs + event log.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The three-store pronunciation telemetry: weak-phones.json (per-phone aggregate + FSRS SRS), pronounce-pairs.json (per-pair confusion counts), and pronounce-events.jsonl (append-only, rotated at 2000 lines), plus the vault roll-up mirror and the four /api routes (weak-phones, weak-phones/review, pronounce-patterns, pronounce-events). Source of truth for what the learner systematically mispronounces..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.data_dir for the JSON/JSONL stores; self.vault_write for the roll-up; self.emit for dictionary:pronounce_logged / weak_phone_logged.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from pathlib import Path
from emptyos.sdk import load_json, save_json, web_route
from emptyos.sdk.srs import fsrs_schedule, quality_to_rating, repair_legacy_schedule
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only


# ─── Bind to DictionaryApp class as ────────────────────────────────
#   _EVENTS_MAX_LINES         = _pronounce._EVENTS_MAX_LINES
#   _weak_phones_path         = _pronounce._weak_phones_path
#   _pronounce_pairs_path     = _pronounce._pronounce_pairs_path
#   _pronounce_events_path    = _pronounce._pronounce_events_path
#   _load_weak_phones         = _pronounce._load_weak_phones
#   _save_weak_phones         = _pronounce._save_weak_phones
#   _load_pronounce_pairs     = _pronounce._load_pronounce_pairs
#   _save_pronounce_pairs     = _pronounce._save_pronounce_pairs
#   _append_pronounce_event   = _pronounce._append_pronounce_event
#   _read_pronounce_events    = _pronounce._read_pronounce_events
#   log_pronounce_events      = _pronounce.log_pronounce_events
#   pronounce_snapshot        = _pronounce.pronounce_snapshot
#   log_weak_phones           = _pronounce.log_weak_phones
#   _write_weak_phones_vault  = _pronounce._write_weak_phones_vault
#   due_weak_phones           = _pronounce.due_weak_phones
#   api_weak_phones           = _pronounce.api_weak_phones
#   api_weak_phones_review    = _pronounce.api_weak_phones_review
#   api_pronounce_patterns    = _pronounce.api_pronounce_patterns
#   api_pronounce_events      = _pronounce.api_pronounce_events
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


_EVENTS_MAX_LINES = 2000


def _weak_phones_path(self) -> Path:
    return self.data_dir / "weak-phones.json"


def _pronounce_pairs_path(self) -> Path:
    return self.data_dir / "pronounce-pairs.json"


def _pronounce_events_path(self) -> Path:
    return self.data_dir / "pronounce-events.jsonl"


def _load_weak_phones(self) -> dict:
    data = load_json(self._weak_phones_path(), {})
    # Same stranding guard as learn — see sdk.srs.repair_legacy_schedule.
    if isinstance(data, dict):
        repair_legacy_schedule(data.values())
    return data


def _save_weak_phones(self, data: dict):
    save_json(self._weak_phones_path(), data)


def _load_pronounce_pairs(self) -> dict:
    return load_json(self._pronounce_pairs_path(), {})


def _save_pronounce_pairs(self, data: dict):
    save_json(self._pronounce_pairs_path(), data)


def _append_pronounce_event(self, event: dict):
    """Append-only event log for time-series analysis. Rotates at 2000 lines."""
    path = self._pronounce_events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    # Cheap rotation — read line count; if over the cap, keep the tail.
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > self._EVENTS_MAX_LINES:
            keep = lines[-self._EVENTS_MAX_LINES:]
            path.write_text("".join(keep), encoding="utf-8")
    except OSError:
        pass


def _read_pronounce_events(self, since_ts: float = 0.0) -> list[dict]:
    path = self._pronounce_events_path()
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since_ts and float(e.get("ts", 0)) < since_ts:
                    continue
                out.append(e)
    except OSError:
        pass
    return out


async def log_pronounce_events(
    self,
    events: list[dict],
    *,
    source: str = "",
    sentence: str = "",
    channel: str = "production",
) -> dict:
    """Record a batch of alignment events from one practice attempt.

    Each event in `events` is one alignment row:
        {ref, hyp, op: "sub"|"del"|"ins", confidence?, word?}

    Updates three stores in one pass:
      - weak-phones.json (per-phone aggregate + SRS state)
      - pronounce-pairs.json (per-pair aggregate "REF→HYP")
      - pronounce-events.jsonl (append-only log)

    ``channel`` separates two genuinely different kinds of evidence that happen
    to name the same phone. ``production`` means the learner *said* it wrong —
    which is what every caller before soundcheck meant, and what ``occurrences``
    has always counted. ``perception`` means they *misheard* it.

    Mixing them would silently change what an existing number means: the
    crosswalk reads ``occurrences`` as ``errors`` and the hub renders it as
    "N slips", so folding mishearings in would make "3 slips" mean some
    unstated mixture of things you said wrong and things you failed to hear.
    So a non-production channel increments its own counter and leaves
    ``occurrences`` alone. The default keeps every existing caller
    byte-for-byte identical.

    Recognising a sound and producing it are different skills with different
    forgetting curves, which is also why a perception channel never advances
    the SRS schedule on these entries — see below.

    Returns a small summary of what was logged.
    """
    from datetime import date as _date
    import time as _time

    if not events:
        return {"logged": 0}

    today = _date.today().isoformat()
    now_ts = _time.time()
    is_production = (channel or "production") == "production"
    weak_store = self._load_weak_phones()
    pair_store = self._load_pronounce_pairs()
    pairs_seen: set[str] = set()
    phones_seen: set[str] = set()

    for ev in events:
        op = ev.get("op")
        ref = (ev.get("ref") or "").strip().upper() or None
        hyp = (ev.get("hyp") or "").strip().upper() or None
        if op not in ("sub", "del", "ins"):
            continue
        # Per-event log row
        self._append_pronounce_event({
            "ts": round(now_ts, 3),
            "source": source,
            "sentence": sentence,
            "op": op,
            "ref": ref,
            "hyp": hyp,
            "word": ev.get("word", ""),
            "confidence": ev.get("confidence"),
            "channel": channel,
        })
        # Per-phone aggregate (only ref-side misses — sub + del)
        if ref and op in ("sub", "del"):
            phones_seen.add(ref)
            entry = weak_store.get(ref) or {
                "phone": ref,
                "occurrences": 0,
                "examples": [],
                "sources": [],
                "review_count": 0,
                "next_review": today,
            }
            if is_production:
                entry["occurrences"] = int(entry.get("occurrences", 0)) + 1
            else:
                entry["perception_occurrences"] = int(
                    entry.get("perception_occurrences", 0)) + 1
            channels = entry.setdefault("channels", {})
            channels[channel] = int(channels.get(channel, 0)) + 1
            ex_label = hyp or "∅"
            if ex_label not in entry["examples"]:
                entry["examples"].append(ex_label)
            entry["examples"] = entry["examples"][-20:]
            if source and source not in entry["sources"]:
                entry["sources"].append(source)
            entry["sources"] = entry["sources"][-10:]
            entry["last_seen"] = today
            weak_store[ref] = entry
        # Per-pair aggregate
        if op == "sub" and ref and hyp:
            pair_key = f"{ref}→{hyp}"
        elif op == "del" and ref:
            pair_key = f"{ref}→∅"
        elif op == "ins" and hyp:
            pair_key = f"∅→{hyp}"
        else:
            continue
        pairs_seen.add(pair_key)
        pair = pair_store.get(pair_key) or {
            "pair": pair_key,
            "ref": ref,
            "hyp": hyp,
            "op": op,
            "count": 0,
            "first_seen": today,
            "recent_sentences": [],
            "sources": [],
        }
        if is_production:
            pair["count"] = int(pair.get("count", 0)) + 1
        else:
            pair["perception_count"] = int(pair.get("perception_count", 0)) + 1
        pair["last_seen"] = today
        if sentence and sentence not in pair["recent_sentences"]:
            pair["recent_sentences"].append(sentence)
        pair["recent_sentences"] = pair["recent_sentences"][-10:]
        if source and source not in pair["sources"]:
            pair["sources"].append(source)
        pair["sources"] = pair["sources"][-10:]
        pair_store[pair_key] = pair

    self._save_weak_phones(weak_store)
    self._save_pronounce_pairs(pair_store)
    self._write_weak_phones_vault(weak_store)
    await self.emit(
        "dictionary:pronounce_logged",
        {"phones": len(phones_seen), "pairs": len(pairs_seen),
         "source": source, "channel": channel},
    )
    return {
        "logged": len(events),
        "phones_touched": sorted(phones_seen),
        "pairs_touched": sorted(pairs_seen),
        "channel": channel,
    }


def pronounce_snapshot(self) -> dict:
    """The raw pronunciation telemetry, for another app to read.

    A plain method rather than a route, because ``call_app`` invokes the
    function directly: a ``@web_route`` handler asked for without a ``request``
    raises TypeError, and a caller wrapping that in a bare ``except`` sees the
    same empty result it would see on a cold start. That is how soundcheck ran
    entirely without telemetry for a while with nothing appearing wrong.

    Returns both stores whole and unranked. ``api_pronounce_patterns`` is the
    display endpoint — it sorts, truncates to a top-N per bucket, and computes
    trends. A consumer weighting *every* contrast needs the untruncated map.
    """
    return {
        "weak": self._load_weak_phones(),
        "pairs": self._load_pronounce_pairs(),
    }


async def log_weak_phones(
    self,
    phone: str,
    examples: list[str] | None = None,
    source: str = "",
) -> dict:
    """Legacy single-phone logging. Kept for compatibility with older
    callers. Prefer `log_pronounce_events` so substitution pairs and
    sentence context are captured."""
    from datetime import date as _date

    phone = (phone or "").strip().upper()
    if not phone:
        return {"error": "phone required"}
    store = self._load_weak_phones()
    entry = store.get(phone) or {
        "phone": phone,
        "occurrences": 0,
        "examples": [],
        "sources": [],
        "review_count": 0,
        "next_review": _date.today().isoformat(),
    }
    entry["occurrences"] = int(entry.get("occurrences", 0)) + 1
    for ex in (examples or [])[:3]:
        if ex and ex not in entry["examples"]:
            entry["examples"].append(ex)
    entry["examples"] = entry["examples"][-20:]
    if source and source not in entry["sources"]:
        entry["sources"].append(source)
        entry["sources"] = entry["sources"][-10:]
    entry["last_seen"] = _date.today().isoformat()
    store[phone] = entry
    self._save_weak_phones(store)
    self._write_weak_phones_vault(store)
    await self.emit("dictionary:weak_phone_logged", {"phone": phone, "count": entry["occurrences"]})
    return {"phone": phone, "occurrences": entry["occurrences"]}


def _write_weak_phones_vault(self, store: dict):
    """Mirror the JSON store into a vault note for human inspection."""
    from datetime import date as _date

    rows = sorted(store.values(), key=lambda e: -e.get("occurrences", 0))
    lines = [
        "---",
        "tag: pronounce-weak",
        f"updated: {_date.today().isoformat()}",
        f"total_phones: {len(rows)}",
        "---",
        "",
        "# Weak Phones",
        "",
        "| Phone | Count | Last seen | Sources | Recent confusions |",
        "|---|---:|---|---|---|",
    ]
    for r in rows[:50]:
        confusions = ", ".join(r.get("examples", [])[-3:]) or "—"
        sources = ", ".join(r.get("sources", [])) or "—"
        lines.append(
            f"| `{r['phone']}` | {r.get('occurrences', 0)} | "
            f"{r.get('last_seen', '')} | {sources} | {confusions} |"
        )
    self.vault_write("weak-phones.md", "\n".join(lines))


def due_weak_phones(self, limit: int = 5) -> list[dict]:
    """Return weak phones due for review, ordered by occurrences."""
    from datetime import date as _date

    today = _date.today().isoformat()
    rows = [
        r for r in self._load_weak_phones().values()
        if r.get("next_review", today) <= today
    ]
    rows.sort(key=lambda r: -r.get("occurrences", 0))
    return rows[:limit]


@web_route("GET", "/api/weak-phones")
async def api_weak_phones(self, request):
    all_phones = sorted(
        self._load_weak_phones().values(),
        key=lambda r: -r.get("occurrences", 0),
    )
    return {
        "all": all_phones,
        "due": self.due_weak_phones(limit=10),
    }


@web_route("POST", "/api/weak-phones/review")
async def api_weak_phones_review(self, request):
    """Record a review pass on a weak phone. quality: 0-5."""
    data = await request.json()
    phone = (data.get("phone") or "").strip().upper()
    quality = int(data.get("quality", 3))
    if not phone:
        return {"error": "phone required"}
    store = self._load_weak_phones()
    entry = store.get(phone)
    if not entry:
        return {"error": "phone not tracked"}
    fsrs_schedule(entry, quality_to_rating(quality))
    store[phone] = entry
    self._save_weak_phones(store)
    return {
        "phone": phone,
        "stability": entry["s"],
        "next_review": entry["next_review"],
    }


@web_route("GET", "/api/pronounce-patterns")
async def api_pronounce_patterns(self, request):
    """Top confusion pairs + per-bucket roll-ups + trend signal.

    Query params:
        limit        — top-N pairs to return per bucket (default 10)
        since_days   — only consider events from the last N days (0 = all time)

    Returns three buckets (substitutions / deletions / insertions), each
    a list ordered by frequency, with example sentences and a count for
    last-7d-vs-prior-7d trend per pair (negative = improving).
    """
    import time as _time

    limit = max(1, min(int(request.query_params.get("limit", "10") or 10), 50))
    since_days = int(request.query_params.get("since_days", "0") or 0)
    since_ts = _time.time() - since_days * 86400 if since_days else 0.0

    pairs = self._load_pronounce_pairs()
    events = self._read_pronounce_events(since_ts=since_ts)

    # Trend windows
    now = _time.time()
    seven_d_ago = now - 7 * 86400
    fourteen_d_ago = now - 14 * 86400
    last7: dict[str, int] = {}
    prior7: dict[str, int] = {}
    recent_events: list[dict] = []
    for ev in events:
        ts = float(ev.get("ts", 0))
        ref = (ev.get("ref") or "").strip() or "∅"
        hyp = (ev.get("hyp") or "").strip() or "∅"
        key = f"{ref}→{hyp}"
        if ts >= seven_d_ago:
            last7[key] = last7.get(key, 0) + 1
        elif ts >= fourteen_d_ago:
            prior7[key] = prior7.get(key, 0) + 1
    # Tail of most recent events (newest first), with a small cap so the
    # response stays light.
    recent_events = events[-50:][::-1]

    def _bucket(op: str) -> list[dict]:
        rows = [p for p in pairs.values() if p.get("op") == op]
        rows.sort(key=lambda r: -r.get("count", 0))
        top = rows[:limit]
        for p in top:
            key = p.get("pair", "")
            p["trend_last_7d"] = last7.get(key, 0)
            p["trend_prior_7d"] = prior7.get(key, 0)
            p["trend_delta"] = p["trend_last_7d"] - p["trend_prior_7d"]
        return top

    return {
        "since_days": since_days,
        "total_events": len(events),
        "substitutions": _bucket("sub"),
        "deletions": _bucket("del"),
        "insertions": _bucket("ins"),
        "recent": recent_events,
        "weak_phones_due": self.due_weak_phones(limit=5),
    }


@web_route("GET", "/api/pronounce-events")
async def api_pronounce_events(self, request):
    """Raw events log, newest first. For the timeline view."""
    limit = max(1, min(int(request.query_params.get("limit", "100") or 100), 1000))
    events = self._read_pronounce_events()
    return {"events": events[-limit:][::-1], "total": len(events)}
