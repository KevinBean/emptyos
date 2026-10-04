"""Picture Dictionary — hear the word, then say it back and be scored.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md rule 4).
Owns: the cached reference clip for an animal's name, the recorded-attempt upload,
and scoring that attempt.

Two contracts worth stating, both learned from ``audio-course/practice.py``:

* ``pronounce`` is best-effort. The wav2vec2 service is frequently absent, so a
  missing scorer must **degrade the score, never fail the attempt** — we fall back
  to word-level accuracy via ``listen`` and say which scorer ran, so the number on
  screen is never silently a different measurement than it claims.
* A weak score never grades the review card **down**. A bad microphone should not
  wreck a schedule the learner did not actually fail.

Cross-module callers reach these via ``self.X`` after re-binding.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import difflib
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.audio import score_fluency, words_used  # noqa: F401 — words_used re-exported
from emptyos.sdk.media.audio import (ONSET_FRAME_S, decode_levels, pauses_from_levels,
                                     speech_onset, speech_span)
from emptyos.sdk.scoring import alignment_to_events
from emptyos.sdk.tts_cache import speak_cached

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only


# ─── Bind to DictionaryApp class as ─────────────────────────────────
#   api_picture_say           = _speak.api_say
#   api_picture_clip          = _speak.api_clip
#   api_picture_speak_upload  = _speak.api_speak_upload
#   api_picture_speak_attempt = _speak.api_speak_attempt
#   api_picture_speak_fast    = _pic_speak.api_picture_speak_fast
#   _score_spoken             = _pic_speak._score_spoken
#   _setting_float            = _pic_speak._setting_float
#   api_picture_talk          = _pic_speak.api_picture_talk
#   _attempt_audio            = _pic_speak._attempt_audio
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


# Seconds from the picture appearing to the first sound that still count as
# "fast". A practice target for automatic recall, not a research figure —
# the learner can change it (picture-dict.fast_seconds).
DEFAULT_FAST_SECONDS = 2.0

# One word takes local whisper well under a second (a 22 s answer measured
# 2.3 s), so this only bites when the local engine is down and the chain falls
# through to a slower provider. Bounded because a round is ten cards.
FAST_LISTEN_TIMEOUT_S = 15


def word_score(target: str, heard: str) -> float:
    """Fallback accuracy when phoneme scoring is unavailable — a plain
    similarity over normalised words, so the number stays honest about being
    coarser than a phone-level score."""
    def norm(s: str) -> list[str]:
        return re.findall(r"[a-z']+", (s or "").lower())
    a, b = norm(target), norm(heard)
    if not a:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)


def said_the_word(target: str, heard: str) -> bool:
    """Did this transcript contain the target word?

    A whole-word match, with plurals, accents and hyphens tolerated (see
    ``words_used``) — and nothing looser. A character-similarity fallback was
    tried, to forgive a transcript like "giraff" for "giraffe", and removed:
    run over all 508 pack names, a 0.85 similarity bar accepted 11 pairs of
    genuinely different objects (owl/bowl, cow/crow, nail/snail, pea/pear,
    lamp/clamp, car/card, hose/horse…), because the ratio quantises coarsely
    on short words. Marking a bowl as an owl is worse than asking for one
    retry, and the learner sees the transcript either way.
    """
    return bool(heard) and bool(words_used(heard, [target]))


@web_route("GET", "/api/picture/say/{slug}")
async def api_picture_say(self, request):
    """A cached spoken reference for the animal's name."""
    slug = request.path_params.get("slug", "")
    item = self.items.get(slug)
    if not item:
        return {"error": f"unknown animal '{slug}'"}
    path = await speak_cached(self, item["name"],
                              cache_dir=self.data_subdir("clips"),
                              prefix="pd", language="en", source="word")
    if not path:
        return {"error": "no voice engine is available on this machine"}
    return {"audio_url": f"/dictionary/api/picture/clip/{path.name}", "text": item["name"]}


@web_route("GET", "/api/picture/clip/{name}")
async def api_picture_clip(self, request):
    return self.serve_data_file("clips", request.path_params.get("name", ""),
                                media_type="audio/mpeg")


