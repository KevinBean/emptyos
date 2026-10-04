"""Pin the shared bundle to FRONTEND-DESIGN-LANGUAGE §4.1/§5 where no scanner looks.

`check_hardcoded_hex.py` and `check-text-tokens.py` walk `apps/**/pages/` only,
and the 2026-07-11 readability audit found that the last two defects were in the
platform, not in any app. The 2026-09-12 design-system audit found the same
shape again: `.eos-bar-fill { transition:width 0.5s }` — forbidden on both the
property and the duration — was the rule 28 app pages had copied, and
`.eos-pill-*` painted fixed dark-theme brights as text on the light themes.

Both directions are pinned: a healthy bundle passes, and re-introducing either
shape fails. The first draft of this file was green while the bundle carried
six `transition: all` (which includes width) and a 1 s `animation`; the hostile
review that found those is why the motion checks read the whole stripped text,
treat `all` as geometry, and cover `animation` with an explicit exception list.
Mutation-verified with `.claude/skills/eos-mutation-verify`.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "emptyos" / "web" / "static" / "eos-components.css"

# §5: 120–200 ms hover, 250 ms panels, 300 ms max for anything visible. The two
# documented exceptions (hands-free pulse, breath pacer) do not live in this
# file. These continuous or ambient animations are exempted here, each with the
# reason a 300 ms cap does not apply:
ANIMATION_EXCEPTIONS = {
    "spin": "a spinner cycles; 300 ms would be frantic, and it is not an entry motion",
    "eosRecPulse": "the recording pulse — the same 0.8–1.4 s class as the hands-free pulse §5 exempts",
    "eosAiStreamingPulse": "§6 streaming pulse on the container border while tokens arrive",
    "eos-flash-anim": "a one-shot highlight fade that must stay visible long enough to be seen",
    "eos-gridDrift": "ambient 60 s background drift on the demo banner; not an interaction",
}
MAX_MS = 300

# Anchors the number: `.15s` must read as 0.15 s, not 15 s — the unanchored form
# inflated the audit's first count from 42 to 147.
DURATION = re.compile(r"(?<![\w.])(\d*\.?\d+)(ms|s)\b")
# `all` includes width/height/top/left, so it is a geometry transition.
GEOMETRY = re.compile(r"(?:^|[\s,])(all|width|height|top|left|right|bottom)\b")


def _strip_comments(css: str) -> str:
    # Keep the newlines inside comments so reported line numbers stay real.
    return re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", "", m.group(0)), css, flags=re.S)


def _line_of(css: str, pos: int) -> int:
    return css.count("\n", 0, pos) + 1


def _declarations(css: str, prop: str) -> list[tuple[int, str]]:
    """Every `<prop>: value` in the stripped text, across line breaks."""
    out = []
    for m in re.finditer(r"(?<![\w-])" + prop + r"\s*:\s*([^;}]+)", css):
        out.append((_line_of(css, m.start()), " ".join(m.group(1).split())))
    return out


def _duration_ms(spec: str) -> list[float]:
    return [float(n) * (1000 if unit == "s" else 1) for n, unit in DURATION.findall(spec)]


def _css() -> str:
    return _strip_comments(CSS.read_text(encoding="utf-8"))


def test_bundle_has_motion_to_check():
    # A rewrite that drops every transition/animation would satisfy the rules
    # below vacuously; assert the input has the shape the rules are about.
    css = _css()
    assert len(_declarations(css, "transition")) >= 10
    assert len(_declarations(css, "animation")) >= 5


def test_no_geometry_transitions():
    css = _css()
    bad = [(i, spec) for i, spec in _declarations(css, "transition") if GEOMETRY.search(spec)]
    bad += [(i, spec) for i, spec in _declarations(css, "transition-property") if GEOMETRY.search(spec)]
    assert not bad, f"§5 forbids animating width/height/top/left (and `all` includes them): {bad}"


def test_no_transition_over_300ms():
    css = _css()
    decls = _declarations(css, "transition") + _declarations(css, "transition-duration")
    bad = [(i, spec) for i, spec in decls if any(ms > MAX_MS for ms in _duration_ms(spec))]
    assert not bad, f"§5 caps visible motion at {MAX_MS}ms: {bad}"


def test_no_animation_over_300ms_outside_exceptions():
    css = _css()
    bad = []
    for i, spec in _declarations(css, "animation"):
        name = spec.split()[0]
        if name in ("none", "unset", "inherit") or name in ANIMATION_EXCEPTIONS:
            continue
        if any(ms > MAX_MS for ms in _duration_ms(spec)):
            bad.append((i, spec))
    assert not bad, f"§5 caps entry/interaction animations at {MAX_MS}ms: {bad}"


def test_animation_exceptions_are_all_in_use():
    # An exception nobody uses is a hole waiting for a name; keep the list honest.
    css = _css()
    names = {spec.split()[0] for _, spec in _declarations(css, "animation")}
    unused = set(ANIMATION_EXCEPTIONS) - names
    assert not unused, f"exempted animation names not in the bundle: {unused}"


def test_bar_fill_reveal_is_a_transform():
    css = _css()
    m = re.search(r"\.eos-bar-fill\s*\{([^}]*)\}", css)
    assert m, ".eos-bar-fill rule missing"
    body = m.group(1)
    for _, spec in _declarations(body + ";", "transition"):
        assert not GEOMETRY.search(spec), spec
        assert all(ms <= MAX_MS for ms in _duration_ms(spec)), spec
    assert "eos-bar-grow" in body and "transform-origin" in body
    assert re.search(r"@keyframes\s+eos-bar-grow\s*\{[^}]*scaleX\(0\)", css)


# §4.1: a status/pill text colour must come from a theme token so it survives
# every theme. The rule is inverted on purpose — the value must BE a token
# reference — because enumerating forbidden notations (hex, rgb(), `white`)
# is exactly the list a rewrite can step around.
TOKENISED_FAMILIES = (
    r"\.eos-pill-(?:blue|amber|green|emerald|red|purple|orange|gray)\s*\{",
    r"\.eos-conf-badge\.(?:ok|bad)\s*\{",
    r"\.eos-diff-(?:add|del)\s*\{",
)
PILL_GROUP = r"\.eos-pill-blue,[^{]*\{"


def _rule_bodies(css: str, selector_re: str) -> list[str]:
    return [m.group(1) for m in re.finditer(selector_re + r"([^}]*)\}", css)]


def _colour_values(bodies: list[str]) -> list[str]:
    out = []
    for body in bodies:
        for m in re.finditer(r"(?<![\w-])(?:color|--pc)\s*:\s*([^;]+)", body):
            out.append(m.group(1).strip())
    return out


def test_status_families_present():
    css = _css()
    for fam in TOKENISED_FAMILIES:
        assert _rule_bodies(css, fam), f"no rules matched {fam!r} — the pin would pass vacuously"
    assert _colour_values(_rule_bodies(css, PILL_GROUP)), "the shared .eos-pill-* colour rule is missing"


def test_status_text_colour_is_a_token():
    css = _css()
    values = _colour_values(_rule_bodies(css, PILL_GROUP))
    for fam in TOKENISED_FAMILIES:
        values += _colour_values(_rule_bodies(css, fam))
    assert values, "no colour declarations found — vacuous"
    bad = [v for v in values if "var(--" not in v]
    assert not bad, f"status/pill colour that is not a theme-token reference: {bad}"


def test_pill_palette_keys_are_distinct():
    # A category palette's whole job is N distinguishable colours; two keys
    # resolving to the same token (green == emerald) is a silent collapse.
    css = _css()
    seen: dict[str, str] = {}
    for m in re.finditer(r"\.eos-pill-(\w+)\s*\{\s*--pc\s*:\s*([^;]+);", css):
        key, value = m.group(1), " ".join(m.group(2).split())
        assert value not in seen.values(), f"pill {key!r} duplicates {seen} → {value!r}"
        seen[key] = value
    assert len(seen) >= 8, seen
