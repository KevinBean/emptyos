"""Synthesise a phrase once, then serve it from disk forever.

Extracted at the third consumer, past the second-consumer trigger in CLAUDE.md
rule 9. The two hand-rolls this generalises are ``reader/app.py::_speak_cached``
and ``radio/chatter.py::speak_to_cache``, and each had learned something the
other had not:

* **radio** knew that ``speak()`` does not have one return type. Providers hand
  back a path string, a ``Path``, raw bytes, or a dict carrying ``path`` —
  depending on which one answered. Anything treating the result as a path is
  correct until the day a different provider wins the chain.
* **reader** knew that a cache key must include the *provider*, and that a
  local-only caller must never silently fall through to a cloud one. Its
  provider-pinning walk is genuinely richer than this and stays where it is;
  ``local_only`` here is the small honest version of the same intent.

Stdlib plus the app handle only. No kernel import, so a caller can be tested
without booting one.

## What this deliberately does not do

**No expiry, no eviction.** A clip is a pure function of its text and voice, so
it never goes stale, and a bounded cache would mean re-synthesising the words a
learner meets most. Callers that need a bound should scope their own directory
and delete it.

**No concurrency lock.** Two tasks synthesising the same phrase both write the
same bytes to the same path; the loser wastes one call. A lock would cost every
caller a shared registry to prevent a duplicate render nobody can hear.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from emptyos.speechlang import detect_speech_language

__all__ = ["cache_key", "cache_path_for", "speak_cached", "coerce_audio"]


def cache_key(text: str, *parts: str) -> str:
    """Content address for a phrase plus whatever else changes its audio.

    Parts are separated by a byte that cannot occur in any of them, so
    ``("a", "bc")`` and ``("ab", "c")`` cannot collide into one key.
    """
    h = hashlib.sha256()
    h.update((text or "").encode("utf-8"))
    for part in parts:
        h.update(b"\x1f")
        h.update((part or "").encode("utf-8"))
    return h.hexdigest()[:24]


def cache_path_for(cache_dir: Path | str, text: str, *parts: str,
                   prefix: str = "tts", suffix: str = ".mp3") -> Path:
    """Where a given phrase's clip lives.

    The provider and voice belong in ``parts``. Leaving them out means switching
    voice silently serves the old one — the failure looks like the setting was
    ignored, and the real cause is a cache key that never mentioned it.
    """
    return Path(cache_dir) / f"{prefix}-{cache_key(text, *parts)}{suffix}"


def coerce_audio(result) -> tuple[Path | None, bytes | None]:
    """Normalise whatever ``speak()`` returned into ``(path, bytes)``.

    The single most portable thing in this module. ``speak()`` is a capability
    with several providers, and they do not agree on a return type: edge-tts and
    kokoro yield a path, others yield bytes, and some yield a dict. Code written
    against whichever provider was active at the time works until the chain
    picks a different one, and then fails in a way that looks like the voice
    engine broke.
    """
    if result is None:
        return None, None
    if isinstance(result, bytes):
        return None, result
    if isinstance(result, (str, Path)):
        candidate = Path(str(result))
        return (candidate if candidate.is_file() else None), None
    if isinstance(result, dict):
        raw = result.get("path") or result.get("audio_path") or result.get("file")
        if raw:
            candidate = Path(str(raw))
            if candidate.is_file():
                return candidate, None
        data = result.get("audio") or result.get("bytes")
        if isinstance(data, bytes):
            return None, data
    return None, None


async def speak_cached(
    app,
    text: str,
    *,
    cache_dir: Path | str,
    voice: str = "",
    speed: float | None = None,
    language: str = "",
    prefix: str = "tts",
    suffix: str = ".mp3",
    max_chars: int = 2000,
    local_only: bool = False,
    extra_key: str = "",
    source: str = "",
) -> Path | None:
    """Return the cached clip for ``text``, synthesising only on a miss.

    Returns ``None`` on any failure rather than raising. A missing clip should
    degrade one round or one line; an exception would take down whatever is
    playing.

    ``local_only`` restricts synthesis to providers that run on this machine.
    It is a privacy control, not a performance one: a caller that means "never
    send this text off the box" must not be quietly satisfied by a cloud
    provider winning the chain. When no local provider is available it returns
    ``None`` — refusing is the point.

    ``source`` (``"word"`` / ``"lesson"``) is passed to ``BaseApp.speak``; a
    build that sets ``[speech] cloud_sources`` sends only those to a cloud
    voice (``emptyos/capabilities/speech_guard.py``).
    """
    text = (text or "").strip()
    if not text:
        return None

    cache_dir = Path(cache_dir)
    provider_tag = "local" if local_only else "auto"
    # The routed language is part of the key because it now selects the
    # PROVIDER, not just a voice: a Chinese clip synthesised before Chinese was
    # routed away from kokoro hashes identically afterwards, so the fix would
    # never reach anything already spoken and would look like it worked because
    # new phrases came out fine. Only non-English is tagged, so English keys are
    # unchanged and nothing already cached is re-rendered needlessly.
    lang_tag = detect_speech_language(text)
    parts = [provider_tag, voice or "_default", str(speed or ""), language, extra_key]
    if lang_tag != "en":
        parts.append(f"lang:{lang_tag}")
    dest = cache_path_for(cache_dir, text, *parts, prefix=prefix, suffix=suffix)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest

    kwargs: dict = {}
    if voice:
        kwargs["voice"] = voice
    if speed is not None:
        kwargs["speed"] = speed
    if language:
        kwargs["language"] = language
    if source:
        kwargs["source"] = source

    try:
        if local_only:
            result = await _speak_local(app, text[:max_chars], kwargs)
        else:
            result = await app.speak(text[:max_chars], **kwargs)
    except Exception:
        return None

    src, raw = coerce_audio(result)
    if src is None and raw is None:
        return None

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw if raw is not None else src.read_bytes())
    except OSError:
        return None
    return dest


async def _speak_local(app, text: str, kwargs: dict):
    """Synthesise through on-machine providers only.

    Walks the ``speak`` chain and skips anything the kernel classifies as cloud,
    rather than naming providers — a name list goes stale the moment a new local
    engine is added, and the classification is already maintained centrally for
    the consent gate.
    """
    try:
        cap = app.kernel.capabilities.get("speak")
    except Exception:
        return None
    for provider in list(getattr(cap, "providers", []) or []):
        if getattr(provider, "is_cloud", False):
            continue
        try:
            if not await provider.available():
                continue
            result = await provider.execute(text=text, **kwargs)
            if result:
                return result
        except Exception:
            continue
    return None
