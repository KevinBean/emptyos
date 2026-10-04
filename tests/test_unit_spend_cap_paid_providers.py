"""The monthly spend cap reaches every paid provider, not only ``think``.

A hosted learner daemon may spend $X a month on AI. ``think`` reports its cost
on an event; a paid voice (openai-tts), transcription (openai-whisper) or image
(openai gpt-image-1) call now meters and gates ITSELF through
``spend_cap.check_paid_call`` / ``spend_cap.charge``, because not every path to
it goes through the capability chain (a pinned provider, reader's own voice
picker). Pinned here, against a local server standing in for the API:

  - at the cap the provider refuses BEFORE sending (no request reaches the API)
    with ``SpendCapReached``, the in-band "monthly AI limit reached" error;
  - under the cap each call adds its published price to the month: per input
    character for speech, per minute of audio for transcription, per token for
    images;
  - a call that cannot price itself is still stopped at the cap, and says so once;
  - with no cap configured, both halves are no-ops.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web

from emptyos.capabilities import spend_cap
from emptyos.capabilities.providers.openai_tts import (
    STT_USD_PER_MINUTE,
    TTS_USD_PER_MILLION_CHARS,
    OpenAITTSProvider,
    OpenAIWhisperSTTProvider,
)
from emptyos.capabilities.spend_cap import SpendCap, SpendCapReached

FAKE_KEY = "sk-test-not-a-real-key"  # secrets-scan: fake


@pytest.fixture
def cap(tmp_path, monkeypatch):
    """A $0.25 cap made active for the paid providers, reset after the test."""
    warnings: list[str] = []
    c = SpendCap(tmp_path, 0.25, warn=warnings.append)
    c.warnings = warnings
    spend_cap.activate(c)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    yield c
    spend_cap.activate(None)


def _serve(route: str, body: dict | bytes, call, hits: list | None = None):
    """Run `call(host)` against a local stand-in for the API; return (result, hits).

    Pass *hits* to keep the record of requests when `call` raises."""
    hits = [] if hits is None else hits

    async def handler(request):
        hits.append(request.path)
        if isinstance(body, bytes):
            return web.Response(body=body, content_type="audio/mpeg")
        return web.Response(text=json.dumps(body), content_type="application/json")

    async def go():
        app = web.Application()
        app.router.add_post(route, handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            return await call(f"http://127.0.0.1:{port}")
        finally:
            await runner.cleanup()

    return asyncio.run(go()), hits


def _spent(cap) -> float:
    return cap.status().get("spent_usd") or 0.0


def _cloud(provider):
    """The local stand-in server makes the provider read as local; the real
    host is OpenAI's, so declare it a cloud service the way the host would."""
    provider.trust = "service"
    return provider


def test_a_self_hosted_compatible_server_is_never_charged(cap, tmp_path, monkeypatch):
    # The same provider class pointed at a local OpenAI-compatible voice costs
    # nothing: it must not fill the learner's cap at OpenAI's prices.
    monkeypatch.setattr("emptyos.capabilities.providers.openai_tts.AUDIO_DIR", tmp_path)
    _serve("/v1/audio/speech", b"ID3",
           lambda host: OpenAITTSProvider(host=host).execute(text="a" * 1000))
    assert _spent(cap) == 0.0 and cap.warnings == []


# ── speak: openai-tts ────────────────────────────────────────────────────────

def test_tts_charges_its_published_per_character_price(cap, tmp_path, monkeypatch):
    monkeypatch.setattr("emptyos.capabilities.providers.openai_tts.AUDIO_DIR", tmp_path)
    text = "a" * 1000
    _, hits = _serve("/v1/audio/speech", b"ID3",
                     lambda host: _cloud(OpenAITTSProvider(host=host)).execute(text=text))
    assert hits == ["/v1/audio/speech"]
    # 1000 chars x $15.00 / 1M chars (tts-1)
    assert _spent(cap) == pytest.approx(0.015, abs=1e-9)
    assert TTS_USD_PER_MILLION_CHARS["tts-1"] == 15.00


def test_tts_at_the_cap_refuses_before_sending(cap, tmp_path, monkeypatch):
    monkeypatch.setattr("emptyos.capabilities.providers.openai_tts.AUDIO_DIR", tmp_path)
    cap.record(0.30)
    hits: list = []
    with pytest.raises(SpendCapReached) as exc:
        _serve("/v1/audio/speech", b"ID3",
               lambda host: _cloud(OpenAITTSProvider(host=host)).execute(text="hello"), hits)
    assert hits == []                                 # no request reached the API
    assert "monthly AI limit reached" in str(exc.value)
    assert str(exc.value).startswith("No available provider for capability 'speak'")
    assert list(tmp_path.glob("tts_*.mp3")) == []   # no audio was fetched or written


