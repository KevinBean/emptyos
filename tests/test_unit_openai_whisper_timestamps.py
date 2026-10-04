"""OpenAI Whisper STT provider — opt-in word/segment timestamps.

music-studio needs word timings for lyric-synced subtitles. It used to get them
by calling the OpenAI SDK directly, which bypassed the cloud consent gate. The
provider now returns them itself (``timestamps=True``), so the call can go
through the ``listen`` capability. These tests run the provider against a local
HTTP server standing in for the API and check the request it sends as well as
the shape it returns.
"""

import asyncio
import json

from aiohttp import web

from emptyos.capabilities.providers.openai_tts import OpenAIWhisperSTTProvider

VERBOSE = {
    "text": "hello there",
    "language": "english",
    "duration": 2.3456,
    "segments": [{"id": 0, "start": 0.0, "end": 2.3456, "text": " hello there "}],
    "words": [{"start": 0.1, "end": 0.5, "word": "hello"},
              {"start": 0.6, "end": 1.04321, "word": "there"}],
}


def _run_against_fake_api(tmp_audio, **kwargs):
    seen = {}

    async def handler(request):
        form = await request.post()
        seen["fields"] = {k: form.getall(k) for k in form if k != "file"}
        body = VERBOSE if form.get("response_format") == "verbose_json" else {"text": "hello there"}
        return web.Response(text=json.dumps(body), content_type="application/json")

    async def go():
        app = web.Application()
        app.router.add_post("/v1/audio/transcriptions", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            prov = OpenAIWhisperSTTProvider(host=f"http://127.0.0.1:{port}")
            return await prov.execute(audio=str(tmp_audio), **kwargs)
        finally:
            await runner.cleanup()

    return asyncio.run(go()), seen


def _audio(tmp_path):
    p = tmp_path / "clip.wav"
    p.write_bytes(b"RIFF0000WAVE")
    return p


def test_plain_call_is_unchanged(tmp_path, monkeypatch):
    # The stand-in API is on 127.0.0.1, i.e. a self-hosted compatible server:
    # not billed, so it keeps the plain request. (A paid host also asks for
    # verbose_json, to learn the duration it is billed on —
    # test_unit_spend_cap_paid_providers.)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")  # secrets-scan: fake
    out, seen = _run_against_fake_api(_audio(tmp_path))
    assert out == "hello there"
    assert "response_format" not in seen["fields"]
    assert "timestamp_granularities[]" not in seen["fields"]


def test_missing_duration_is_none_not_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")  # secrets-scan: fake
    monkeypatch.delitem(VERBOSE, "duration")
    out, _ = _run_against_fake_api(_audio(tmp_path), timestamps=True)
    assert out["duration"] is None


def test_consent_summary_names_the_audio_file(tmp_path):
    p = _audio(tmp_path)
    s = OpenAIWhisperSTTProvider().consent_summary(audio=str(p))
    assert s == "audio file clip.wav (12 bytes)"
    assert OpenAIWhisperSTTProvider().consent_summary(audio=b"abc") == "audio data (3 bytes)"


def test_timestamps_request_verbose_json_with_word_and_segment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")  # secrets-scan: fake
    out, seen = _run_against_fake_api(_audio(tmp_path), timestamps=True)
    assert seen["fields"]["response_format"] == ["verbose_json"]
    assert sorted(seen["fields"]["timestamp_granularities[]"]) == ["segment", "word"]
    assert out == {
        "text": "hello there",
        "language": "english",
        "duration": 2.346,
        "segments": [{"start": 0.0, "end": 2.346, "text": "hello there"}],
        "words": [{"start": 0.1, "end": 0.5, "word": "hello"},
                  {"start": 0.6, "end": 1.043, "word": "there"}],
    }
