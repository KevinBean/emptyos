"""dictionary — vocabulary-expansion loop: intake feed, reading-harvest, production reps.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the four-stage growth loop layered on top of the SRS engine —
(2) auto-harvest of above-level words from digested reading, (3) a daily
LLM-curated breadth feed, (4) weekly production reps with coached feedback —
plus the hub panel that surfaces all three. Stage 1 (conversation intake) is
human-driven and lives outside the app. All state in ``data/.../vocab_loop.json``;
all surfaces gated behind ``[apps.dictionary] feature.vocab-loop.enabled`` (dark
default) so the app is byte-for-byte unchanged until the flag is flipped.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.save_word (vocab — persist + SRS enroll);
self._vault_words/_read_vault_word/_vault_as_lookup (spine data layer);
self._load_srs (srs); self.think/self.read/self.emit/self.app_config (capabilities);
the VOCAB_* personas from shared.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

from emptyos.sdk import load_json, on_event, parse_llm_json, save_json, strip_frontmatter, web_route

from .shared import (
    VOCAB_FEED_SYSTEM,
    VOCAB_HARVEST_SYSTEM,
    VOCAB_PRODUCTION_SYSTEM,
    moment_index,
    moment_url,
    reading_prompt,
)

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only


# ─── Bind to DictionaryApp class as ────────────────────────────────
#   _vl_path             = _vocab_loop._vl_path
#   _vl_load             = _vocab_loop._vl_load
#   _vl_save             = _vocab_loop._vl_save
#   _vl_on               = _vocab_loop._vl_on
#   _harvest_text        = _vocab_loop._harvest_text
#   _digest_moments      = _vocab_loop._digest_moments
#   _ensure_feed         = _vocab_loop._ensure_feed
#   _on_video_digested   = _vocab_loop._on_video_digested     # @on_event
#   api_loop_status      = _vocab_loop.api_loop_status
#   api_progress         = _vocab_loop.api_progress
#   api_log_test         = _vocab_loop.api_log_test
#   api_harvest          = _vocab_loop.api_harvest
#   api_harvest_inbox    = _vocab_loop.api_harvest_inbox
#   api_harvest_resolve  = _vocab_loop.api_harvest_resolve
#   api_vocab_feed       = _vocab_loop.api_vocab_feed
#   api_feed_add         = _vocab_loop.api_feed_add
#   api_production_prompt= _vocab_loop.api_production_prompt
#   api_production_check = _vocab_loop.api_production_check
#   panel_vocab_today    = _vocab_loop.panel_vocab_today
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


_INPUT_CAP = 6000        # max chars of a reading note fed to the harvester
_EXCLUDE_CAP = 200       # max known words passed to the feed prompt

# Progress-to-30k trajectory (see the vault plan note). Phase targets are the
# upper bound of each band; the north star is the top of the native range.
_NORTH_STAR = 30000
_PHASE_TARGETS = [
    {"phase": 1, "to": 10000},
    {"phase": 2, "to": 16000},
    {"phase": 3, "to": 23000},
    {"phase": 4, "to": 30000},
]
# Known historical test scores (preply test-your-vocab), seeded once.
_SEED_TESTS = [
    {"date": "2023-01-17", "score": 6448},
    {"date": "2026-06-15", "score": 6116},
]
_DECK_HISTORY_CAP = 730  # ~2 years of daily snapshots


def _days_since(iso: str | None) -> int:
    if not iso:
        return 9999
    try:
        d0 = date.fromisoformat(iso)
    except Exception:
        return 9999
    return (date.today() - d0).days


# ── State ────────────────────────────────────────────────────────────


def _vl_path(self):
    return self.data_dir / "vocab_loop.json"


def _vl_load(self) -> dict:
    d = load_json(self._vl_path(), {})
    d.setdefault("harvest_inbox", [])
    d.setdefault("harvested_seen", [])
    d.setdefault("feed", {})
    d.setdefault("production", {})
    d.setdefault("tests", [])          # [{date, score}] vocabulary-test history
    d.setdefault("deck_history", [])   # [{date, count}] daily SRS-deck-size snapshots
    return d


def _vl_save(self, d: dict):
    save_json(self._vl_path(), d)


def _vl_on(self) -> bool:
    return bool(self.app_config("feature.vocab-loop.enabled", False))


# ── Stage 2: harvest above-level words from digested reading ──────────


async def _harvest_text(
    self,
    text: str,
    *,
    source: str = "",
    source_url: str = "",
    moments: dict | None = None,
) -> list[dict]:
    """LLM-extract above-C1 words from a passage; stage genuinely new ones.

    Dedupes against the saved deck, the harvested-seen set, and the inbox so
    a word is only ever surfaced once. Returns the newly staged candidates.

    ``moments`` is a ``moment_index`` over the source's timestamped transcript,
    when there is one. A word found in it gets a source URL deep-linked to the
    second it was said, so review can send the reader back to the moment they
    met it rather than to the top of an hour-long video.
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        # The same bar the reading layer uses. This prompt used to hardcode "an
        # advanced (C1) reader" — a level asserted about a reader nobody had asked,
        # in a second place, free to drift from the first.
        from .reading import _band_for  # noqa: PLC0415 — avoids an import cycle

        resp = await self.think(
            text[:_INPUT_CAP],
            system=reading_prompt(VOCAB_HARVEST_SYSTEM, "", "", level=_band_for(self)),
            domain="text",
            temperature=0.2,
        )
        cands = parse_llm_json(resp)
    except Exception:
        return []
    if not isinstance(cands, list):
        return []

    d = self._vl_load()
    known = {w.lower() for w in await self._vault_words()}
    seen = set(d["harvested_seen"])
    inbox_words = {(c.get("word") or "").lower() for c in d["harvest_inbox"]}
    today = date.today().isoformat()
    added: list[dict] = []
    for c in cands:
        if not isinstance(c, dict):
            continue
        w = (c.get("word") or "").strip()
        wl = w.lower()
        if not w or wl in known or wl in seen or wl in inbox_words:
            continue
        # Where this word was actually said, when the source carried timestamps.
        # The transcript line is the fallback sentence: it is the reader's own
        # encounter, which beats the model's paraphrase of it.
        moment = (moments or {}).get(wl) or {}
        entry = {
            "word": w,
            "part_of_speech": c.get("part_of_speech", ""),
            "definition": c.get("definition", ""),
            "chinese": c.get("chinese", ""),
            "sentence": c.get("sentence", "") or moment.get("text", ""),
            "source": source,
            "source_url": moment_url(source_url, moment["t"]) if moment else source_url,
            "ts": today,
        }
        d["harvest_inbox"].append(entry)
        inbox_words.add(wl)
        added.append(entry)
    if added:
        self._vl_save(d)
        await self.emit("dictionary:vocab_harvested", {"count": len(added), "source": source})
    return added


