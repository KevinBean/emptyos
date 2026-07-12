"""Dictation — voice-to-text into the focused field, anywhere in EmptyOS.

The real work lives in the global overlay (`emptyos/web/static/eos-dictate.js`):
push-to-talk hotkey, Web Speech capture, and the focused-field insertion
primitive. This backend is a thin support layer — a dark-flag gate and the
LLM transcript polish (shared with hands-free via `emptyos.sdk.text_cleanup`).

Dark-flagged off by default: with the flag off, the overlay binds no hotkey and
`/api/cleanup` refuses, so every page is byte-identical to the pre-feature build.

Two STT providers, chosen by the `dictation.stt_provider` setting:
  - "web-speech" (default) — in-browser Web Speech API, live streaming, zero
    STT code on our side; great for English, weaker for Chinese.
  - "server" — the overlay records an audio blob (MediaRecorder) and POSTs it to
    `/api/transcribe`, which runs the `listen` capability (voice-api Whisper,
    fully local, better for Chinese). No live interim; text lands on stop.
Both share the same insertion + polish path.
"""

from __future__ import annotations

from emptyos.sdk import BaseApp, web_route
from emptyos.sdk.text_cleanup import clean_dictation

# setting_key -> (emptyos.toml [apps.dictation] key, default)
SETTINGS: dict[str, tuple[str, object]] = {
    "dictation.feature.enabled": ("feature.enabled", False),
    "dictation.hotkey": ("hotkey", "ctrl+shift+d"),
    "dictation.cleanup_threshold_words": ("cleanup_threshold_words", 6),
    "dictation.mic_language": ("mic_language", "en-US"),
    "dictation.vocabulary": ("vocabulary", ""),
    "dictation.stt_provider": ("stt_provider", "web-speech"),
}


class DictationApp(BaseApp):
    def _get(self, setting_key: str):
        """Settings-service value (live ⚙ toggle) first, then emptyos.toml."""
        short, default = SETTINGS[setting_key]
        v = self.setting(setting_key, None)
        if v is not None:
            return v
        return self.app_config(short, default)

    def _enabled(self) -> bool:
        return bool(self._get("dictation.feature.enabled"))

    def _threshold(self) -> int:
        try:
            return int(self._get("dictation.cleanup_threshold_words"))
        except (TypeError, ValueError):
            return 6

    @web_route("GET", "/api/config")
    async def api_config(self, request):
        """Dark-flag gate + the knobs the overlay needs. Vocabulary is NOT
        returned — it's only used server-side in the polish call."""
        return {
            "enabled": self._enabled(),
            "hotkey": str(self._get("dictation.hotkey") or "ctrl+shift+d"),
            "cleanup_threshold_words": self._threshold(),
            "mic_language": str(self._get("dictation.mic_language") or "en-US"),
            "stt_provider": str(self._get("dictation.stt_provider") or "web-speech"),
        }

    @web_route("POST", "/api/cleanup")
    async def api_cleanup(self, request):
        """Polish a raw transcript. Below the word threshold, return it verbatim
        (no model call). Refuses when the feature is disabled."""
        if not self._enabled():
            return {"cleaned": "", "error": "dictation is disabled"}
        data = await self.safe_json(request)
        text = (data.get("text") or "").strip()
        if not text:
            return {"cleaned": ""}
        if len(text.split()) <= self._threshold():
            return {"cleaned": text, "skipped": "below_threshold"}
        vocab = str(self._get("dictation.vocabulary") or "")
        cleaned = await clean_dictation(self.think, text, vocabulary=vocab)
        return {"cleaned": cleaned, "original": text}

    @web_route("POST", "/api/transcribe")
    async def api_transcribe(self, request):
        """Server-side STT for stt_provider="server": multipart audio blob -> text
        via the `listen` chain (voice-api Whisper). Web Speech needs no server.
        The mic language (BCP-47, e.g. en-US / zh-CN) is reduced to the 2-letter
        code Whisper expects (en / zh)."""
        if not self._enabled():
            return {"text": "", "error": "dictation is disabled"}
        form = await request.form()
        audio = form.get("audio")
        if audio is None:
            return {"text": "", "error": "no audio file"}
        try:
            filepath, _ = await self.save_audio_upload(audio, prefix="dictate")
            mic = str(self._get("dictation.mic_language") or "en-US")
            lang = mic.split("-")[0].strip()
            kwargs = {"language": lang} if lang else {}
            text = await self.listen(str(filepath), **kwargs)
            return {"text": text or ""}
        except Exception as e:
            return {"text": "", "error": f"transcription failed: {e}"}
