"""Unit tests: Nature Math Simulation's numeric core — no daemon.

Pins the contracts the app's claims rest on:
  - recipes are deterministic (stroke recipes included) and resolution-free: every recipe passes its own
    same-kind bar between 1376, 1920 and 2752 px
  - the same-kind bar is a real bar: an image scores 0 against itself and its mirror on every gated statistic,
    and a different motif fails EVERY gated statistic (so each one discriminates)
  - the verdict's cost penalises only distance PAST the bar, so a fit cannot trade a pass away cheaply
  - the background fit recovers a known smooth surface under a masked-out structure
  - a fit never ends worse than the measured defaults, at a reference size other than the calibration's
  - the timing law: T = clamp(k * distance, t_min, t_max) including its linear middle, smootherstep easing,
    and a state that comes too soon is delayed, never cut
  - a morph only moves continuous parameters: a small step changes the picture a little, a discrete
    parameter dissolves instead, and a background blend has no jump at its midpoint
"""
from __future__ import annotations

import pytest

# Module scope, before any numpy use: CI installs no numpy, and an unguarded
# import is a collection error that aborts the WHOLE run, not just this file.
np = pytest.importorskip("numpy")

from helpers import load_app_module, requires_app, requires_dep

pytestmark = [requires_app("nature-math-simulation"), requires_dep("numpy", "cv2")]

APP = "nature-math-simulation"
CORE = ("primitives", "recipes", "kind_stats", "compose")


@pytest.fixture(scope="module")
def m():
    fitting = load_app_module(APP, "fitting", preload=CORE)
    animate = load_app_module(APP, "animate", preload=CORE)
    import sys
    ns = {k: sys.modules[f"apps.{APP}.{k}"] for k in CORE}
    ns.update(fitting=fitting, animate=animate)
    return type("Core", (), ns)


def _model(m, recipe, seed=1, **params):
    md = m.compose.new_model(recipe, params, seed)
    md["grain"] = {"std": 2.0, "blur": 0.5}
    return md


# ── recipes ─────────────────────────────────────────────────────────────────
def test_every_recipe_renders_finite_at_two_sizes(m):
    for name in m.recipes.RECIPES:
        for w, h in ((344, 192), (688, 384)):
            I, ember = m.recipes.render_structure(name, {}, 3, w, h)
            assert I.shape == (h, w), name
            assert np.isfinite(I).all(), name
            assert I.max() > 0, f"{name} rendered nothing at {w}x{h}"


@pytest.mark.parametrize("recipe", ["ripple", "grass", "spider", "rain", "dissolve"])
def test_render_is_byte_identical_for_the_same_model(m, recipe):
    # Stroke recipes included: ripple alone never uses its seed, so an unseeded rng would pass on it.
    md = _model(m, recipe, seed=4)
    a = m.compose.to_png_bytes(m.compose.render(md, 400, 224))
    b = m.compose.to_png_bytes(m.compose.render(md, 400, 224))
    assert a == b


def test_seed_changes_a_stroke_layout(m):
    a, _ = m.recipes.render_structure("grass", {}, 1, 300, 170)
    b, _ = m.recipes.render_structure("grass", {}, 2, 300, 170)
    assert np.abs(a - b).mean() > 1e-3


def test_every_recipe_is_resolution_free(m):
    # A model fitted at 1376 px must still be the same kind of picture at 1920 and 2752 px. Raw pixel lengths
    # (glow radii, stroke widths, grain) failed this for 11 of 21 recipes (adversarial review, 2026-10-03).
    ks = m.kind_stats
    failures = []
    for name in m.recipes.RECIPES:
        md = _model(m, name, seed=3)
        ref = ks.stats(m.compose.render(md, 1376, 768))
        for w, h in ((1920, 1080), (2752, 1536)):
            v = ks.verdict(ks.distance(ref, ks.stats(m.compose.render(md, w, h))))
            if v["passed"] < 4:
                failures.append(f"{name}@{w}: {[k for k, ok in v['pass'].items() if not ok]}")
    assert not failures, failures


def test_clamp_keeps_parameters_in_range(m):
    rec = m.recipes.RECIPES["ripple"]
    P = m.recipes.clamp(rec, {"wavelength": 1e9, "squash": -5, "nonsense": 3})
    assert P["wavelength"] == rec.params["wavelength"][2]
    assert P["squash"] == rec.params["squash"][1]
    assert "nonsense" not in P


