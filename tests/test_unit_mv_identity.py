"""scripts/mv/lipsync/identity_check.py — verdict bands, flags, exit codes, CLI refusals.

Daemon-free; no model files needed. Parity with the original harness (S3 0.547,
S2 0.686 against the short-drama references) was checked on the user's footage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "mv" / "lipsync"))

import identity_check as ic  # noqa: E402


def _slot(med, low=None, other=0.1, h=200):
    return {"face_h_px": h, "hero_median": med, "hero_min": med if low is None else low,
            "other_median": other, "other_min": other}


@pytest.mark.parametrize("med,want", [(0.45, "pass"), (0.449, "review"), (0.35, "review"), (0.349, "fail")])
def test_bands_are_inclusive_at_their_floor(med, want):
    assert ic.verdict(_slot(med), "hero", ["hero", "other"]).split(" / ")[0] == want


def test_drift_flag_on_a_low_frame():
    assert ic.verdict(_slot(0.6, low=0.34), "hero", ["hero", "other"]) == "pass / drift"
    assert ic.verdict(_slot(0.6, low=0.35), "hero", ["hero", "other"]) == "pass"


def test_clone_risk_when_the_face_also_matches_someone_else():
    assert ic.verdict(_slot(0.6, other=0.30), "hero", ["hero", "other"]) == "pass / clone risk"
    assert ic.verdict(_slot(0.6, other=0.299), "hero", ["hero", "other"]) == "pass"


def test_small_face_is_report_only():
    assert ic.verdict(_slot(0.1, h=89), "hero", ["hero"]).startswith("report-only")


@pytest.mark.parametrize("verdicts,code", [
    (["pass", "report-only (face < 90 px)"], 0),
    (["pass / drift", "review"], 3),
    (["review", "fail / clone risk"], 1),
    (["fail (no face)"], 1),
])
def test_exit_code_is_the_worst_verdict(verdicts, code):
    results = [{"slots": {f"s{i}": {"verdict": v}}} for i, v in enumerate(verdicts)]
    assert ic.exit_code(results) == code


def test_cli_refuses_a_malformed_ref_and_an_unknown_expected_name(monkeypatch, capsys):
    assert ic.main(["--ref", "hero", "t.png"]) == 2
    assert "name=path" in capsys.readouterr().err
    monkeypatch.setattr(ic, "ref_embedding", lambda p: [1.0])
    assert ic.main(["--ref", "hero=h.png", "--expect", "villain", "t.png"]) == 2
    assert "no --ref named villain" in capsys.readouterr().err


def test_expect_and_expect_lr_are_exclusive():
    with pytest.raises(SystemExit):
        ic.main(["--ref", "a=a.png", "--expect", "a", "--expect-lr", "a,b", "t.png"])


def test_thin_face_coverage_is_review_not_pass():
    s = {**_slot(0.7), "faces_pct": 0.3}
    assert ic.verdict(s, "hero", ["hero", "other"]).startswith("review (face in 30%")


@pytest.mark.parametrize("v", ["pass / drift", "pass / clone risk"])
def test_flags_turn_a_pass_into_exit_3(v):
    assert ic.exit_code([{"slots": {"a": {"verdict": v}}}]) == 3


def test_sheet_shows_the_spread_and_the_worst_frame():
    scores = [0.8, 0.7, 0.75, 0.2, 0.9, 0.85, 0.8, 0.8, 0.8, 0.8]
    picks = ic.sheet_picks(scores)
    assert 3 in picks and picks[0] == 0 and 9 in picks


def test_a_crash_is_exit_2_never_fail(monkeypatch, capsys):
    def boom(p):
        raise RuntimeError("onnxruntime failed")

    monkeypatch.setattr(ic, "ref_embedding", boom)
    assert ic.main(["--ref", "hero=h.png", "t.png"]) == 2
    assert ic.main(["--ref", "hero=h.png", "--every", "0", "t.png"]) == 2


def test_two_shot_pairs_faces_left_to_right(monkeypatch):
    np = pytest.importorskip("numpy")
    left = (np.array([10, 10, 60, 60.]), 0.9, "kL")
    right = (np.array([200, 10, 260, 70.]), 0.9, "kR")
    monkeypatch.setattr(ic, "frames_of", lambda t, every: iter([(0, "img")]))
    monkeypatch.setattr(ic.face_onnx, "detect", lambda img: [right, left])
    emb = {"kL": np.array([1.0, 0.0]), "kR": np.array([0.0, 1.0])}
    monkeypatch.setattr(ic.face_onnx, "embed", lambda img, kps: (emb[kps], np.zeros((112, 112, 3), np.uint8)))
    monkeypatch.setattr(ic, "jaw_zoom", lambda img, box: np.zeros((112, 224, 3), np.uint8))
    refs = {"a": np.array([1.0, 0.0]), "b": np.array([0.0, 1.0])}
    rec, sheet = ic.measure(Path("t.png"), refs, expect_lr=["a", "b"])
    assert rec["slots"]["a"]["a_median"] == 1.0 and rec["slots"]["b"]["b_median"] == 1.0
    assert set(sheet) == {"a", "b"}
