"""scripts/mv/lipsync — verdict thresholds, audio prep, MFCC, InfiniteTalk graph, config.

Daemon-free. numpy/soundfile/cv2/torch are optional and skip when absent; the
model weights are never needed here. Parity with the original harness (S3
6.797 vs 2.114, graphs identical, openness identical) was checked separately
on the user's own test footage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "mv"))
sys.path.insert(0, str(ROOT / "scripts" / "mv" / "lipsync"))

import infinitetalk_workflow as itw  # noqa: E402
import mv_config  # noqa: E402

import lipsync_check as lc  # noqa: E402  (heavy deps load lazily, so this always imports)


def _r(lse_c=6.0, offset=-2, edge=False, faces=1.0, face_h=200):
    return {"lse_c": lse_c, "offset": offset, "edge": edge, "faces_pct": faces, "face_h_px": face_h}


# ── Verdict thresholds ──────────────────────────────────────────────────────

def test_pass_exactly_at_every_threshold():
    v, reasons = lc.verdict(_r(4.5, offset=6), _r(2.5), 1.0)
    assert v == "pass" and reasons == []


@pytest.mark.parametrize("own,decoy,speech,want,why", [
    (_r(4.499), _r(0.0), 2.0, "fail", "own LSE-C"),
    (_r(6.0), _r(4.001), 2.0, "fail", "own − decoy"),
    (_r(6.0, offset=7), _r(1.0), 2.0, "fail", "offset +7"),
    (_r(6.0, offset=-7), _r(1.0), 2.0, "fail", "offset -7"),
    (_r(6.0, edge=True), _r(1.0), 2.0, "fail", "search edge"),
    (_r(6.0), _r(1.0), 0.99, "unmeasurable", "speech"),
    (_r(6.0, faces=0.49), _r(1.0), 2.0, "unmeasurable", "face found"),
    ({"error": "no face found"}, _r(1.0), 2.0, "fail", "no face"),
])
def test_each_threshold_decides_on_its_own(own, decoy, speech, want, why):
    v, reasons = lc.verdict(own, decoy, speech)
    assert v == want
    assert any(why in r for r in reasons)


def test_missing_decoy_score_cannot_pass():
    v, reasons = lc.verdict(_r(8.0), {"error": "no face found"}, 2.0)
    assert v == "fail" and any("decoy" in r for r in reasons)


def test_main_exit_codes_follow_the_verdict(monkeypatch, tmp_path):
    for verdict_, code in (("pass", 0), ("fail", 1), ("unmeasurable", 3)):
        monkeypatch.setattr(lc, "check", lambda *a, _v=verdict_, **k: {
            "verdict": _v, "reasons": [], "warnings": [], "speech_s": 1.5, "own": {}, "decoy": {}})
        assert lc.main(["v.mp4", "o.wav", "d.wav", "--frames", "85"]) == code

    for exc in (ValueError("cannot read v.mp4"), RuntimeError("libsndfile: format not recognised"),
                ImportError("no torch")):
        def boom(*a, _e=exc, **k):
            raise _e

        monkeypatch.setattr(lc, "check", boom)
        assert lc.main(["v.mp4", "o.wav", "d.wav"]) == 2   # could not run — never "fail"


# ── Audio preparation ───────────────────────────────────────────────────────

def _tone(path, seconds, sr=16000, on=(0.0, None)):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    t = np.arange(int(seconds * sr)) / sr
    a = 0.3 * np.sin(2 * np.pi * 220 * t)
    lo, hi = on
    a[(t < lo) | ((t >= hi) if hi is not None else False)] = 0
    # a real room is never digitally silent: a floor ~40 dB down must not count as speech
    a += np.random.default_rng(1).normal(0, 0.003, a.size)
    sf.write(str(path), a, sr)
    return path


def test_pad_audio_is_exactly_the_frame_count(tmp_path):
    sf = pytest.importorskip("soundfile")
    src = _tone(tmp_path / "a.wav", 1.0)
    for frames in (10, 85):      # trim and pad
        out = lc.pad_audio(src, frames, tmp_path / f"p{frames}.wav")
        info = sf.info(str(out))
        assert info.frames == round(frames / 25 * 16000)


def test_speech_seconds_counts_only_the_loud_part(tmp_path):
    src = _tone(tmp_path / "a.wav", 3.0, on=(1.0, 1.8))
    assert lc.speech_seconds(src) == pytest.approx(0.8, abs=0.05)


def test_mfcc_shape_and_energy_coefficient():
    np = pytest.importorskip("numpy")
    import syncnet

    sig = np.random.default_rng(0).normal(0, 1000, 16000)
    feat = syncnet.mfcc(sig)
    assert feat.shape == (99, 13)
    assert np.isfinite(feat).all()
    loud = syncnet.mfcc(sig * 10)
    assert loud[:, 0].mean() == pytest.approx(feat[:, 0].mean() + np.log(100), abs=0.01)


# ── InfiniteTalk graph ──────────────────────────────────────────────────────

BASE = dict(image="a.png", audio="a.wav", frames=85, seed=1, steps=6, start_step=0)


@pytest.mark.parametrize("mode,extra,msg", [
    ("multi", {"audio_2": "b.wav"}, "needs masks"),
    ("multi", {"audio_2": "b.wav", "masks": ["l", "r", "b"]}, "needs pos"),
    ("multi", {"masks": ["l", "r", "b"]}, "needs audio_2"),
    ("v2v", {}, "needs video"),
    ("dub", {}, "unknown mode"),
])
def test_build_refuses_an_incomplete_run(mode, extra, msg):
    with pytest.raises(ValueError, match=msg):
        itw.build(mode, **BASE, **extra)


def test_multi_wires_masks_in_speaker_order():
    g = itw.build("multi", **BASE, audio_2="b.wav", masks=["left.png", "right.png", "bg.png"], pos="two people")
    assert [g[str(i)]["inputs"]["image"] for i in (20, 21, 22)] == ["left.png", "right.png", "bg.png"]
    assert g["10"]["inputs"]["ref_target_masks"] == ["25", 0]
    assert g["2"]["inputs"]["model"] == itw.MODEL_DEFAULTS["talk_multi"]


def test_start_step_and_steps_reach_the_sampler():
    g = itw.build("i2v", **{**BASE, "steps": 6, "start_step": 0})
    assert (g["14"]["inputs"]["steps"], g["14"]["inputs"]["start_step"]) == (6, 0)
    assert g["4"]["inputs"]["merge_loras"] is False


def test_model_names_come_from_config():
    cfg = {"mv_tools": {"infinitetalk": {"base": "other-base.gguf"}}}
    models = itw.models_from_config(cfg)
    assert models["base"] == "other-base.gguf" and models["vae"] == itw.MODEL_DEFAULTS["vae"]
    g = itw.build("i2v", **BASE, models=models)
    assert g["5"]["inputs"]["model"] == "other-base.gguf"


# ── mv_config additions ─────────────────────────────────────────────────────

def test_comfy_host_precedence_and_refusal(capsys):
    cfg = {"mv_tools": {"comfy_host": "http://a:1/"}, "plugins": {"comfyui": {"host": "http://b:2"}}}
    assert mv_config.comfy_host("http://c:3/", cfg=cfg) == "http://c:3"
    assert mv_config.comfy_host(cfg=cfg) == "http://a:1"
    assert mv_config.comfy_host(cfg={"plugins": {"comfyui": {"host": "http://b:2"}}}) == "http://b:2"
    with pytest.raises(SystemExit) as exc:
        mv_config.comfy_host(cfg={})
    assert exc.value.code == 2


def test_setting_walks_dotted_keys():
    cfg = {"mv_tools": {"a": {"b": 3}}}
    assert mv_config.setting("a.b", cfg=cfg) == 3
    assert mv_config.setting("a.c", "d", cfg=cfg) == "d"
    assert mv_config.setting("x.y", 0, cfg={}) == 0


# ── Openness proxy helpers ──────────────────────────────────────────────────

def test_closed_window_parsing_and_stats():
    np = pytest.importorskip("numpy")
    import openness

    assert openness.parse_windows("0-1.5,3-4") == [(0.0, 1.5), (3.0, 4.0)]
    with pytest.raises(ValueError):
        openness.parse_windows("2-1")
    op = np.array([0.1] * 25 + [0.5] * 25 + [np.nan] * 5)
    w = openness.window_openness(op, 25.0, [(1.0, 2.2)])
    assert w["inside"]["frames"] == 25 and w["inside"]["mean"] == pytest.approx(0.5)
    assert w["outside"]["frames"] == 25 and w["outside"]["mean"] == pytest.approx(0.1)


# ── Review-round fixes ──────────────────────────────────────────────────────

def test_no_face_is_a_fail_and_few_faces_unmeasurable():
    assert lc.verdict({"error": "no face found"}, _r(1.0), 2.0)[0] == "fail"
    assert lc.verdict(_r(6.0, faces=0.3), _r(1.0), 2.0)[0] == "unmeasurable"


def test_a_near_silent_decoy_cannot_make_the_margin():
    v, reasons = lc.verdict(_r(6.0), _r(0.5), 2.0, decoy_speech_s=0.4)
    assert v == "unmeasurable" and "decoy" in reasons[0]
    assert lc.verdict(_r(6.0), _r(0.5), 2.0, decoy_speech_s=1.0)[0] == "pass"


def test_a_decaying_window_fails_a_long_line():
    windows = [{"lse_c": 8.0}, {"lse_c": 3.49}]
    v, reasons = lc.verdict(_r(6.0), _r(1.0), 5.0, 5.0, windows)
    assert v == "fail" and "window 1" in reasons[0]
    assert lc.verdict(_r(6.0), _r(1.0), 5.0, 5.0, [{"lse_c": 3.5}])[0] == "pass"


def test_speech_is_the_span_including_pauses(tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    sr = 16000
    t = np.arange(3 * sr) / sr
    a = 0.3 * np.sin(2 * np.pi * 220 * t) * (((t >= 0.5) & (t < 0.9)) | ((t >= 1.3) & (t < 1.7)))
    a += np.random.default_rng(1).normal(0, 0.003, a.size)
    sf.write(str(tmp_path / "a.wav"), a, sr)
    assert lc.speech_seconds(tmp_path / "a.wav") == pytest.approx(1.2, abs=0.05)   # 0.5-1.7, gap included


def test_check_scores_windows_only_for_long_lines_and_warns(monkeypatch, tmp_path):
    calls = {"windows": 0}
    frames = [object()] * 200
    monkeypatch.setattr(lc.syncnet, "read_frames", lambda v: frames)
    monkeypatch.setattr(lc.syncnet, "load_model", lambda: "net")
    monkeypatch.setattr(lc.syncnet, "face_crops", lambda f, r: (["c"] * len(f), 1.0, 100.0))
    monkeypatch.setattr(lc.syncnet, "measure", lambda *a, **k: _r(6.0, face_h=100) if "own" in str(a[1]) else _r(1.0))
    monkeypatch.setattr(lc, "pad_audio", lambda src, n, out: out)
    monkeypatch.setattr(lc, "speech_seconds", lambda p: 3.0)

    def fake_windows(net, crops, wav, n, tmp):
        calls["windows"] += 1
        return [{"start_frame": 0, "lse_c": 7.0}]

    monkeypatch.setattr(lc, "score_windows", fake_windows)
    r = lc.check(tmp_path / "v.mp4", tmp_path / "o.wav", tmp_path / "d.wav")
    assert calls["windows"] == 1 and r["verdict"] == "pass"
    assert any("no --frames" in w for w in r["warnings"]) and any("100px" in w for w in r["warnings"])
    r = lc.check(tmp_path / "v.mp4", tmp_path / "o.wav", tmp_path / "d.wav", frames=150)
    assert calls["windows"] == 1 and not any("no --frames" in w for w in r["warnings"])


def test_nms_keeps_the_best_of_overlapping_boxes():
    np = pytest.importorskip("numpy")
    import face_onnx

    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], float)
    scores = np.array([0.9, 0.95, 0.8])
    assert sorted(face_onnx.nms(boxes, scores)) == [1, 2]


def test_workflow_cli_refuses_models_in_the_json_and_prints_a_graph(capsys):
    assert itw.main(['{"mode":"i2v","image":"a","audio":"a","frames":1,"seed":1,"steps":1,'
                     '"start_step":0,"models":{}}', "--print"]) == 1
    assert "mv_tools.infinitetalk" in capsys.readouterr().err
    assert itw.main(['{"mode":"i2v","image":"a","audio":"a","frames":1,"seed":1,"steps":1,'
                     '"start_step":0}', "--print"]) == 0
    assert '"MultiTalkWav2VecEmbeds"' in capsys.readouterr().out