SPIKE = 5.0   # measured 2026-10-03: reshuffling parameters score 7-13, smooth ones <= 2.1 (vast.line_y 1.3)


def _spike(m, name, rec, k):
    """Largest step / median step over a small sweep of one parameter (blurred, so a 1-px line moving a
    sub-pixel is a small change). A reshuffle shows as one step far larger than its neighbours; a steep but
    continuous parameter (water glints swirling) has uniformly large steps and no spike."""
    import cv2
    d, lo, hi = rec.params[k]
    base = m.recipes.defaults(rec)
    imgs = [cv2.GaussianBlur(m.recipes.render_structure(name, dict(base, **{k: d + j * 0.003 * (hi - lo)}),
                                                        5, 344, 192)[0], (0, 0), 3) for j in range(9)]
    scale = float(np.abs(imgs[0]).mean()) or 1.0
    steps = [float(np.abs(b - a).mean()) / scale for a, b in zip(imgs, imgs[1:])]
    return max(steps) / (float(np.median(steps)) + 0.005)


def test_continuous_parameters_morph_without_reshuffling(m):
    # Before the discrete sets, spider reshuffled 126 % of its frame per bead_gap step; before noise_1d drew a
    # fixed number of points, silkline's wobble_len reshuffled its whole edge (spike 12.2).
    spikes = [f"{name}.{k}: {s:.1f}" for name, rec in m.recipes.RECIPES.items()
              for k in rec.params if k not in rec.discrete and (s := _spike(m, name, rec, k)) > SPIKE]
    assert not spikes, spikes


def test_the_spike_measure_sees_a_reshuffle(m):
    # The other direction: a parameter known to reshuffle must score over the threshold, or the test above
    # proves nothing.
    assert _spike(m, "ice", m.recipes.RECIPES["ice"], "branch") > SPIKE


# ── same-kind statistics ────────────────────────────────────────────────────
def test_mirror_and_self_score_zero_on_every_gated_statistic(m):
    import cv2
    img = m.compose.render(_model(m, "dunes", seed=2), 688, 384)
    s = m.kind_stats.stats(img)
    for other in (img, cv2.flip(img, 1)):
        d = m.kind_stats.distance(s, m.kind_stats.stats(other))
        for k in m.kind_stats.GATED:
            assert d[k] < 1e-4 * m.kind_stats.DEFAULT_BAR[k], k   # float noise from the flipped resize


def test_a_different_motif_fails_every_gated_statistic(m):
    # Every statistic must fail, not just one: otherwise three of the four could be constants and this passes.
    a = m.compose.render(_model(m, "line"), 1376, 768)
    b = m.compose.render(_model(m, "rain"), 1376, 768)
    v = m.kind_stats.verdict(m.kind_stats.distance(m.kind_stats.stats(a), m.kind_stats.stats(b)))
    assert v["passed"] == 0, v["distance"]


def test_wasserstein_is_not_a_mean_difference(m):
    a = np.linspace(0, 100, 2000)
    assert m.kind_stats.wasserstein_1d(a, a + 7.5) == pytest.approx(7.5, abs=0.05)
    split = np.r_[np.zeros(1000), np.full(1000, 10.0)]     # same mean as the constant, W1 = 5
    assert m.kind_stats.wasserstein_1d(split, np.full(2000, 5.0)) == pytest.approx(5.0, abs=0.05)


def test_verdict_cost_counts_only_distance_past_the_bar(m):
    bar = m.kind_stats.DEFAULT_BAR
    at_bar = {k: bar[k] for k in m.kind_stats.GATED}
    at_bar.update(orient=0, backgr=0)
    v = m.kind_stats.verdict(at_bar)
    assert v["passed"] == 4 and v["excess"] == 0.0
    over = dict(at_bar, hist=bar["hist"] * 3)
    assert m.kind_stats.verdict(over)["excess"] == pytest.approx(2.0)


def test_calibration_refuses_too_few_or_identical_pairs(m):
    img = m.compose.render(_model(m, "ripple"), 344, 192)
    with pytest.raises(ValueError):
        m.kind_stats.calibrate([(img, img), (img, img)])
    with pytest.raises(ValueError):                      # a zero bar would divide by zero in every verdict
        m.kind_stats.calibrate([(img, img)] * 3)


