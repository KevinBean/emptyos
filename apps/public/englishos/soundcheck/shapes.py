"""soundcheck — the round shapes: how one item becomes one question.

Pure, stdlib-only. Owns: the ``SHAPES`` table (one ``ShapeSpec`` per round form)
and the builders that turn a bank item plus a little context into the payload
the page renders. Source of truth for *what a question looks like*.

This module is the DATA half of the engine split. ``engine.py`` is the code
half, and the contract between them is deliberately narrow:

    build(item, ctx) -> Round(payload, answer)

``engine.py`` must never mention a shape name. If it ever needs to, the
abstraction has failed and the fix belongs here, not there. Adding a seventh
shape should be one ``ShapeSpec`` row, one builder, and one page renderer —
with no engine change at all. That is the acceptance test.

**The answer never travels.** ``Round.payload`` is what the page receives;
``Round.answer`` stays server-side. Truth in the payload would be visible in
devtools, and the confusion matrix needs server-side truth regardless.

**"All the same" is an option, not a flag.** The odd-one-out shape draws, at a
rate the generator fixed, a set with no odd member whose answer is literally
``["same"]``. Modelling it as a UI checkbox would let the page infer the answer
from its own chrome; modelling it as an option means elimination genuinely
fails.

No ``self``, no I/O — every builder is a function of its arguments.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from .shared import (
    DEFAULT_DEADLINE_MS,
    SAME_KEY,
    SHAPE_CONFIDENCE,
)


@dataclass(frozen=True)
class Round:
    """What a built round is: what the page sees, and what it does not."""

    payload: dict
    answer: list[str]


@dataclass(frozen=True)
class BuildContext:
    """Everything a builder may know that is not in the item."""

    rid: int
    modality: str = "ear"            # ear | eye
    heat: int = 1                    # 0..4 — drives how many distractors show
    deadline_ms: int = DEFAULT_DEADLINE_MS
    audio: dict[str, str] = field(default_factory=dict)   # word -> url
    rng: random.Random = field(default_factory=random.Random)


@dataclass(frozen=True)
class ShapeSpec:
    """One round form, declared."""

    id: str
    prompt: str
    build: Callable[[dict, BuildContext], Round]
    modalities: tuple[str, ...] = ("ear", "eye")
    allow_none_option: bool = False
    min_options: int = 2
    max_options: int = 5
    deadline_scale: float = 1.0
    # A shape whose stimulus is only ever audio cannot run without a voice
    # engine, and the session planner drops it rather than rendering a dead ▶.
    needs_audio: bool = False


# ── helpers shared by builders ────────────────────────────────────

def _word_of(item: dict, index: int = 0) -> dict:
    words = item.get("words") or []
    return words[index] if index < len(words) else {}


def _audio_for(text: str, ctx: BuildContext) -> str:
    return ctx.audio.get(text, "")


def _stimulus(item: dict, ctx: BuildContext, *, index: int = 0) -> dict:
    """The thing being asked about, rendered for this modality.

    ``eye`` withholds the audio and ``ear`` withholds the spelling — the second
    matters more than it looks. Showing the word next to the clip turns a
    listening test into a reading test, which is exactly the confound the
    eye/ear split exists to measure.
    """
    w = _word_of(item, index)
    text = w.get("text", "")
    if ctx.modality == "eye":
        return {"mode": "text", "items": [{"key": "s", "text": text}]}
    return {"mode": "audio", "items": [{"key": "s", "audio": _audio_for(text, ctx)}]}


def _visible_options(item: dict, ctx: BuildContext, spec: ShapeSpec) -> list[dict]:
    """Trim the baked option set down to what this heat level should show.

    Heat 0-1 shows the minimum; each step up adds one distractor. The correct
    option is always kept — this narrows the field, it never removes the answer.
    """
    options = list(item.get("options") or [])
    answer = set(item.get("answer") or [])
    budget = max(spec.min_options, min(spec.max_options, spec.min_options + ctx.heat))
    if len(options) <= budget:
        return options

    keep = [o for o in options if o.get("id") in answer]
    pool = [o for o in options if o.get("id") not in answer]
    ctx.rng.shuffle(pool)
    return keep + pool[: max(0, budget - len(keep))]


def _shuffled(options: list[dict], ctx: BuildContext) -> list[dict]:
    """Shuffle, but pin ``same`` last so it never masquerades as a sibling."""
    body = [o for o in options if o.get("id") != SAME_KEY]
    tail = [o for o in options if o.get("id") == SAME_KEY]
    ctx.rng.shuffle(body)
    return body + tail


def _base(item: dict, ctx: BuildContext, spec: ShapeSpec) -> dict:
    return {
        "rid": ctx.rid,
        "shape": spec.id,
        "dimension": item.get("dimension", ""),
        "modality": ctx.modality,
        # An item may override the shape's default question. A linking item is
        # still a `sort`, but "which sound does this word have?" is the wrong
        # thing to ask about a two-word phrase.
        "prompt": item.get("prompt") or spec.prompt,
        "deadline_ms": int(ctx.deadline_ms * spec.deadline_scale),
        "select": {"min": 1, "max": 1},
        "meta": {
            "contrast": item.get("contrast_id", ""),
            "heat": ctx.heat,
            "item": item.get("id", ""),
            "stem": item.get("stem_id", ""),
        },
    }


def _finish(payload: dict, options: list[dict], item: dict, spec: ShapeSpec) -> Round:
    payload["options"] = [
        {k: v for k, v in o.items() if k in ("id", "label", "special")} for o in options
    ]
    shown = {o.get("id") for o in options}
    answer = [a for a in (item.get("answer") or []) if a in shown]
    return Round(payload=payload, answer=answer)


# ── the builders ──────────────────────────────────────────────────

def build_minimal_pair(item: dict, ctx: BuildContext) -> Round:
    """Hear one word; say which of two near-identical words it was.

    The tightest diagnostic available for a single contrast, because the two
    options differ in exactly one phone and nothing else. Ear only — shown as
    text the question answers itself.
    """
    spec = SHAPES["minimal_pair"]
    target = _word_of(item, 0).get("text", "")
    payload = _base(item, ctx, spec)
    payload["stimulus"] = {"mode": "audio",
                           "items": [{"key": "s", "audio": _audio_for(target, ctx)}]}
    options = _shuffled(list(item.get("options") or []), ctx)
    return _finish(payload, options, item, spec)


def build_sort(item: dict, ctx: BuildContext) -> Round:
    """Which sound bucket does this word belong to?

    The eye pass tests what the learner believes the spelling says; the ear pass
    tests what they actually hear. Same item, same buckets — so the two are
    directly comparable, which is the whole point of running both.
    """
    spec = SHAPES["sort"]
    payload = _base(item, ctx, spec)
    payload["stimulus"] = _stimulus(item, ctx)
    options = _shuffled(_visible_options(item, ctx, spec), ctx)
    return _finish(payload, options, item, spec)


def build_odd_one_out(item: dict, ctx: BuildContext) -> Round:
    """Four words; which one differs — or do they all match?

    ``same`` is a real answer roughly a fifth of the time. Without it the shape
    is solvable by elimination and stops measuring perception.
    """
    spec = SHAPES["odd_one_out"]
    payload = _base(item, ctx, spec)
    words = item.get("words") or []

    if ctx.modality == "eye":
        stim_items = [{"key": w.get("key", str(i)), "text": w.get("text", "")}
                      for i, w in enumerate(words)]
        mode = "text"
    else:
        stim_items = [{"key": w.get("key", str(i)),
                       "audio": _audio_for(w.get("text", ""), ctx)}
                      for i, w in enumerate(words)]
        mode = "audio"
    payload["stimulus"] = {"mode": mode, "items": stim_items}

    options = _shuffled(list(item.get("options") or []), ctx)
    return _finish(payload, options, item, spec)


def build_stress(item: dict, ctx: BuildContext) -> Round:
    """Which syllable carries the beat?

    Options are ordinal slots rendered as the syllables themselves. Deliberately
    not orthographic hyphenation — deriving that from phones is its own project,
    and the ordinal plus a beat pattern asks the same question without it.
    """
    spec = SHAPES["stress"]
    w = _word_of(item, 0)
    payload = _base(item, ctx, spec)
    text = w.get("text", "")
    stim = {"mode": "both" if ctx.modality == "ear" else "text",
            "items": [{"key": "s", "text": text}]}
    if ctx.modality == "ear":
        stim["items"][0]["audio"] = _audio_for(text, ctx)
    payload["stimulus"] = stim
    payload["meta"]["syllables"] = item.get("syllables", 0)
    payload["meta"]["beat"] = item.get("beat", "")
    options = list(item.get("options") or [])      # ordinals — order is meaning
    return _finish(payload, options, item, spec)


def build_count(item: dict, ctx: BuildContext) -> Round:
    """How many syllables?

    A better eye question than it sounds: ⟨chocolate⟩ looks like three and is
    usually two, so the spelling actively misleads. That gap is the lesson.
    """
    spec = SHAPES["count"]
    payload = _base(item, ctx, spec)
    payload["stimulus"] = _stimulus(item, ctx)
    options = list(item.get("options") or [])      # numeric — order is meaning
    return _finish(payload, options, item, spec)


def build_presence(item: dict, ctx: BuildContext) -> Round:
    """Is a given sound actually there?

    Aimed squarely at deletion: a final consonant that never gets pronounced is
    invisible to the speaker, so asking "did you hear one?" is the only way to
    surface it.
    """
    spec = SHAPES["presence"]
    payload = _base(item, ctx, spec)
    payload["stimulus"] = _stimulus(item, ctx)
    options = list(item.get("options") or [])      # yes/no — order is meaning
    return _finish(payload, options, item, spec)


def build_produce(item: dict, ctx: BuildContext) -> Round:
    """Say it, and be scored.

    The one shape a worksheet cannot do — and the one that depends on the
    flakiest service in the stack, so it ships dark and degrades to a
    self-report that is logged but deliberately excluded from the confusion
    matrix.
    """
    spec = SHAPES["produce"]
    w = _word_of(item, 0)
    text = w.get("text", "")
    payload = _base(item, ctx, spec)
    payload["stimulus"] = {"mode": "audio",
                           "items": [{"key": "s", "text": text,
                                      "audio": _audio_for(text, ctx)}]}
    payload["select"] = {"min": 0, "max": 0}
    payload["record"] = {"reference": text, "max_ms": 6000}
    return Round(payload=payload, answer=[])


# ── the table ─────────────────────────────────────────────────────

SHAPES: dict[str, ShapeSpec] = {
    "minimal_pair": ShapeSpec(
        id="minimal_pair",
        prompt="Which word did you hear?",
        build=build_minimal_pair,
        modalities=("ear",),
        min_options=2, max_options=2,
        deadline_scale=0.7,
        needs_audio=True,
    ),
    "sort": ShapeSpec(
        id="sort",
        prompt="Which sound does this word have?",
        build=build_sort,
        modalities=("ear", "eye"),
        min_options=2, max_options=4,
    ),
    "odd_one_out": ShapeSpec(
        id="odd_one_out",
        prompt="Which one is different?",
        build=build_odd_one_out,
        modalities=("ear", "eye"),
        allow_none_option=True,
        min_options=5, max_options=5,
        deadline_scale=1.4,
    ),
    "stress": ShapeSpec(
        id="stress",
        prompt="Which syllable is stressed?",
        build=build_stress,
        modalities=("ear", "eye"),
        min_options=2, max_options=5,
    ),
    "count": ShapeSpec(
        id="count",
        prompt="How many syllables?",
        build=build_count,
        modalities=("ear", "eye"),
        min_options=2, max_options=5,
    ),
    "presence": ShapeSpec(
        id="presence",
        prompt="Is that sound there?",
        build=build_presence,
        modalities=("ear", "eye"),
        min_options=2, max_options=2,
        deadline_scale=0.8,
    ),
    "produce": ShapeSpec(
        id="produce",
        prompt="Say the word.",
        build=build_produce,
        modalities=("ear",),
        min_options=0, max_options=0,
        deadline_scale=0.0,
        needs_audio=True,
    ),
}


def spec_for(shape: str) -> ShapeSpec | None:
    return SHAPES.get(shape)


def build_round(item: dict, ctx: BuildContext) -> Round:
    """The single entry point ``engine.py`` uses. Shape is data, not a branch."""
    spec = SHAPES.get(item.get("shape", ""))
    if spec is None:
        raise ValueError(f"unknown shape {item.get('shape')!r}")
    return spec.build(item, ctx)


def shapes_for(*, audio_ok: bool, modality: str = "ear") -> list[str]:
    """Which shapes are playable right now.

    With no voice engine the audio-only shapes drop out entirely rather than
    rendering a play button that cannot work. Three dimensions remain fully
    playable by eye, which is what keeps a silent install a real game.
    """
    out = []
    for spec in SHAPES.values():
        if spec.needs_audio and not audio_ok:
            continue
        if modality not in spec.modalities:
            continue
        out.append(spec.id)
    return out


def confidence_for(shape: str) -> float:
    """How diagnostic a miss on this shape is, for the telemetry write-back."""
    return SHAPE_CONFIDENCE.get(shape, 0.5)
