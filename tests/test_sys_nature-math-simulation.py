"""System tests: Nature Math Simulation — over HTTP against a live daemon.

Acceptance criteria → tests (from 30_Resources/EmptyOS/grill/new-app-nature-math-simulation-20261003.md):
  AC1 POST /api/fit {reference, recipe} saves a model + PNG; the result carries the distances, the bar and
      per-statistic pass flags; the same model renders byte-identical PNG     → test_fit_end_to_end,
                                                                                 test_preview_reproduces_the_stored_render
  AC2 GET /api/models rows carry reference, render, distances, pass count; the page shows them
                                                                              → test_fit_end_to_end, test_page_loads
  AC3 POST /api/animate: transitions = clamp(k·distance, min, max), pollable progress, frame count =
      ceil(duration·fps)                                                      → test_plan_law, test_animate_short
  AC4 exports land in the renders folder with a sidecar JSON                  → test_export_still, test_animate_short
Edge/regression (no AC): test_recipes_catalogue, test_unknown_recipe_refused, test_reads_and_writes_stay_contained,
  test_model_id_guard_answers_in_band, test_upload_rejects_non_image, test_non_ascii_reference_is_addressable,
  test_bad_numbers_answer_in_band, test_plan_unknown_model, test_set_params_saves_and_rescores, test_delete_model
"""
from __future__ import annotations

import base64
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from helpers import TEST_PREFIX, assert_ok, requires_app, requires_dep

pytestmark = [requires_app("nature-math-simulation"), requires_dep("numpy", "cv2")]

P = "/nature-math-simulation"


def _png(w=344, h=192) -> bytes:
    """A dark frame with one soft horizontal line — the simplest still the recipes model."""
    import cv2
    import numpy as np
    y = np.arange(h, dtype=np.float32)[:, None]
    img = 6 + 150 * np.exp(-0.5 * ((y - h * 0.45) / 1.5) ** 2) * np.ones((1, w), np.float32)
    ok, buf = cv2.imencode(".png", np.clip(img, 0, 255).astype(np.uint8))
    return buf.tobytes()


