"""The depth guard's verdict, and the JSON contract its one consumer parses.

The guard returns a ``MediaVerdict`` — the same hard/soft type the audio and
video gates return. A depth sequence is a control signal, not a rendered
artifact, so no ffprobe machinery is shared; what is shared is the *claim*
("deterministic pre-flight, hard blocks, soft annotates"), which is what lets
`apps/personal/music-studio/blockout.py` treat all three gates alike.

The verdict crosses a subprocess boundary as JSON, so the type alone proves
nothing about what the caller receives. These tests run the real CLI and parse
its real stdout with the caller's own logic.

Sequences are constructed rather than fixtured because both cases have to
exist: a gate is a claim about the world, and a claim needs the case it must
catch *and* the case it must let through. The bad case is `room_dolly` from the
2026-07-27 probe run — the view flattened onto one surface — and the good case
is a plain depth ramp sliding gently, i.e. a dolly.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / "scripts" / "check_depth_sequence.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("eos_depth_guard", GUARD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eos_depth_guard"] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


def _write(d: Path, fn, n: int = 8) -> str:
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.fromarray(fn(i).astype(np.uint8)).save(d / f"f{i:03d}.png")
    return str(d)


@pytest.fixture
def good(tmp_path):
    """A depth ramp sliding gently — a dolly with real near/far separation."""
    yy, _xx = np.mgrid[0:120, 0:160]
    return _write(tmp_path / "good", lambda i: np.clip(20 + yy * 1.6 + i * 2, 0, 255))


@pytest.fixture
def flat(tmp_path):
    """The room_dolly failure — every pixel at one depth."""
    return _write(tmp_path / "flat", lambda i: np.full((120, 160), 128 + i))


@pytest.fixture
def jump(tmp_path):
    """A camera jump: one step far larger than the median."""
    yy, _xx = np.mgrid[0:120, 0:160]
    return _write(
        tmp_path / "jump",
        lambda i: np.clip(20 + yy * 1.6 + (i * 2 if i < 5 else i * 2 + 90), 0, 255),
    )


# --- the verdict ------------------------------------------------------------

def test_a_healthy_sequence_is_accepted(good):
    v = guard.analyse(good)
    assert v.ok and not v.hard
    assert v.metrics["frames"] == 8
    assert v.metrics["range_min_spread"] > guard.RANGE_MIN_SPREAD


def test_a_flattened_view_is_a_hard_finding(flat):
    v = guard.analyse(flat)
    assert not v.ok
    assert any(h.startswith("FLAT") for h in v.hard)
    assert any(h.startswith("RANGE") for h in v.hard)


def test_a_camera_jump_is_a_hard_finding(jump):
    v = guard.analyse(jump)
    assert not v.ok, f"smooth_ratio={v.metrics.get('smooth_ratio')}"
    assert any(h.startswith("SMOOTH") for h in v.hard)


def test_drift_is_advisory_and_never_blocks(tmp_path):
    """DRIFT was demoted after rejecting a legitimate reveal three times.

    A crane-up over a valley moves the mean a long way while every frame stays
    perfectly readable. If this ever returns ok=False the gate has re-acquired
    the false positive that cost three GPU renders.
    """
    yy, _xx = np.mgrid[0:120, 0:160]
    d = _write(tmp_path / "reveal", lambda i: np.clip(10 + yy * 1.5 + i * 12, 0, 255))
    v = guard.analyse(d)
    assert v.metrics["drift"] > guard.DRIFT_ADVISORY, "the reveal did not drift"
    assert v.ok, f"a legitimate reveal was blocked: {v.hard}"
    assert any(s.startswith("DRIFT") for s in v.soft)


def test_a_missing_sequence_is_hard_not_a_separate_error_channel(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    v = guard.analyse(str(empty))
    assert not v.ok and v.hard and not v.metrics


# --- the JSON contract the consumer actually parses -------------------------

def _cli(directory: str):
    r = subprocess.run(
        [sys.executable, str(GUARD), directory, "--json"],
        capture_output=True, text=True, cwd=REPO, timeout=120,
    )
    return r.returncode, json.loads(r.stdout or "{}")


def test_cli_emits_the_media_verdict_shape(good):
    rc, r = _cli(good)
    assert rc == 0
    assert set(r) == {"ok", "hard", "soft", "metrics"}


def test_exit_code_counts_hard_findings_only(flat):
    """Usable as a shell test, and soft findings must not inflate it."""
    rc, r = _cli(flat)
    assert rc == len(r["hard"]) > 0


def test_the_consumer_reads_reject_from_hard(flat):
    """`blockout.py::_render_and_gate`'s own logic, run against real output.

    Its retry loop feeds this string back to the scene author, so reading the
    wrong key would either drop the complaint (the author retries blind) or
    hand it an advisory to chase.
    """
    _rc, r = _cli(flat)
    assert not r.get("ok")
    detail = "; ".join(r.get("hard") or [])
    assert detail and "FLAT" in detail


def test_the_consumer_reads_metrics_from_the_metrics_table(good):
    _rc, r = _cli(good)
    m = r.get("metrics") or {}
    assert {"frames", "flat_min_levels", "range_min_spread", "smooth_ratio"} <= set(m)
    assert all(m[k] is not None for k in ("frames", "flat_min_levels"))


def test_advisories_do_not_reach_the_rejection_path(tmp_path):
    """A soft-only verdict passes — the caller must not see it as a rejection."""
    yy, _xx = np.mgrid[0:120, 0:160]
    d = _write(tmp_path / "reveal", lambda i: np.clip(10 + yy * 1.5 + i * 12, 0, 255))
    rc, r = _cli(d)
    assert rc == 0 and r["ok"] is True
    assert r["soft"] and not r["hard"]