@web_route("POST", "/api/picture/speak/upload")
async def api_picture_speak_upload(self, request):
    form = await request.form()
    upload = form.get("audio")
    if upload is None or not hasattr(upload, "read"):
        return {"error": "no audio was uploaded"}
    path, name = await self.save_audio_upload(upload, subdir="attempts", prefix="say")
    return {"audio_path": str(path), "filename": name}


@web_route("POST", "/api/picture/speak/attempt")
async def api_picture_speak_attempt(self, request):
    body = await request.json() if await request.body() else {}
    return await self._score_spoken(str(body.get("slug") or ""),
                                    str(body.get("audio_path") or ""))


def _setting_float(self, key: str, default: float) -> float:
    try:
        return float(self.setting_or_config(key, default))
    except (TypeError, ValueError):
        return default


@web_route("POST", "/api/picture/speak/fast")
async def api_picture_speak_fast(self, request):
    """One card of a speed round: was the word right, and how fast did it come?

    The page shows the picture only once recording has started, so the silence
    before the first sound in the recording IS the reaction time — measured
    from the audio, not from a browser timer. Right and within the time limit
    counts as a review of a due card. Slow or wrong adds a card to Review if
    it is not there yet, and never grades it down (same rule as a normal
    spoken attempt).

    **The verdict asks whether the right WORD came out, so it is judged on
    what was heard, not on pronunciation accuracy.** Judging it by the phoneme
    score (the Speak tab's measure, pass mark 0.85) marked a real round 0 of
    10 — "peacock", clearly said, scored 0.6 and read as a miss. With no
    transcript available at all, that score is still the only signal there is,
    and the verdict falls back to it (``judged_on``).

    The schedule answers to both skills: a word recalled but said below the
    pass mark is not graded forward and lands in Review, where the Speak tab
    drills pronunciation. Nothing here ever grades a card down.
    """
    body = await request.json() if await request.body() else {}
    slug = str(body.get("slug") or "")
    audio_path = str(body.get("audio_path") or "")
    res = await self._score_spoken(slug, audio_path, grade=False)
    if res.get("error"):
        return res

    target = self.items[slug]["name"]
    heard = (res.get("heard") or "").strip()
    if not heard and res.get("scorer") == "phones":
        # The phoneme scorer does not transcribe, so ask for the words — once,
        # and bounded: with no local speech-to-text the chain can fall through
        # to a 30 s browser provider, inside an HTTP handler.
        try:
            heard = (await asyncio.wait_for(self.listen(audio_path),
                                            FAST_LISTEN_TIMEOUT_S) or "").strip()
        except Exception:
            heard = ""
    if heard:
        said, judged_on = said_the_word(target, heard), "words"
    elif res.get("scorer") == "words":
        said, judged_on = False, "words"        # it transcribed, and heard nothing
    else:
        # No transcript at all: fall back to the pronunciation verdict rather
        # than calling every card a miss.
        said, judged_on = bool(res["passed"]), "pronunciation"
    res = {**res, "heard": heard, "said_word": said, "judged_on": judged_on}

    onset = await speech_onset(audio_path)
    limit = self._setting_float("picture-dict.fast_seconds", DEFAULT_FAST_SECONDS)
    if not res["said_word"]:
        verdict = "missed"
    elif onset is None:
        # Speed unmeasurable (no ffmpeg, or nothing audible): the word was
        # right, so say that without claiming a time.
        verdict = "right"
    else:
        verdict = "fast" if onset <= limit else "slow"

    # A round is extra practice, not a review session: it grades a card only
    # when the card is due (an early "good" would stretch the interval every
    # round), and it adds a slow or missed card to Review only if it is not
    # already there — it never moves an existing schedule, down or up.
    entry = self.load_picture_progress().get(slug) or {}
    srs = entry.get("srs") if isinstance(entry.get("srs"), dict) else None
    today = date.today().isoformat()
    due = srs is not None and (srs.get("next_review") or today) <= today

    # The verdict is about recall, but the schedule still answers to both
    # skills: a word recalled and said clumsily is not "remembered", so it is
    # not graded forward, and it lands in Review where the Speak tab can drill
    # the pronunciation. Without a phoneme score there is nothing to weigh.
    pron_weak = res.get("scorer") == "phones" and not res.get("passed")
    remembered = verdict in ("fast", "right")

    graded, enrolled = None, False
    if remembered and not pron_weak:
        if due:
            g = await self.picture_grade(slug, "good")
            graded = g.get("next_review") if isinstance(g, dict) else None
    elif srs is None:
        enrolled = bool((await self.picture_enroll(slug)).get("added"))

    async with self._prog_lock:
        prog = self.load_picture_progress()
        fast = prog.setdefault(slug, {}).setdefault("speak", {}).setdefault("fast", {})
        fast["rounds"] = int(fast.get("rounds", 0)) + 1
        fast["last_verdict"] = verdict
        if onset is not None:
            fast["last_onset"] = onset
            if verdict == "fast":
                prev = fast.get("best_onset")
                fast["best_onset"] = onset if prev is None else min(onset, float(prev))
        self.save_picture_progress(prog)

    return {**res, "onset_s": onset, "limit_s": limit, "verdict": verdict,
            "next_review": graded, "enrolled": enrolled,
            "pronunciation_weak": pron_weak,
            "in_review": srs is not None or enrolled}


