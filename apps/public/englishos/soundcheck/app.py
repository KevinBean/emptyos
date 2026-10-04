"""Sound Check — train the ear and the spelling instinct on English sounds.

The spine. Everything substantial lives in helper modules and is re-bound onto
this class; what stays here is boot, the bank handle, the per-answer state
update, and the bindings.

The app trains **perception**, which is the half of pronunciation the rest of
the English suite does not touch: shadowing, voice-review and audio-course all
score what you *say*, and the dictionary crosswalk shows what English *spells*.
None of them ask whether you can hear the difference in the first place — and
you cannot reliably produce a contrast you cannot perceive.
"""

from __future__ import annotations

from pathlib import Path

from emptyos.sdk import BaseApp
from emptyos.sdk.srs import fsrs_schedule, score_to_rating

from . import audio as _audio
from . import bank_loader as _bank_loader
from . import panels as _panels
from . import sessions as _sessions
from . import shared


class SoundcheckApp(BaseApp):
    """Perception drills over a committed, human-reviewed item bank."""

    async def setup(self):
        await super().setup()
        self._bank = self._load_bank()
        if not self._bank.get("count"):
            await self.log(
                "soundcheck: bank is empty — every generated item is still "
                "awaiting review. Run scripts/build_soundcheck_bank.py.",
                level="warning",
            )

    # ── the bank ──────────────────────────────────────────────────

    def _load_bank(self) -> dict:
        return _bank_loader.load_bank(
            Path(__file__).parent / "bank",
            include_merger_sensitive=bool(
                self.setting_or_config("soundcheck.merger_contrasts", False)
            ),
        )

    def _reload_bank(self) -> dict:
        self._bank = self._load_bank()
        return self._bank

    # ── per-answer state ──────────────────────────────────────────

    def _record_outcome(self, session: dict, verdict: dict, item: dict | None) -> None:
        """Persist what one answer changed: heat, schedule, perception counts.

        Deliberately does **not** touch ``dictionary``'s weak-phone schedule.
        Those entries drive production drills, and letting ear-only success push
        their ``next_review`` out would quietly stop asking the learner to *say*
        a sound they can now merely recognise. Soundcheck writes evidence to the
        shared store; it keeps its own schedule.
        """
        cid = verdict.get("contrast") or ""
        if not cid:
            return

        heat = self._load_state("heat", {})
        heat[cid] = verdict.get("heat", shared.HEAT_DEFAULT)
        self._save_state("heat", heat)

        srs = self._load_state("contrast-srs", {})
        row = srs.setdefault(cid, {"contrast": cid, "trials": 0})
        row["trials"] = int(row.get("trials") or 0) + 1
        fsrs_schedule(row, score_to_rating(100 if verdict.get("correct") else 0))
        self._save_state("contrast-srs", srs)

        if not verdict.get("correct") and not verdict.get("timeout"):
            perception = self._load_state("perception", {})
            p = perception.setdefault(cid, {"misses": 0})
            p["misses"] = int(p.get("misses") or 0) + 1
            p["last_seen"] = (session.get("started") or "")[:10]
            self._save_state("perception", perception)

    # ── bindings ──────────────────────────────────────────────────

    # Sessions + the HTTP surface (sessions.py)
    _session_path = _sessions._session_path
    _load_session = _sessions._load_session
    _save_session = _sessions._save_session
    _list_sessions = _sessions._list_sessions
    _load_state = _sessions._load_state
    _save_state = _sessions._save_state
    _telemetry = _sessions._telemetry
    _write_back = _sessions._write_back
    _serve_round = _sessions._serve_round
    _fill_audio = _sessions._fill_audio
    api_start = _sessions.api_start
    api_answer = _sessions.api_answer
    api_session = _sessions.api_session
    api_complete = _sessions.api_complete
    api_abandon = _sessions.api_abandon
    api_history = _sessions.api_history
    api_review = _sessions.api_review
    api_second_pass = _sessions.api_second_pass

    # Audio (audio.py)
    _clip_dir = _audio._clip_dir
    _clip_name = _audio._clip_name
    _clip_path = _audio._clip_path
    _clip_url = _audio._clip_url
    _voice = _audio._voice
    _speed = _audio._speed
    _synthesise = _audio._synthesise
    _probe_audio = _audio._probe_audio
    _audio_for_next = _audio._audio_for_next
    _prewarm_candidates = _audio._prewarm_candidates
    _prewarm = _audio._prewarm
    api_audio = _audio.api_audio

    # Hub panel, verbs, bank introspection (panels.py)
    _worst_contrasts = _panels._worst_contrasts
    _bank_pending_path = _panels._bank_pending_path
    panel_drill = _panels.panel_drill
    voice_start = _panels.voice_start
    voice_weak = _panels.voice_weak
    voice_progress = _panels.voice_progress
    api_bank = _panels.api_bank
    api_dimensions = _panels.api_dimensions
    srs_due = _panels.srs_due
    srs_grade = _panels.srs_grade