def _wait(http_client, job_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = assert_ok(http_client.get(f"{P}/api/jobs/{job_id}"))
        if j["status"] in ("done", "error"):
            return j
        time.sleep(1.0)
    pytest.fail(f"job {job_id} did not finish in {timeout}s")


def _fit(http_client, reference, recipe):
    job = assert_ok(http_client.post(f"{P}/api/fit", json={"reference": reference, "recipe": recipe, "budget": 10}))
    assert "job" in job, job
    done = _wait(http_client, job["job"])
    assert done["status"] == "done", done
    assert done["progress"] == 1.0
    return done["result"]


@pytest.fixture(scope="module")
def fitted(http_client):
    up = assert_ok(http_client.post(f"{P}/api/references", json={
        "name": f"{TEST_PREFIX}line.png", "data": base64.b64encode(_png()).decode()}))
    assert "error" not in up, up
    return {"line": _fit(http_client, up["path"], "line"), "ripple": _fit(http_client, up["path"], "ripple"),
            "upload": up}


@pytest.mark.api
class TestNatureMathSimulationAPI:
    def test_page_loads(self, http_client):
        r = http_client.get(f"{P}/")
        assert r.status_code == 200 and "Nature Math Simulation" in r.text

    def test_recipes_catalogue(self, http_client):
        d = assert_ok(http_client.get(f"{P}/api/recipes"))
        names = {r["name"] for r in d["recipes"]}
        assert {"line", "ripple", "dunes", "rain"} <= names
        ripple = next(r for r in d["recipes"] if r["name"] == "ripple")
        assert ripple["params"]["wavelength"]["min"] < ripple["params"]["wavelength"]["max"]

    def test_unknown_recipe_refused(self, http_client):
        d = assert_ok(http_client.post(f"{P}/api/fit", json={"reference": "x.png", "recipe": "nope"}))
        assert "unknown recipe" in d.get("error", "")

    def test_reads_and_writes_stay_contained(self, http_client, fitted):
        # reads outside the vault, by relative paths that climb out of it
        for path in ("../emptyos.toml", "../../emptyos/emptyos.toml", "../x.png"):
            assert http_client.get(f"{P}/api/image", params={"path": path}).status_code == 404, path
        mid = fitted["line"]["id"]
        # (a leading "/" is stripped, so "/etc" means renders/etc — inside, by design; these climb out)
        for out in ("../models", "../../..", r"..\models", "C:/Windows"):
            d = assert_ok(http_client.post(f"{P}/api/models/{mid}/export", json={"width": 64, "height": 36, "out": out}))
            assert "renders folder" in d.get("error", ""), (out, d)

    def test_model_id_guard_answers_in_band(self, http_client):
        d = assert_ok(http_client.get(f"{P}/api/models/nul"))
        assert "invalid model id" in d.get("error", ""), d       # the guard's message, not a lookup miss

    def test_upload_rejects_non_image(self, http_client):
        d = assert_ok(http_client.post(f"{P}/api/references", json={
            "name": f"{TEST_PREFIX}bad.png", "data": base64.b64encode(b"not an image").decode()}))
        assert "error" in d

    def test_non_ascii_reference_is_addressable(self, http_client):
        d = assert_ok(http_client.post(f"{P}/api/references", json={
            "name": f"{TEST_PREFIX}看见.png", "data": base64.b64encode(_png(64, 36)).decode()}))
        assert "error" not in d, d
        assert d["name"].isascii() and d["name"].startswith(TEST_PREFIX.lower()), d
        gone = assert_ok(http_client.delete(f"{P}/api/references/{d['name']}"))
        assert gone.get("ok"), gone

    def test_bad_numbers_answer_in_band(self, http_client, fitted):
        mid = fitted["line"]["id"]
        cases = [(f"{P}/api/fit", {"reference": fitted["upload"]["path"], "recipe": "line", "budget": "abc"}),
                 (f"{P}/api/models/{mid}/params", {"params": {"line_y": "x"}}),
                 (f"{P}/api/models/{mid}/params", {"params": [1]}),
                 (f"{P}/api/plan", {"states": "abc"}),
                 (f"{P}/api/plan", {"states": [1]}),
                 (f"{P}/api/animate", {"states": [{"model": mid, "at": 0}], "duration": 1e9})]
        for url, body in cases:
            r = http_client.post(url, json=body)
            assert r.status_code == 200 and "error" in r.json(), (url, body, r.status_code, r.text[:200])

    def test_plan_unknown_model(self, http_client):
        d = assert_ok(http_client.post(f"{P}/api/plan", json={"states": [{"model": "does-not-exist", "at": 0}]}))
        assert "error" in d

    def test_fit_end_to_end(self, http_client, fitted):
        row0 = fitted["line"]
        assert row0["recipe"] == "line"
        assert set(row0["pass"]) == {"hist", "spectrum", "orient_fold", "cover"}
        assert row0["of"] == 4 and 0 <= row0["passed"] <= 4
        assert all(k in row0["bar"] for k in row0["pass"])
        rows = assert_ok(http_client.get(f"{P}/api/models"))["models"]
        row = next(r for r in rows if r["id"] == row0["id"])
        assert row["reference"] == fitted["upload"]["path"]
        img = http_client.get(f"{P}/api/image", params={"path": row["render"]})
        assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_preview_reproduces_the_stored_render(self, http_client, fitted):
        m = assert_ok(http_client.get(f"{P}/api/models/{fitted['line']['id']}"))
        w, h = m["size"]
        url = f"{P}/api/models/{m['id']}/preview"
        a = http_client.post(url, json={"width": w, "height": h})
        b = http_client.post(url, json={"width": w, "height": h})
        stored = http_client.get(f"{P}/api/image", params={"path": m["render"]})
        assert a.status_code == 200 and a.content == b.content == stored.content

    def test_set_params_saves_and_rescores(self, http_client, fitted):
        mid = fitted["ripple"]["id"]
        before = assert_ok(http_client.get(f"{P}/api/models/{mid}"))
        target = 0.31 if abs(before["params"]["cy"] - 0.31) > 0.01 else 0.69
        d = assert_ok(http_client.post(f"{P}/api/models/{mid}/params", json={"params": {"cy": target}}))
        assert d["id"] == mid and d["of"] == 4
        after = assert_ok(http_client.get(f"{P}/api/models/{mid}"))
        assert after["params"]["cy"] == pytest.approx(target)
        assert after["verdict"]["distance"] != before["verdict"]["distance"]

    def test_export_still(self, http_client, fitted):
        import cv2
        import numpy as np
        mid = fitted["line"]["id"]
        d = assert_ok(http_client.post(f"{P}/api/models/{mid}/export", json={"width": 320, "height": 180}))
        assert d["png"].endswith(".png") and d["sidecar"].endswith(".json"), d
        png = http_client.get(f"{P}/api/image", params={"path": d["png"]}).content
        assert cv2.imdecode(np.frombuffer(png, np.uint8), 1).shape == (180, 320, 3)
        names = {r["name"] for r in assert_ok(http_client.get(f"{P}/api/renders"))["renders"]}
        assert Path(d["sidecar"]).name in names
        again = assert_ok(http_client.post(f"{P}/api/models/{mid}/export", json={"width": 320, "height": 180}))
        assert again["png"] != d["png"], "a second export overwrote the first"

    def test_plan_law(self, http_client, fitted):
        a, b = fitted["line"]["id"], fitted["ripple"]["id"]
        states = [{"model": a, "at": 0}, {"model": b, "at": 1}]
        d = assert_ok(http_client.post(f"{P}/api/plan", json={"states": states, "duration": 60,
                                                              "k": 1, "t_min": 0.01, "t_max": 500}))
        row = d["transitions"][0]
        assert row["seconds"] == pytest.approx(row["distance"], rel=1e-3)        # T = k·distance in the middle
        lo = assert_ok(http_client.post(f"{P}/api/plan", json={"states": states, "duration": 60,
                                                               "k": 1e-6, "t_min": 3.25, "t_max": 9}))
        assert lo["transitions"][0]["seconds"] == 3.25
        hi = assert_ok(http_client.post(f"{P}/api/plan", json={"states": states, "duration": 60,
                                                               "k": 1e6, "t_min": 3.25, "t_max": 7.5}))
        assert hi["transitions"][0]["seconds"] == 7.5

    def test_animate_short(self, http_client, fitted):
        job = assert_ok(http_client.post(f"{P}/api/animate", json={
            "states": [{"model": fitted["line"]["id"], "at": 0}], "duration": 2, "fps": 6, "width": 160,
            "height": 90, "name": TEST_PREFIX + "anim"}))
        assert "job" in job, job
        done = _wait(http_client, job["job"])
        assert done["status"] == "done", done
        assert done["progress"] == 1.0
        assert done["result"]["frames"] == math.ceil(2 * 6)
        v = http_client.get(f"{P}/api/video", params={"path": done["result"]["mp4"]})
        assert v.status_code == 200 and v.content[4:8] == b"ftyp"
        if shutil.which("ffprobe"):                    # the frames actually encoded, not the server's count
            with tempfile.TemporaryDirectory() as td:
                f = Path(td) / "a.mp4"
                f.write_bytes(v.content)
                out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                                      "-show_entries", "stream=width,height,nb_read_frames", "-of", "csv=p=0",
                                      str(f)], capture_output=True, text=True, timeout=60).stdout.strip()
            assert out.split(",") == ["160", "90", "12"], out
        side = assert_ok(http_client.get(f"{P}/api/renders"))["renders"]
        assert any(r["name"] == Path(done["result"]["sidecar"]).name for r in side)

    def test_delete_model(self, http_client, fitted):
        victim = _fit(http_client, fitted["upload"]["path"], "dew")
        render = victim["render"]
        assert http_client.get(f"{P}/api/image", params={"path": render}).status_code == 200
        d = assert_ok(http_client.delete(f"{P}/api/models/{victim['id']}"))
        assert d.get("ok"), d
        assert "error" in assert_ok(http_client.get(f"{P}/api/models/{victim['id']}"))
        assert http_client.get(f"{P}/api/image", params={"path": render}).status_code == 404


@pytest.mark.interactive
class TestNatureMathSimulationUI:
    def test_page_renders_without_js_errors(self, page, base_url):
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base_url + f"{P}/")
        page.wait_for_selector("#fit-recipe option", state="attached")
        assert not errors, errors