# "Talk about it" asks for at least this much speaking. Under it the answer is
# still scored, with a note to keep going; a practice target, not a test rule.
# pages/talk-about.js carries the same number as TALK_ABOUT_TARGET_S for the
# live clock; the result screen reads this one from the response.
TALK_TARGET_SECONDS = 30

# Local whisper transcribed a 22 s answer in 2.3 s (measured 2026-09-22), so
# 60 s takes a few seconds. Past this the request nears the web server's own
# ~30 s limit; give up and say so instead of letting it 500. The whisper
# provider is an HTTP call, so cancelling it leaves no process behind.
TALK_LISTEN_TIMEOUT_S = 25


def _attempt_audio(self, audio_path: str) -> Path | None:
    """The uploaded recording, only if it really is one of this app's uploads.

    The speak routes take a server path back from the client (the upload
    route hands it out). Resolving it inside ``data_dir/attempts`` keeps a
    caller from pointing speech-to-text or ffmpeg at any other file.
    """
    if not audio_path:
        return None
    try:
        root = (self.data_dir / "attempts").resolve()
        path = Path(audio_path).resolve()
        path.relative_to(root)
    except (ValueError, OSError):
        return None
    return path if path.is_file() else None


@web_route("POST", "/api/picture/talk")
async def api_picture_talk(self, request):
    """Score 30–60 seconds of free speech about one picture.

    Fluency is Speaking Practice's scorer (``score_fluency``: pace, fillers,
    immediate repeats, long pauses). Pace and pauses come from the recording
    itself: the span from the first spoken sound to the last, and the silent
    gaps inside it. Pauses the room's noise makes unmeasurable are reported as
    unmeasured, never as none. Also reports whether the learner said the
    object's name and which other words from the chosen pack they used.
    Practice only: it never touches the review schedule.
    """
    body = await request.json() if await request.body() else {}
    slug = str(body.get("slug") or "")
    item = self.items.get(slug)
    if not item:
        return {"error": f"unknown animal '{slug}'"}
    audio = self._attempt_audio(str(body.get("audio_path") or ""))
    if audio is None:
        return {"error": "That recording could not be found — record again."}

    levels = await decode_levels(audio)
    if not levels:
        # Without the audio's loudness there is no duration, so no pace — and a
        # score built on 0 seconds would be a made-up 1/5. Say so; save nothing.
        return {"error": "This recording could not be measured on this machine "
                         "(ffmpeg is needed) — the answer was not scored."}
    try:
        transcript = (await asyncio.wait_for(self.listen(str(audio)),
                                             TALK_LISTEN_TIMEOUT_S) or "").strip()
    except asyncio.TimeoutError:
        return {"error": "Transcribing took too long — try a shorter answer."}
    except Exception:
        transcript = ""
    if not transcript:
        return {"error": "Nothing was heard — try again, a little closer to the microphone."}

    span = speech_span(levels)
    pauses = pauses_from_levels(levels)
    if span:
        seconds = round(span[1] - span[0], 2)
    else:   # no speech stands out of the room: fall back to the whole clip
        seconds = round(len(levels) * ONSET_FRAME_S, 2)
    fluency = score_fluency(transcript, seconds, pauses=pauses or [])

    pack = str(body.get("pack") or "")
    if pack not in item.get("packs", []):
        pack = item.get("pack", "")
    scene = [self.items[s]["name"] for s in self.by_pack.get(pack, [])
             if s != slug and s in self.items]
    used = words_used(transcript, scene)
    said_name = bool(words_used(transcript, [item["name"]]))

    async with self._prog_lock:
        prog = self.load_picture_progress()
        talk = prog.setdefault(slug, {}).setdefault("talk", {})
        talk["attempts"] = int(talk.get("attempts", 0)) + 1
        talk["last_score"] = fluency["score"]
        talk["best_score"] = max(fluency["score"], float(talk.get("best_score") or 0.0))
        talk["last_seconds"] = seconds
        talk["last_wpm"] = fluency["metrics"]["wpm"]
        self.save_picture_progress(prog)

    return {
        "ok": True, "slug": slug, "name": item["name"], "transcript": transcript,
        "seconds": seconds, "target_seconds": TALK_TARGET_SECONDS,
        "fluency": fluency, "pauses_measured": pauses is not None,
        "said_name": said_name, "scene_words_used": used,
        "scene_pack": pack, "scene_word_count": len(scene),
    }


