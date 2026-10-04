"""soundcheck — clip synthesis, caching and serving.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the voice-engine probe, the content-addressed clip cache, the
lookahead prewarm, and ``GET /api/audio/{filename}``. Source of truth for
*whether this session can use sound, and where a clip lives*.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``engine``/``bank_loader`` only through the session
dict it is handed. Do not import from ``.app`` (it imports us, which would cycle).

## Synthesis is never on the click path

A round's audio is looked up, not generated. Clips are content-addressed by
``(text, voice, speed)`` so the same word is synthesised once ever, and the
first few rounds of a session are warmed before the first question is served.
Everything after that rides the verdict response, which carries the *next*
round's URLs — so round N+1 is fetching while the learner is still reading the
verdict for round N.

## The probe decides the whole session, once

``probe_audio`` runs at session start and its answer is frozen into the plan.
Discovering a dead voice engine at round seven would mean rewriting a plan the
learner is halfway through; deciding up front means a silent install gets a
coherent eye-only session instead of a game that degrades under them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.tts_cache import cache_path_for, speak_cached

if TYPE_CHECKING:
    from .app import SoundcheckApp  # noqa: F401 — for type hints only


# ─── Bind to SoundcheckApp class as ────────────────────────────────
#   _clip_dir        = _audio._clip_dir
#   _clip_path       = _audio._clip_path
#   _clip_url        = _audio._clip_url
#   _synthesise      = _audio._synthesise
#   _probe_audio     = _audio._probe_audio
#   _audio_for_next  = _audio._audio_for_next
#   _prewarm         = _audio._prewarm
#   api_audio        = _audio.api_audio
#   _clip_name       = _audio._clip_name
#   _prewarm_candidates = _audio._prewarm_candidates
#   _speed           = _audio._speed
#   _voice           = _audio._voice
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────

# How many upcoming rounds to warm at session start. Four is enough to cover the
# opening plus the learner's reading time; more just delays the first question.
PREWARM = 4


def _clip_dir(self) -> Path:
    d = self.data_subdir("clips")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clip_path(self, text: str) -> Path:
    """Where this phrase's clip lives, at the current voice and speed.

    Both are part of the address: leave either out and switching voice silently
    serves the old clip, which reads as the setting being ignored rather than as
    a stale cache.

    Takes no voice/speed arguments on purpose. The key must be byte-identical to
    the one ``speak_cached`` computes, and the way that drifts is two call sites
    normalising the same setting differently — a float ``1.0`` stringifies to
    "1.0" while the raw setting "1" does not, so every lookup misses and every
    word is synthesised twice, into a path nobody reads. One reader, one key.
    """
    return cache_path_for(self._clip_dir(), text, "auto",
                          self._voice() or "_default", str(self._speed() or ""),
                          "", "", prefix="sc")


def _clip_name(self, text: str) -> str:
    return self._clip_path(text).name


def _clip_url(self, text: str) -> str:
    return f"/soundcheck/api/audio/{self._clip_name(text)}"


def _voice(self) -> str:
    return str(self.setting_or_config("soundcheck.voice", "") or "")


def _speed(self) -> float | None:
    """The clip speed as a number, or ``None`` when it cannot be read as one."""
    try:
        return float(self.setting_or_config("soundcheck.speed", "1.0") or 1.0)
    except (TypeError, ValueError):
        return None


async def _synthesise(self, text: str) -> str:
    """Return a servable URL for ``text``, synthesising only on a cache miss.

    Returns ``""`` on any failure rather than raising. A missing clip degrades
    that one round; an exception would take down the session.
    """
    text = (text or "").strip()
    if not text:
        return ""
    # source="lesson": every caller passes a word or word pair from the
    # committed item bank (or the literal probe word), never learner input —
    # which is what lets the hosted build's speech guard admit it to a cloud
    # voice. tests/test_unit_englishos_cloud_plans.py pins the call sites, so a
    # new caller has to be reviewed against that claim.
    dest = await speak_cached(self, text, cache_dir=self._clip_dir(),
                              voice=self._voice(), speed=self._speed(),
                              prefix="sc", source="lesson")
    return self._clip_url(text) if dest else ""


async def _probe_audio(self) -> bool:
    """Is a voice engine reachable right now?

    Answered by actually synthesising a word, not by asking the capability
    whether it has providers — a registered-but-unreachable provider is the
    common failure (edge-tts needs the network), and it only shows up on use.
    """
    if not bool(self.setting_or_config("soundcheck.audio", True)):
        return False
    try:
        return bool(await self._synthesise("check"))
    except Exception:
        return False


async def _audio_for_next(self, session: dict) -> dict[str, str]:
    """Clip URLs for the words the next round might need.

    The round is not built yet, so this warms the plausible candidates rather
    than the exact ones — cheap, because a hit costs a dict lookup and a miss
    costs one synthesis that the next round would have paid for anyway.
    """
    urls: dict[str, str] = {}
    for item in self._prewarm_candidates(session, PREWARM):
        for word in item.get("words") or []:
            text = word.get("text", "")
            if text and text not in urls:
                url = await self._synthesise(text)
                if url:
                    urls[text] = url
    return urls


def _prewarm_candidates(self, session: dict, n: int) -> list[dict]:
    """A cheap slice of the pool, filtered the way selection will filter it."""
    plan = session.get("plan") or {}
    dims = set(plan.get("dimensions") or [])
    shapes = set(plan.get("shapes") or [])
    seen = {r.get("item_id") for r in session.get("rounds") or []}
    out = []
    for item in self._bank.get("items") or []:
        if item.get("id") in seen:
            continue
        if dims and item.get("dimension") not in dims:
            continue
        if shapes and item.get("shape") not in shapes:
            continue
        out.append(item)
        if len(out) >= n * 4:
            break
    return out


async def _prewarm(self, session: dict) -> None:
    """Background warm for the rest of the session's likely words."""
    try:
        await self._audio_for_next(session)
    except Exception:
        pass


@web_route("GET", "/api/audio/{filename}")
async def api_audio(self, request):
    """Serve a cached clip. Traversal-guarded by the shared helper."""
    name = request.path_params.get("filename", "")
    return self.serve_data_file("clips", name, media_type="audio/mpeg")