async def _digest_moments(self, digest_path: str) -> dict:
    """Moment index for a digest, from video-digest's transcript sidecar.

    video-digest writes ``<stem>.transcript.json`` beside ``<stem>.md`` for any
    source it could get timestamps for (it powers Listen mode). The path is
    derivable, so this needs no change on that side and no new event field.

    Best-effort throughout: a web clip has no sidecar, a rate-limited fetch
    leaves none, and a malformed one is not worth failing a harvest over.
    """
    if not digest_path.endswith(".md"):
        return {}
    try:
        raw = await self.read(f"{digest_path[:-3]}.transcript.json")
        return moment_index(json.loads(raw).get("lines"))
    except Exception:
        return {}


@on_event("video-digest:digested")
async def _on_video_digested(self, event):
    """When a web-clip / video is digested, mine its prose for new words."""
    if not self._vl_on():
        return
    data = getattr(event, "data", {}) or {}
    path = data.get("digest_path")
    if not path:
        return
    try:
        content = await self.read(path)
    except Exception:
        return
    await self._harvest_text(
        strip_frontmatter(content),
        source=data.get("title") or path,
        source_url=data.get("url") or "",
        moments=await self._digest_moments(path),
    )


@web_route("POST", "/api/harvest")
async def api_harvest(self, request):
    """Manually harvest words from a passage of text or a vault note path."""
    if not self._vl_on():
        return {"error": "vocab loop disabled"}
    body = await request.json()
    text = body.get("text", "")
    path = (body.get("path") or "").strip()
    source = body.get("source", "") or path
    if path and not text:
        try:
            text = strip_frontmatter(await self.read(path))
        except Exception:
            return {"error": "could not read note"}
    added = await self._harvest_text(text, source=source)
    return {"ok": True, "added": added, "count": len(added)}