async def _score_spoken(self, slug: str, audio_path: str, *, grade: bool = True) -> dict:
    """Score one recorded attempt at a picture's name and record it.

    ``grade=True`` counts a pass as remembered (the normal Speak tab);
    the speed round passes False and decides the grade itself.
    """
    item = self.items.get(slug)
    if not item:
        return {"error": f"unknown animal '{slug}'"}
    audio = self._attempt_audio(audio_path)
    if audio is None:
        return {"error": "That recording could not be found — record again."}
    audio_path = str(audio)

    target = item["name"]
    heard = ""
    payload: dict | None = None
    try:
        payload = await self.pronounce(audio_path, target)
    except Exception as e:
        payload = {"unavailable": True, "reason": str(e)}

    if payload and not payload.get("unavailable"):
        score = float(payload.get("summary", {}).get("phone_accuracy") or 0.0)
        scorer = "phones"
        weak = list(payload.get("summary", {}).get("weak_phones") or [])[:6]
        # Telemetry must never break a score.
        #
        # A direct call since the merge. This was `try_call_app("dictionary",
        # ...)` when the packs were their own app; that would still work through
        # the registry, but routing a call to ourselves via the kernel is pure
        # indirection. `source` stays "picture-dict" so the rows already in
        # pronounce-events.jsonl keep meaning the same thing.
        try:
            events = alignment_to_events(payload)
            if events:
                await self.log_pronounce_events(
                    events=events, source="picture-dict", sentence=target)
        except Exception:
            pass
    else:
        try:
            heard = await self.listen(audio_path)
        except Exception:
            heard = ""
        score, scorer, weak = word_score(target, heard), "words", []

    async with self._prog_lock:
        prog = self.load_picture_progress()
        entry = prog.setdefault(slug, {})
        sp = entry.setdefault("speak", {})
        sp["attempts"] = int(sp.get("attempts", 0)) + 1
        sp["last"] = score
        sp["best"] = max(score, float(sp.get("best") or 0.0))
        sp["scorer"] = scorer
        if weak:
            sp["weak_phones"] = weak
        self.save_picture_progress(prog)

    graded = None
    pass_score = self._setting_float("picture-dict.pass_score", 0.85)
    if grade and score >= pass_score:
        # Only ever grades UP. A poor attempt is recorded, never scheduled down.
        res = await self.picture_grade(slug, "good")
        graded = res.get("next_review") if isinstance(res, dict) else None

    return {
        "ok": True, "slug": slug, "target": target, "score": round(score, 4),
        "scorer": scorer, "heard": heard, "weak_phones": weak,
        "passed": score >= pass_score, "next_review": graded,
        "scorer_note": ("" if scorer == "phones"
                        else "Phoneme scoring is unavailable — this is word accuracy only."),
    }