def test_tts_unknown_model_is_stopped_at_the_cap_but_warns_it_cannot_count(cap, tmp_path, monkeypatch):
    monkeypatch.setattr("emptyos.capabilities.providers.openai_tts.AUDIO_DIR", tmp_path)
    for _ in range(2):
        _serve("/v1/audio/speech", b"ID3",
               lambda host: _cloud(OpenAITTSProvider(host=host, model="gpt-4o-mini-tts")).execute(text="hi"))
    assert _spent(cap) == 0.0
    assert len(cap.warnings) == 1 and "openai-tts" in cap.warnings[0]


# ── listen: openai-whisper ───────────────────────────────────────────────────

def test_whisper_charges_per_minute_of_audio(cap, tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF0000WAVE")
    out, hits = _serve("/v1/audio/transcriptions", {"text": "hi", "duration": 90.0},
                       lambda host: _cloud(OpenAIWhisperSTTProvider(host=host)).execute(audio=str(audio)))
    assert out == "hi" and hits == ["/v1/audio/transcriptions"]
    # 90 s = 1.5 min x $0.006 / min (whisper-1)
    assert _spent(cap) == pytest.approx(0.009, abs=1e-9)
    assert STT_USD_PER_MINUTE["whisper-1"] == 0.006


def test_whisper_at_the_cap_refuses_before_sending(cap, tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF0000WAVE")
    cap.record(0.30)
    hits: list = []
    with pytest.raises(SpendCapReached) as exc:
        _serve("/v1/audio/transcriptions", {"text": "hi", "duration": 1.0},
               lambda host: _cloud(OpenAIWhisperSTTProvider(host=host)).execute(audio=str(audio)), hits)
    assert str(exc.value).startswith("No available provider for capability 'listen'")
    assert hits == []


def test_whisper_without_a_duration_charges_nothing_and_says_so(cap, tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF0000WAVE")
    _serve("/v1/audio/transcriptions", {"text": "hi"},
           lambda host: _cloud(OpenAIWhisperSTTProvider(host=host)).execute(audio=str(audio)))
    assert _spent(cap) == 0.0
    assert len(cap.warnings) == 1 and "openai-whisper" in cap.warnings[0]


# ── draw: openai gpt-image-1 ─────────────────────────────────────────────────

def _image_plugin():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "plugins" / "openai-image" / "plugin.py"
    spec = importlib.util.spec_from_file_location("_openai_image_plugin", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_image_cost_follows_the_published_token_rates():
    mod = _image_plugin()
    usage = {"input_tokens": 50, "output_tokens": 1000,
             "input_tokens_details": {"text_tokens": 50, "image_tokens": 0}}
    # 50 x $5 + 1000 x $40, per 1M tokens
    assert mod.image_cost_usd(usage) == pytest.approx(0.04025, abs=1e-12)
    # No text/image split: priced at the higher image-input rate (over-count).
    assert mod.image_cost_usd({"input_tokens": 50, "output_tokens": 0}) == pytest.approx(0.0005)
    for missing in (None, {}, {"output_tokens": "x"}):
        assert mod.image_cost_usd(missing) is None


def _draw_provider(mod):
    """Run the plugin's real connect() and return the draw provider it registers."""
    from types import SimpleNamespace

    from emptyos.capabilities import Capability

    draw, think = Capability([]), Capability([])
    draw.name, think.name = "draw", "think"
    kernel = SimpleNamespace(capabilities={"draw": draw, "think": think})
    plugin = mod.OpenAIImagePlugin(kernel, {})
    asyncio.run(plugin.connect())
    return plugin, draw._domains[mod.CLOUD_DOMAIN][0]


def test_draw_charges_the_generation_and_refuses_at_the_cap(cap, monkeypatch):
    mod = _image_plugin()
    plugin, provider = _draw_provider(mod)
    sent: list[str] = []

    async def fake_generate(prompt, **kw):
        sent.append(prompt)
        return "/tmp/x.png", {"input_tokens": 0, "output_tokens": 1000}

    monkeypatch.setattr(plugin, "generate_with_usage", fake_generate)
    assert asyncio.run(provider.execute(prompt="a cat")) == "/tmp/x.png"
    assert _spent(cap) == pytest.approx(0.04, abs=1e-9)

    cap.record(0.30)
    with pytest.raises(SpendCapReached) as exc:
        asyncio.run(provider.execute(prompt="another cat"))
    assert str(exc.value).startswith("No available provider for capability 'draw'")
    assert sent == ["a cat"]                          # the capped call never reached OpenAI


# ── the module-level half ────────────────────────────────────────────────────

def test_with_no_cap_configured_both_halves_are_no_ops():
    spend_cap.activate(None)
    tts = OpenAITTSProvider()
    spend_cap.check_paid_call(tts, "speak")              # does not raise
    spend_cap.charge(tts, "speak", 1.0)                 # does not raise, records nowhere


def test_a_ledger_that_cannot_be_written_never_fails_the_paid_call(cap, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(cap, "record", boom)
    spend_cap.charge(OpenAITTSProvider(), "speak", 0.01)   # must not raise
    assert any("could not record" in w for w in cap.warnings)


def test_a_free_provider_is_never_charged_or_stopped(cap):
    class Free:
        name, is_cloud, metered = "edge-tts", True, False

    cap.record(0.30)
    spend_cap.check_paid_call(Free(), "speak")            # at the cap, still allowed
    spend_cap.charge(Free(), "speak", None)
    assert cap.warnings == []                            # no "cannot price" noise either


# ── a caller that picks the voice itself (reader) ────────────────────────────

def test_a_capped_voice_is_refused_before_the_guard_logs_a_send(cap, tmp_path):
    """reader runs its chosen voice through speech_guard.call_direct. At the cap
    the call must be refused before the "send" audit row: nothing left the
    machine, and a cap is not an outage to alert about."""
    from emptyos.capabilities.speech_guard import SpeechGuard, call_direct

    alerts: list = []
    guard = SpeechGuard(audit_path=tmp_path / "audit.jsonl", notify=alerts.append)
    tts = OpenAITTSProvider()                 # api.openai.com: cloud, metered
    sent: list = []

    async def send():
        sent.append(1)
        return "x.mp3"

    cap.record(0.30)
    with pytest.raises(SpendCapReached):
        asyncio.run(call_direct(guard, tts, send, text="hello", app="reader"))
    assert sent == []
    audit = guard.audit_path
    assert not audit.exists() or audit.read_text(encoding="utf-8").strip() == ""
    assert alerts == []


# ── the kernel turns the provider-side half on ───────────────────────────────

def _boot(tmp_path, spend: str):
    from emptyos.kernel import Kernel

    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)
    toml = tmp_path / "emptyos.toml"
    toml.write_text(
        f'[os]\ndata_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'[notes]\npath = "{(tmp_path / "vault").as_posix()}"\n' + spend,
        encoding="utf-8",
    )
    return Kernel(str(toml))


def test_the_kernel_activates_its_cap_for_the_paid_providers(tmp_path, monkeypatch):
    monkeypatch.delenv("EOS_SPEND_MONTHLY_CAP_USD", raising=False)
    try:
        kernel = _boot(tmp_path, "[spend]\nmonthly_cap_usd = 0.25\n")
        assert kernel.spend_cap is not None
        assert spend_cap._active is kernel.spend_cap
        # And with the cap off, a cap left by an earlier kernel stops applying.
        off = _boot(tmp_path / "off", "")
        assert off.spend_cap is None and spend_cap._active is None
    finally:
        spend_cap.activate(None)


# ── whisper asks for the duration only where it is billed ────────────────────

def _whisper_fields(tmp_path, provider):
    seen: dict = {}

    async def handler(request):
        form = await request.post()
        seen.update({k: form.getall(k) for k in form if k != "file"})
        return web.Response(text=json.dumps({"text": "hi", "duration": 1.0}),
                            content_type="application/json")

    async def go():
        app = web.Application()
        app.router.add_post("/v1/audio/transcriptions", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            provider.host = f"http://127.0.0.1:{port}"
            audio = tmp_path / "clip.wav"
            audio.write_bytes(b"RIFF0000WAVE")
            return await provider.execute(audio=str(audio))
        finally:
            await runner.cleanup()

    assert asyncio.run(go()) == "hi"
    return seen


def test_a_self_hosted_whisper_keeps_the_plain_request(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    local = _whisper_fields(tmp_path, OpenAIWhisperSTTProvider())
    assert "response_format" not in local
    paid = _whisper_fields(tmp_path, _cloud(OpenAIWhisperSTTProvider()))
    assert paid["response_format"] == ["verbose_json"]