@web_route("GET", "/api/harvest/inbox")
async def api_harvest_inbox(self, request):
    if not self._vl_on():
        return {"enabled": False, "inbox": []}
    d = self._vl_load()
    return {"enabled": True, "inbox": d["harvest_inbox"]}


@web_route("POST", "/api/harvest/resolve")
async def api_harvest_resolve(self, request):
    """Approve (→ save + SRS) or reject a harvested candidate."""
    if not self._vl_on():
        return {"error": "vocab loop disabled"}
    body = await request.json()
    word = (body.get("word") or "").strip()
    action = body.get("action", "approve")
    if not word:
        return {"error": "word required"}
    d = self._vl_load()
    entry = next((c for c in d["harvest_inbox"] if c.get("word") == word), None)
    if not entry:
        return {"error": "not in inbox"}
    d["harvest_inbox"] = [c for c in d["harvest_inbox"] if c.get("word") != word]
    if word.lower() not in {w.lower() for w in d["harvested_seen"]}:
        d["harvested_seen"].append(word)
    self._vl_save(d)
    if action == "approve":
        await self.save_word(
            word=word,
            definition=entry.get("definition", ""),
            part_of_speech=entry.get("part_of_speech", ""),
            example=entry.get("sentence", ""),
            chinese=entry.get("chinese", ""),
            # The encounter, not just the word. `sentence` is where it was met and
            # `source_url` is the moment it was said; both were staged on the inbox
            # entry and dropped here, so an approved word arrived context-free.
            sentence=entry.get("sentence", ""),
            source_url=entry.get("source_url", ""),
        )
        return {"ok": True, "approved": word}
    return {"ok": True, "rejected": word}


# ── Stage 3: daily breadth feed ───────────────────────────────────────


async def _ensure_feed(self) -> list[dict]:
    """Return today's feed, generating it once per day (LLM, deduped)."""
    d = self._vl_load()
    today = date.today().isoformat()
    feed = d.get("feed") or {}
    if feed.get("date") == today and feed.get("words"):
        return feed["words"]

    known = sorted({w for w in await self._vault_words()})
    knownl = {w.lower() for w in known}
    # Ask for more than 3 and retry once — a large existing deck dedups out most
    # picks, so a single "give me 3" call often yields <3 after filtering.
    clean: list[dict] = []
    have: set[str] = set()
    for round_n in range(2):
        if len(clean) >= 3:
            break
        # exclusion = the deck (capped) + whatever we've already accepted this build
        exclude = (known[: _EXCLUDE_CAP - len(have)]) + sorted(have)
        prompt = (
            "Give me 6 fresh words for today's vocabulary feed (I'll keep the best 3).\n"
            f"Exclusion list (never pick any of these): {', '.join(exclude) if exclude else '(none)'}"
        )
        try:
            resp = await self.think(
                prompt, system=VOCAB_FEED_SYSTEM, domain="text", temperature=0.7
            )
            words = parse_llm_json(resp)
        except Exception:
            words = []
        if not isinstance(words, list):
            words = []
        for w in words:
            if not (isinstance(w, dict) and (w.get("word") or "").strip()):
                continue
            wl = w["word"].lower()
            if wl in knownl or wl in have:
                continue
            clean.append(w)
            have.add(wl)
            if len(clean) >= 3:
                break

    clean = clean[:3]
    d["feed"] = {"date": today, "words": clean}
    self._vl_save(d)
    if clean:
        await self.emit("dictionary:vocab_feed_built", {"count": len(clean)})
    return clean