# ── fitting ─────────────────────────────────────────────────────────────────
def test_background_fit_ignores_the_masked_structure(m):
    h, w = 192, 344
    r = np.random.default_rng(1)
    coefs = [list(r.normal(0, 2, 49)) for _ in range(3)]
    for c in coefs:
        c[0] = 20.0
    true = {"deg": 6, "coefs": coefs}
    surface = m.compose.eval_background(true, w, h)
    img = surface.copy()
    mask = np.zeros((h, w), bool)
    mask[80:110, 100:240] = True
    img[mask] += 200.0                                   # bright structure the fit must not chase
    got = m.compose.eval_background(m.compose.fit_background(img, mask, deg=6, step=2), w, h)
    assert np.abs(got - surface)[~mask].max() < 0.5


def test_fit_never_ends_worse_than_the_measured_defaults(m):
    # A stroke recipe at a 1920x1080 reference: the size where a search scored elsewhere ended worse
    # (grass 1/4 -> 0/4) while its own cost improved.
    target = m.compose.render(_model(m, "grass", seed=9, blades=320, lean=1.6), 1920, 1080)
    before = m.fitting.baseline(target, "grass")["verdict"]["cost"]
    model = m.fitting.fit(target, "grass", budget=8, seeds=2)
    assert model["verdict"]["cost"] <= before
    assert model["fit"]["evals"] <= 8
    again = m.fitting.score(model, m.kind_stats.stats(target), 1920, 1080, m.kind_stats.STATS_SIZE, None)
    assert again["cost"] == model["verdict"]["cost"]   # the reported verdict is the saved model's score


def test_fit_keeps_the_defaults_when_they_are_already_best(m):
    # The guarantee only bites when no search candidate beats the defaults: a target drawn from the defaults
    # themselves. (Mutation-verified: dropping the defaults as a candidate survived the far-target test above,
    # because a random candidate beat them there anyway.)
    target = m.compose.render(_model(m, "grass", seed=1), 1376, 768)
    before = m.fitting.baseline(target, "grass", seed=1)["verdict"]["cost"]
    model = m.fitting.fit(target, "grass", budget=6, seeds=1)
    assert model["verdict"]["cost"] <= before


def test_fit_is_deterministic(m):
    # The target sits far from the defaults so the search must move; a search that never leaves its
    # start would be 'deterministic' whether or not it is seeded (mutation-verified: a 6-render fit at a
    # near-default target survived an unseeded rng).
    target = m.compose.render(_model(m, "line", line_y=0.82, fade=1.2, core=4.0), 1376, 768)
    defaults = m.recipes.defaults(m.recipes.RECIPES["line"])
    a = m.fitting.fit(target, "line", budget=12, seeds=1)
    b = m.fitting.fit(target, "line", budget=12, seeds=1)
    assert any(abs(a["params"][k] - defaults[k]) > 1e-6 for k in defaults), "the search never moved"
    assert a["params"] == b["params"] and a["gain"] == b["gain"]


# ── animation timing ────────────────────────────────────────────────────────
def test_similar_states_transition_faster_than_unlike_ones(m):
    models = {"a": _model(m, "ripple", wavelength=46), "b": _model(m, "ripple", wavelength=60),
              "c": _model(m, "rain")}
    near = m.animate.plan([{"model": "a", "at": 0}, {"model": "b", "at": 10}], models, duration=40)
    far = m.animate.plan([{"model": "a", "at": 0}, {"model": "c", "at": 10}], models, duration=40)
    tn, tf = near["transitions"][0]["seconds"], far["transitions"][0]["seconds"]
    assert near["transitions"][0]["distance"] < far["transitions"][0]["distance"]
    assert 2.5 <= tn < tf <= 9.0


def test_transition_time_is_k_times_distance_between_the_clamps(m):
    models = {"a": _model(m, "ripple", wavelength=30), "b": _model(m, "ripple", wavelength=90)}
    d = m.animate.distance(models["a"], models["b"])
    assert 0 < d < 1
    for target in (3.0, 5.0, 8.0):                       # the linear middle of the law, at three slopes
        p = m.animate.plan([{"model": "a", "at": 0}, {"model": "b", "at": 1}], models, k=target / d,
                           t_min=2.5, t_max=9.0, duration=60)
        assert p["transitions"][0]["seconds"] == pytest.approx(target)


def test_transition_is_clamped_to_the_law(m):
    models = {"a": _model(m, "line"), "b": _model(m, "line"), "c": _model(m, "dust")}
    same = m.animate.plan([{"model": "a", "at": 0}, {"model": "b", "at": 5}], models, duration=60)
    assert same["transitions"][0]["seconds"] == 2.5            # identical models still never cut
    far = m.animate.plan([{"model": "a", "at": 0}, {"model": "c", "at": 5}], models, k=1000, duration=60)
    assert far["transitions"][0]["seconds"] == 9.0


