"""Safeguards on text sent to a cloud voice (edge-tts, or any cloud `speak`
provider).

edge-tts is Microsoft's speech endpoint reached through an unofficial client:
no contract, no SLA, and the text sent is exactly the text to be read aloud. So
before a line goes to any cloud voice, and around the send, four rules apply:

1. **Scan before send** — the text runs through ``outbound_scan.scan_outbound``
   (credential shapes + the operator's ``.eos-personal`` patterns) plus two
   speech-only shapes a learner's own text is likely to carry: an email
   address and a phone number. Any hit keeps the line on the machine.
2. **Source and length limits** — only where ``[speech] cloud_sources`` is set
   (the hosted learner build). A call must declare what it is reading
   (``BaseApp.speak(text, source=...)``): a ``word`` (up to three letters-only
   words, the longest picture name) or ``lesson`` content. Anything
   undeclared — a learner's notes, free text, a lesson video narrated from
   their notes — stays local. ``[speech] cloud_max_chars`` caps one call.
3. **Fallback on failure** — a failing or hung send (``SEND_TIMEOUT_S`` in the
   edge-tts plugin) falls through to the next local provider. Each failure is
   audited and raises an alert, at most one per ``ALERT_INTERVAL_S``.
4. **Kill switch + audit log** — the Settings switch turns every cloud voice
   off at once (read per call). ``[speech] cloud_enabled = false`` (env
   ``EOS_SPEECH_CLOUD_ENABLED``) does the same for an operator, from the next
   daemon start, since config and env are loaded at boot. Any one off wins.
   Each send (written before the text leaves), refusal and failure is appended
   to ``data/speech/cloud-audit.jsonl`` with its length, calling app and time,
   never the text.

Where it runs: every cloud ``speak`` send goes through ``call_direct()``,
which asks ``refusal()`` immediately before the send and writes the audit
rows. The speak chain calls it for each cloud voice, and also asks once
before consent so a refused line skips every cloud voice whatever the chain
order; ``pinned_execute`` and reader, which invoke a provider themselves, call
it too. A refused line with no local voice raises ``SpeechKeptLocal``; on the
hosted build there is no on-device voice, so it gets no audio and says so, the
accepted beta limit (plan englishos-cloud-ai T6).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from pathlib import Path

# The Settings switch, read on every call.
CLOUD_SETTING = "speech.cloud_enabled"
CLOUD_SETTING_LABEL = "Use the online voice"
# "word" means a headword or a picture name. The dictionary's 509 picture
# names run to at most 3 words and 19 characters (measured 2026-09-28), so 3
# words / 32 characters admits every one of them and no sentence. A token is
# letters (accented ones included: café, naïve) with inner apostrophes or
# hyphens; digits and other symbols make it not a word.
WORD_MAX_TOKENS = 3
WORD_MAX_CHARS = 32
_WORD_TOKEN = re.compile(r"[^\W\d_](?:[^\W\d_]|['’-])*")
# How often a continuing outage may alert. Not measured: it bounds operator
# noise to six alerts an hour while the voice stays down.
ALERT_INTERVAL_S = 600.0
AUDIT_FILE = Path("speech") / "cloud-audit.jsonl"

# Personal shapes outbound_scan does not carry, because its patterns are the
# operator's own; a learner's line is more likely to hold these. Phone
# numbers: one starting with "0" or "(0" and running to 9+ digits (Australian
# with an area or mobile prefix), one starting with "+" and running to 8+
# digits (international), and the US 3-3-4 shape. Dates, years, decimals and
# ISBNs are left alone. Not caught: 7- or 8-digit local numbers with no prefix
# (345 6789, 9876 5432), numbers spelled out, and "name at host dot com".
_SPEECH_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Email address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("Phone number", re.compile(r"(?<![\w.])(?:\+|\(?0)\d(?:[\s().-]{0,3}\d){7,13}(?!\d)")),
    ("Phone number", re.compile(r"(?<![\w.-])\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w-])")),
]

REASONS = {
    "switched_off": "the online voice is switched off",
    "sensitive": "the text looks like it holds personal or secret data",
    "source": "only lesson content and single words go to the online voice",
    "length": "the text is longer than the online voice accepts in one call",
    "config": "the speech settings are not readable, so nothing goes online",
}


def learner_message(code: str) -> str:
    """What a learner is told when a refused line has no voice on the device."""
    if code == "switched_off":
        return ("The online voice is switched off, and no voice is available on "
                "this device to read this.")
    return "Audio for this is made only on this device, and no voice is available here."


class SpeechKeptLocal(RuntimeError):
    """This text must not go to a cloud voice. The chain skips every cloud
    provider (``keep_local``) and tries local ones only."""

    keep_local = True

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"kept on this device: {REASONS.get(code, code)}")


def _is_off(value) -> bool:
    """True only for an explicit "off" value (false/0/no/off). Anything else
    leaves the switch as it is, since its default is on."""
    if isinstance(value, bool):
        return value is False
    if isinstance(value, int | float):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in ("0", "false", "no", "off")
    return False


def _parse_sources(raw) -> frozenset[str] | None:
    """The configured sources; None for a value that is not a list or a
    comma string, which the guard then treats as "refuse everything"."""
    if raw in (None, "", (), []):
        return frozenset()
    if isinstance(raw, str):
        items = raw.split(",")
    elif isinstance(raw, list | tuple | set | frozenset):
        items = raw
    else:
        return None
    return frozenset(s for s in (str(x).strip().lower() for x in items) if s)


def sensitive_findings(text: str) -> list[str]:
    """Names of the patterns that hit in `text` (never the matched values)."""
    from emptyos.capabilities.outbound_scan import scan_outbound

    names = [f.pattern_name for f in scan_outbound(text)]
    names += [name for name, pat in _SPEECH_PATTERNS if pat.search(text)]
    return names


class _NoConfig:
    def get(self, key, default=None):
        return default


class SpeechGuard:
    def __init__(
        self,
        *,
        config=None,
        settings=None,
        audit_path: Path | None = None,
        warn: Callable[[str], None] | None = None,
        notify: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        # Read per call rather than cached, so a test or a reloaded config
        # sees the current value; the daemon loads config and env at boot.
        self._config = config if config is not None else _NoConfig()
        self._settings = settings
        self.audit_path = audit_path
        self._warn = warn
        self._notify = notify
        self._clock = clock
        self._last_alert = float("-inf")

    @classmethod
    def from_config(cls, config, settings=None, *, warn=None, notify=None) -> SpeechGuard:
        data_dir = getattr(config, "data_dir", None)
        return cls(config=config, settings=settings,
                   audit_path=Path(data_dir) / AUDIT_FILE if data_dir else None,
                   warn=warn, notify=notify)

    # ── configuration, read per call ──────────────────────────────────────

    @property
    def sources(self) -> frozenset[str] | None:
        return _parse_sources(self._config.get("speech.cloud_sources", ()))

    @property
    def max_chars(self) -> int:
        try:
            return max(0, int(self._config.get("speech.cloud_max_chars", 0) or 0))
        except (TypeError, ValueError):
            return -1  # unreadable: refuse (see refusal)

    def operator_off(self) -> bool:
        return _is_off(self._config.get("speech.cloud_enabled", True))

    def switched_off(self) -> bool:
        if self.operator_off():
            return True
        if self._settings is None:
            return False
        try:
            value = self._settings.get(CLOUD_SETTING, True)
        except Exception:
            return True  # an unreadable switch fails closed
        return _is_off(value)

    # ── the rules ─────────────────────────────────────────────────────────

    def refusal(self, text: str, source: str | None = None) -> str | None:
        """Why `text` must stay on the machine, as a REASONS key, or None."""
        if self.switched_off():
            return "switched_off"
        text = text or ""
        sources, max_chars = self.sources, self.max_chars
        if sources is None or max_chars < 0:
            return "config"
        if sources:
            src = (source or "").strip().lower()
            if src not in sources:
                return "source"
            if src == "word":
                tokens = text.split()
                if (not tokens or len(tokens) > WORD_MAX_TOKENS
                        or len(text.strip()) > WORD_MAX_CHARS
                        or not all(_WORD_TOKEN.fullmatch(t) for t in tokens)):
                    return "source"
        if max_chars and len(text) > max_chars:
            return "length"
        if sensitive_findings(text):
            return "sensitive"
        return None

    # ── audit + alert ─────────────────────────────────────────────────────

    def _write_row(self, row: dict) -> None:
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError as e:
            if self._warn:
                self._warn(f"speech audit log not written: {e}")

    async def record(self, *, provider: str, chars: int, app: str | None,
                     outcome: str, source: str | None = None) -> None:
        """Append one audit row, off the event loop. Never the text; a write
        failure never blocks speech."""
        if self.audit_path is None:
            return
        row = {
            "ts": round(self._clock(), 3),
            "provider": provider,
            "app": app or "",
            "source": source or "",
            "chars": int(chars),
            "outcome": outcome,
        }
        await asyncio.to_thread(self._write_row, row)

    async def cloud_failed(self, *, provider: str, text: str, app: str | None,
                           error: BaseException, source: str | None = None) -> None:
        """Audit a failed send and raise an alert, at most one per interval.
        The alert is handed off (`notify` must not block the speech path)."""
        await self.record(provider=provider, chars=len(text or ""), app=app,
                          outcome="failed", source=source)
        now = self._clock()
        if now - self._last_alert < ALERT_INTERVAL_S:
            return
        self._last_alert = now
        from emptyos.speechlang import detect_speech_language

        msg = (f"The online voice failed ({type(error).__name__}); speech falls back "
               f"to the on-device voice where one is installed.")
        if detect_speech_language(text or "") == "zh":
            msg += (" Chinese lines degrade there: the on-device voice's Mandarin is "
                    "about 43% intelligible.")
        if self._warn:
            self._warn(msg)
        if self._notify:
            try:
                self._notify(msg)
            except Exception:
                pass


def guard_of(capability) -> SpeechGuard | None:
    """The speech guard attached to a capability, or None (a test double's
    auto-attribute never counts)."""
    guard = getattr(capability, "speech_guard", None)
    return guard if isinstance(guard, SpeechGuard) else None


async def call_direct(guard, provider, call, *, text: str, source: str | None = None,
                      app: str | None = None):
    """Run `call()` (a send to `provider`) under the guard: the one path every
    cloud speak send takes, the chain's included.

    A local provider, or no guard, runs untouched. For a cloud provider the
    refusal is asked immediately before the send (so a switch flipped while a
    consent prompt was open still counts); a refusal is audited and raises
    ``SpeechKeptLocal``; otherwise a "send" row is written before the text
    leaves, and a failure is audited and alerted before it propagates.
    """
    if guard is None or not getattr(provider, "is_cloud", False):
        return await call()
    # A paid voice past the monthly spend cap is refused here, before the
    # "send" row: nothing leaves the machine, so the audit must not say it did,
    # and a cap is not an outage to alert about. (The chain and pinned calls
    # skip it earlier; this covers a caller that picks the voice itself.)
    from emptyos.capabilities import spend_cap

    spend_cap.check_paid_call(provider, "speak")
    text = str(text or "")
    audit = {"provider": getattr(provider, "name", "?"), "chars": len(text),
             "app": app, "source": source}
    why = guard.refusal(text, source)
    if why:
        await guard.record(outcome=f"kept-local:{why}", **audit)
        raise SpeechKeptLocal(why)
    await guard.record(outcome="send", **audit)
    try:
        return await call()
    except Exception as e:
        await guard.cloud_failed(provider=audit["provider"], text=text, app=app,
                                 error=e, source=source)
        raise