@web_route("GET", "/api/loop/status")
async def api_loop_status(self, request):
    """Cheap status (no LLM) — drives tab visibility + glance counts."""
    if not self._vl_on():
        return {"enabled": False}
    today = date.today().isoformat()
    srs = self._load_srs()
    due = sum(1 for e in srs.values() if e.get("next_review", today) <= today)
    d = self._vl_load()
    return {
        "enabled": True,
        "due": due,
        "inbox_count": len(d.get("harvest_inbox", [])),
        "production_days": _days_since((d.get("production") or {}).get("last")),
    }


@web_route("GET", "/api/progress")
async def api_progress(self, request):
    """Progress-to-30k tracker: deck size, test-score history, trajectory.

    Snapshots today's deck size into deck_history (once/day) and seeds the
    known test scores on first call. No LLM.
    """
    if not self._vl_on():
        return {"enabled": False}
    today = date.today().isoformat()
    deck_size = len(await self._vault_words())
    d = self._vl_load()

    # Seed historical test scores once.
    if not d["tests"]:
        d["tests"] = [dict(t) for t in _SEED_TESTS]

    # Daily deck-size snapshot (idempotent per day).
    hist = d["deck_history"]
    if not hist or hist[-1].get("date") != today:
        hist.append({"date": today, "count": deck_size})
        d["deck_history"] = hist[-_DECK_HISTORY_CAP:]
    else:
        hist[-1]["count"] = deck_size
    self._vl_save(d)

    tests = sorted(d["tests"], key=lambda t: t.get("date", ""))
    latest = tests[-1] if tests else None
    return {
        "enabled": True,
        "deck_size": deck_size,
        "tests": tests,
        "latest_score": latest["score"] if latest else None,
        "latest_test_date": latest["date"] if latest else None,
        "days_since_test": _days_since(latest["date"]) if latest else None,
        "deck_history": d["deck_history"],
        "north_star": _NORTH_STAR,
        "phase_targets": _PHASE_TARGETS,
    }


@web_route("POST", "/api/progress/log-test")
async def api_log_test(self, request):
    """Record a vocabulary-test result {score, date?}."""
    if not self._vl_on():
        return {"error": "vocab loop disabled"}
    body = await request.json()
    try:
        score = int(body.get("score"))
    except (TypeError, ValueError):
        return {"error": "numeric score required"}
    if score <= 0 or score > 100000:
        return {"error": "score out of range"}
    when = (body.get("date") or date.today().isoformat()).strip()
    d = self._vl_load()
    if not d["tests"]:
        d["tests"] = [dict(t) for t in _SEED_TESTS]
    # Replace a same-date entry, else append.
    d["tests"] = [t for t in d["tests"] if t.get("date") != when]
    d["tests"].append({"date": when, "score": score})
    self._vl_save(d)
    await self.emit("dictionary:vocab_test_logged", {"date": when, "score": score})
    return {"ok": True, "date": when, "score": score}


@web_route("GET", "/api/feed")
async def api_vocab_feed(self, request):
    if not self._vl_on():
        return {"enabled": False, "words": []}
    return {"enabled": True, "words": await self._ensure_feed()}