def test_plan_refuses_a_law_that_could_cut(m):
    models = {"a": _model(m, "line")}
    for law in ({"t_min": 0}, {"t_min": -1}, {"t_min": 5, "t_max": 3}, {"k": 0}):
        with pytest.raises(ValueError):
            m.animate.plan([{"model": "a", "at": 0}], models, **law)


def test_a_state_that_comes_too_soon_is_delayed_not_cut(m):
    models = {"a": _model(m, "line"), "c": _model(m, "dust"), "d": _model(m, "rain")}
    p = m.animate.plan([{"model": "a", "at": 0}, {"model": "c", "at": 5}, {"model": "d", "at": 9}], models,
                       duration=60)
    assert p["transitions"][0]["seconds"] == pytest.approx(4.0)  # 4 s to the next state >= t_min: shortened
    p2 = m.animate.plan([{"model": "a", "at": 0}, {"model": "c", "at": 5}, {"model": "d", "at": 6}], models,
                        duration=60)
    first, second = p2["transitions"]
    assert first["seconds"] == 2.5
    assert second["start"] == pytest.approx(first["start"] + first["seconds"])   # delayed to the end
    assert p2["warnings"]
    tl = m.animate.Timeline(p2, models, 172, 96)
    edge = second["start"]
    before, after = tl.frame(edge - 1e-4, 0), tl.frame(edge + 1e-4, 0)
    assert np.abs(before - after).mean() < 1.0, "the picture jumped at a transition boundary"


def test_plan_rejects_an_unknown_model(m):
    with pytest.raises(ValueError):
        m.animate.plan([{"model": "nope", "at": 0}], {})


def test_parameter_morph_runs_from_a_to_b(m):
    a, b = _model(m, "ripple", wavelength=30), _model(m, "ripple", wavelength=90)
    assert m.animate.blend_models(a, b, 0.0)["params"]["wavelength"] == pytest.approx(30)
    assert m.animate.blend_models(a, b, 1.0)["params"]["wavelength"] == pytest.approx(90)
    assert m.animate.blend_models(a, _model(m, "rain"), 0.5) is None   # unlike recipes dissolve instead


def test_a_discrete_parameter_dissolves_instead_of_morphing(m):
    a, b = _model(m, "spider", threads=4), _model(m, "spider", threads=7)
    assert m.animate.blend_models(a, b, 0.5) is None
    assert m.animate.param_distance(a, b) >= m.animate.NEAR
    c = _model(m, "spider", threads=4, bead=1.5)                 # bead is continuous: this one morphs
    assert m.animate.blend_models(a, c, 0.5) is not None


def test_background_blend_has_no_jump_at_its_midpoint(m):
    a, b = _model(m, "line"), _model(m, "line")
    a["grain"] = b["grain"] = {"std": 0.0, "blur": 0.0}
    a["background"] = {"deg": 0, "coefs": [[250.0], [250.0], [250.0]]}
    b["background"] = {"deg": 1, "coefs": [[20.0, 0, 0, 0]] * 3}
    tl = m.animate.Timeline({"states": [], "transitions": []}, {}, 172, 96)
    lo = tl._render(m.animate.blend_models(a, b, 0.499), 1.0, 0)
    hi = tl._render(m.animate.blend_models(a, b, 0.501), 1.0, 0)
    assert np.abs(lo - hi).max() < 2.0


def test_timeline_holds_then_eases(m):
    models = {"a": _model(m, "ripple", wavelength=30), "b": _model(m, "ripple", wavelength=90)}
    p = m.animate.plan([{"model": "a", "at": 0}, {"model": "b", "at": 2}], models, duration=20)
    T = p["transitions"][0]["seconds"]
    tl = m.animate.Timeline(p, models, 160, 90)
    a0, b0, _ = tl.state_at(1.0)
    assert a0 is models["a"] and b0 is None
    _, b1, u = tl.state_at(2.0 + T / 4)
    assert b1 is models["b"] and u == pytest.approx(m.animate.smootherstep(0.25))   # eased, not linear
    assert u < 0.2
    assert tl.state_at(19.0) == (models["b"], None, 1.0)
    assert tl.frame(3.0, 3).shape == (90, 160, 3)
