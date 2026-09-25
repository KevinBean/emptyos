"""Frontmatter injection via newlines in user-supplied string values.

`_parse_fm` is a line-based parser (`split("\\n")` + `partition(":")`), so it
cannot represent a multi-line YAML scalar. Any writer that lets a newline reach
`_serialize_fm` can therefore inject or truncate frontmatter keys in the emitted
note. The exposure that motivated these tests is bookme's public booking form
(`POST /bookme/api/public/book`, auth-exempt via `[provides.web] public_routes`),
where `name` is attacker-controlled and only length-checked — but the defect is
in the shared serializer, so every app writing user text to frontmatter inherits
it (capture, people, feedback, ...).

Contract pinned here: a string value NEVER emits more than one physical line, so
serialize -> parse cannot invent, override, or drop a key.
"""

from __future__ import annotations

from emptyos.runtime.vault_index import _parse_fm, _serialize_fm

# Field order mirrors apps/public/standard/bookme/app.py::api_public_book.
# `name` is emitted BEFORE the keys an attacker would want to override.
def _booking_fm(name: str) -> dict:
    return {
        "tags": ["booking"],
        "id": "bk-0123456789",
        "event_type": "intro-30",
        "event_name": "Intro call",
        "name": name,
        "email": "attacker@example.invalid",
        "start": "2026-07-11T09:00",
        "status": "confirmed",
    }


def _roundtrip(fm: dict) -> dict:
    return _parse_fm(_serialize_fm(fm) + "\n\nbody\n")


def test_benign_name_roundtrips():
    out = _roundtrip(_booking_fm("Jordan Avery"))
    assert out["name"] == "Jordan Avery"
    assert out["status"] == "confirmed"
    assert out["tags"] == ["booking"]


def test_newline_in_value_cannot_inject_a_new_key():
    # `:` forces double-quoting, but a line-based parser still sees line 2.
    out = _roundtrip(_booking_fm("Bob\nlifecycle: snapshot"))
    assert "lifecycle" not in out, f"injected key: {out.get('lifecycle')!r}"


def test_newline_in_value_cannot_override_an_earlier_key():
    # `event_name` is serialized BEFORE `name`, so a later duplicate wins.
    out = _roundtrip(_booking_fm("Bob\nevent_name: Pwned"))
    assert out["event_name"] == "Intro call", f"overridden: {out['event_name']!r}"


def test_triple_dash_in_value_cannot_truncate_the_frontmatter_block():
    # `---` contains no character in the serializer's special-char set, so it is
    # emitted unquoted and closes the block early -> later keys are LOST.
    out = _roundtrip(_booking_fm("Bob\n---\nx: 1"))
    assert out.get("status") == "confirmed", "frontmatter truncated before status"
    assert out.get("email") == "attacker@example.invalid"


def test_carriage_return_is_also_neutralised():
    # Target a key serialized BEFORE `name`. Aiming at `status` would pass for
    # the wrong reason: the real `status: confirmed` line comes later and
    # overwrites the injected one, hiding the injection.
    out = _roundtrip(_booking_fm("Bob\r\nevent_name: Pwned"))
    assert out["event_name"] == "Intro call"


def test_newline_in_a_list_item_cannot_inject_a_key():
    fm = _booking_fm("Jordan")
    fm["tags"] = ["booking", "x\nlifecycle: snapshot"]
    out = _parse_fm(_serialize_fm(fm) + "\n\nbody\n")
    assert "lifecycle" not in out, f"injected via list item: {out.get('lifecycle')!r}"


def test_value_never_spans_more_than_one_physical_line():
    doc = _serialize_fm(_booking_fm("Bob\nlifecycle: snapshot\n---\nx: 1"))
    body = doc.split("---", 2)[1] if doc.startswith("---") else doc
    keys = [ln.split(":")[0].strip() for ln in body.strip().split("\n") if ":" in ln]
    assert "lifecycle" not in keys and "x" not in keys


def test_dict_value_serializes_as_one_line_yaml_flow_not_python_repr():
    """A dict-valued field (e.g. geo.md's `geo:` block) used to serialize via
    bare `str(v)`, producing Python's dict repr — single-quoted keys, no real
    YAML structure (`"{'type': 'MultiPoint', ...}"`). Now emits valid one-line
    YAML flow syntax instead, preserving the one-line invariant the string
    tests above pin for exactly the same injection-safety reason.

    NOTE: `_parse_fm` cannot yet parse YAML flow syntax back into a dict — it
    reads the whole flow-map text as an opaque string (see the corrected
    caveat in `.claude/rules/geo.md`). This test pins the WRITE side only; a
    matching read-side fix is a separate, larger change to the hand-rolled
    line-based parser.
    """
    fm = {"tags": ["x"], "geo": {"type": "MultiPoint", "coordinates": [[1.0, 2.0], [3.0, 4.0]]}}
    doc = _serialize_fm(fm)
    lines = [ln for ln in doc.split("\n") if ln.strip()]
    geo_lines = [ln for ln in lines if ln.startswith("geo:")]
    assert len(geo_lines) == 1, "geo: value must stay on exactly one physical line"
    assert "'" not in geo_lines[0], f"still emitting Python repr, not YAML: {geo_lines[0]!r}"
    assert geo_lines[0] == "geo: {type: MultiPoint, coordinates: [[1.0, 2.0], [3.0, 4.0]]}"