@web_route("POST", "/api/feed/add")
async def api_feed_add(self, request):
    """Add one of today's feed words to the SRS deck."""
    if not self._vl_on():
        return {"error": "vocab loop disabled"}
    body = await request.json()
    word = (body.get("word") or "").strip()
    if not word:
        return {"error": "word required"}
    d = self._vl_load()
    feed_words = (d.get("feed") or {}).get("words", [])
    entry = next((w for w in feed_words if w.get("word") == word), None)
    if not entry:
        return {"error": "not in today's feed"}
    return await self.save_word(
        word=word,
        definition=entry.get("definition", ""),
        phonetic=entry.get("phonetic", ""),
        part_of_speech=entry.get("part_of_speech", ""),
        example=entry.get("example", ""),
        chinese=entry.get("chinese", ""),
        usage_notes=entry.get("usage_notes", ""),
    )


# ── Stage 4: production reps ──────────────────────────────────────────


@web_route("GET", "/api/production/prompt")
async def api_production_prompt(self, request):
    """Hand back up to 5 review words to actively use in a short piece."""
    if not self._vl_on():
        return {"enabled": False, "words": []}
    try:
        n = max(1, min(10, int(request.query_params.get("n", "5"))))
    except Exception:
        n = 5
    srs = self._load_srs()
    # Prefer words seen at least once (recognised) — production activates them.
    reviewed = [w for w, e in srs.items() if e.get("reviews", 0) >= 1]
    pool = reviewed or list(srs.keys())
    pool.sort(key=lambda w: srs[w].get("reviews", 0))  # least-rehearsed first
    words = pool[:n]
    return {"enabled": True, "words": words}


@web_route("POST", "/api/production/check")
async def api_production_check(self, request):
    """Score a learner's paragraph for correct active use of target words."""
    if not self._vl_on():
        return {"error": "vocab loop disabled"}
    body = await request.json()
    text = (body.get("text") or "").strip()
    words = body.get("words") or []
    if not text:
        return {"error": "text required"}
    if not isinstance(words, list):
        words = []
    prompt = (
        f"Target words: {', '.join(str(w) for w in words)}\n\n"
        f"Learner's writing:\n{text[:4000]}"
    )
    try:
        resp = await self.think(
            prompt, system=VOCAB_PRODUCTION_SYSTEM, domain="text", temperature=0.3
        )
        feedback = parse_llm_json(resp)
    except Exception as e:
        return {"error": f"check failed: {e}"}
    d = self._vl_load()
    today = date.today().isoformat()
    prod = d.get("production") or {}
    prod["last"] = today
    hist = prod.get("history") or []
    hist.append({"date": today, "words": [str(w) for w in words]})
    prod["history"] = hist[-20:]
    d["production"] = prod
    self._vl_save(d)
    return {"ok": True, "feedback": feedback, "provenance": self.last_provenance()}


# ── Hub panel ─────────────────────────────────────────────────────────


async def panel_vocab_today(self):
    """Glanceable: due reviews, weekly-rep nudge, harvest inbox, today's feed."""
    if not self._vl_on():
        return None
    today = date.today().isoformat()
    srs = self._load_srs()
    due = sum(1 for e in srs.values() if e.get("next_review", today) <= today)
    d = self._vl_load()
    inbox = d.get("harvest_inbox", [])
    rows: list[dict] = []

    if due:
        rows.append({
            "title": f"\U0001F4DA {due} words due for review",
            "subtitle": "Spaced-repetition deck",
            "href": "/dictionary/#practice",
        })
    if _days_since((d.get("production") or {}).get("last")) >= 7:
        rows.append({
            "title": "✍️ Weekly writing rep ready",
            "subtitle": "Use your review words in a paragraph",
            "href": "/dictionary/#writing",
        })
    if inbox:
        rows.append({
            "title": f"\U0001F33E {len(inbox)} harvested from your reading",
            "subtitle": "Review & add to your deck",
            "href": "/dictionary/#today",
        })
    try:
        feed = await self._ensure_feed()
    except Exception:
        feed = []
    for w in feed[:3]:
        rows.append({
            "title": w.get("word", ""),
            "subtitle": (w.get("definition", "") or "")[:80],
            "href": "/dictionary/#today",
        })
    return rows or None
