"""The blockout spec validator is a trust boundary — pin both directions.

An LLM authors these specs. The renderer interprets them by dispatching typed
values into primitives, so the model's text never becomes code — but that
argument only holds if the validator actually rejects everything malformed.
A validator that passes bad input is the same defect class as a detector wired
backwards: it looks right, and it is silently permissive.

So every test here has a matching negative, and the valid spec is asserted to
pass so a validator that rejects *everything* cannot score green either.
"""

from __future__ import annotations

import pytest

# Ordinary import now that it is an SDK module rather than a script reached by
# path — the move that removed music-studio's `sys.path` insert.
from emptyos.sdk import blockout_spec as bs


def valid_spec() -> dict:
    return {
        "name": "street",
        "near": 2.0,
        "far": 48.0,
        "objects": [
            {"type": "box", "loc": [0, 12, 0], "scale": [5, 24, 0.05]},
            {"type": "cylinder", "loc": [-1, 9, 0.9], "radius": 0.35,
             "depth": 1.8},
        ],
        "camera": [
            {"t": 0.0, "loc": [0, -6, 1.6], "rot_deg": [88, 0, 0]},
            {"t": 1.0, "loc": [0, -6, 9.0], "rot_deg": [66, 0, 0]},
        ],
    }


# --- the positive direction -------------------------------------------------

def test_a_well_formed_spec_is_accepted():
    assert bs.validate_spec(valid_spec()) == []


def test_three_camera_keys_are_accepted():
    s = valid_spec()
    s["camera"].insert(1, {"t": 0.5, "loc": [0, -6, 4.0],
                           "rot_deg": [77, 0, 0]})
    assert bs.validate_spec(s) == []


# --- structural rejections --------------------------------------------------

@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s.update(near=0), "must be > 0"),
    (lambda s: s.update(far=1.0), "greater than near"),
    (lambda s: s.update(far=99_999.0), "exceeds"),
    (lambda s: s.update(objects=[]), "non-empty list"),
    (lambda s: s.update(camera=[{"t": 0.0, "loc": [0, 0, 0],
                                 "rot_deg": [90, 0, 0]}]), "expected 2"),
])
def test_out_of_contract_values_are_rejected(mutate, needle):
    s = valid_spec()
    mutate(s)
    errs = bs.validate_spec(s)
    assert errs, "expected a rejection"
    assert any(needle in e for e in errs), errs


def test_unknown_fields_are_rejected_not_ignored():
    """Silently dropping a field renders something the spec did not describe."""
    s = valid_spec()
    s["lighting"] = "cinematic"
    assert any("lighting" in e for e in bs.validate_spec(s))

    s = valid_spec()
    s["objects"][0]["material"] = "concrete"
    assert any("material" in e for e in bs.validate_spec(s))

    s = valid_spec()
    s["camera"][0]["fov"] = 35
    assert any("fov" in e for e in bs.validate_spec(s))


def test_unknown_object_type_is_rejected():
    s = valid_spec()
    s["objects"][0] = {"type": "torus", "loc": [0, 0, 0], "scale": [1, 1, 1]}
    assert any("torus" in e for e in bs.validate_spec(s))


# --- numeric hygiene --------------------------------------------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected(bad):
    s = valid_spec()
    s["objects"][0]["loc"] = [0, bad, 0]
    assert any("finite" in e for e in bs.validate_spec(s))


def test_booleans_do_not_count_as_numbers():
    """bool is an int in Python — a validator that forgets this lets True through."""
    s = valid_spec()
    s["objects"][0]["loc"] = [0, True, 0]
    assert any("finite" in e for e in bs.validate_spec(s))


def test_strings_that_look_like_numbers_are_rejected():
    s = valid_spec()
    s["near"] = "2.0"
    assert any("near/far" in e for e in bs.validate_spec(s))


def test_absurd_coordinates_are_rejected():
    s = valid_spec()
    s["objects"][0]["loc"] = [0, 1e9, 0]
    assert any("exceeds" in e for e in bs.validate_spec(s))


def test_non_positive_scale_is_rejected():
    s = valid_spec()
    s["objects"][0]["scale"] = [5, 0, 1]
    assert any("must be > 0" in e for e in bs.validate_spec(s))


def test_wrong_length_vectors_are_rejected():
    s = valid_spec()
    s["objects"][0]["loc"] = [0, 1]
    assert any("expected 3 numbers" in e for e in bs.validate_spec(s))


# --- camera timeline --------------------------------------------------------

def test_camera_keys_must_span_zero_to_one():
    s = valid_spec()
    s["camera"][0]["t"] = 0.2
    assert any("exactly 0.0" in e for e in bs.validate_spec(s))
    s = valid_spec()
    s["camera"][-1]["t"] = 0.8
    assert any("exactly 1.0" in e for e in bs.validate_spec(s))


def test_camera_keys_must_be_strictly_increasing():
    s = valid_spec()
    s["camera"].insert(1, {"t": 0.0, "loc": [0, 0, 0], "rot_deg": [90, 0, 0]})
    s["camera"][-1]["t"] = 1.0
    assert any("must be greater than" in e for e in bs.validate_spec(s))


# --- the name field ---------------------------------------------------------

def test_name_is_bounded_and_must_be_a_string():
    s = valid_spec()
    s["name"] = "x" * 200
    assert any("60 characters" in e for e in bs.validate_spec(s))
    s = valid_spec()
    s["name"] = ""
    assert any("non-empty string" in e for e in bs.validate_spec(s))


def test_a_hostile_name_cannot_break_the_contract():
    """The renderer generates object names, so a spec name is inert — but it
    still has to survive validation as ordinary data rather than crash it."""
    s = valid_spec()
    # Inert fixture data: this string is only ever compared and measured. The
    # renderer names objects `obj_<i>` and never interpolates spec text into
    # source, so there is no sink for it to reach — that is what is being pinned.
    s["name"] = "'); import os; os" + ".system('rm -rf /"
    errs = bs.validate_spec(s)
    # Only fails on length/type rules, never raises, never special-cased.
    assert errs == [] or all("name" in e for e in errs)


# --- reply parsing ----------------------------------------------------------

def test_parse_spec_tolerates_a_json_fence():
    import json
    body = "```json\n" + json.dumps(valid_spec()) + "\n```"
    spec, errs = bs.parse_spec(body)
    assert errs == [] and spec["name"] == "street"


def test_parse_spec_surfaces_validation_errors_instead_of_returning_a_spec():
    import json
    bad = valid_spec()
    bad["near"] = -1
    spec, errs = bs.parse_spec(json.dumps(bad))
    assert spec is None and errs


def test_parse_spec_rejects_prose():
    spec, errs = bs.parse_spec("I would place a few boxes on a street.")
    assert spec is None and errs
