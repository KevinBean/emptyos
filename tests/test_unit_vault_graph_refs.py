"""vault-graph — node labels and frontmatter-ref resolution (offline, no daemon).

Both behaviours here were added because a generated viz artifact was measured
to be an *isolated node*: correct address, correct tag, and zero edges. The
tests pin the two things that changed that.

`_resolve_ref` is a pure staticmethod, so the module imports standalone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from helpers import load_app_module  # noqa: E402

VG = load_app_module("vault-graph", "app")
resolve = VG.VaultGraphApp._resolve_ref

NAMES = {"cable rating": "20_Areas/cable rating.md"}
SLUGS = {
    "cse-thrust": "30_Resources/EmptyOS/kb/notes/cse-thrust.md",
    "cable rating": "20_Areas/cable rating.md",
    "kb/cse-thrust": "weird/literally-named.md",
}


def test_exact_slug_still_resolves():
    assert resolve("cse-thrust", {}, SLUGS) == SLUGS["cse-thrust"]


def test_bracketed_and_suffixed_forms_still_resolve():
    assert resolve("[[cse-thrust]]", {}, SLUGS) == SLUGS["cse-thrust"]
    assert resolve("cse-thrust.md", {}, SLUGS) == SLUGS["cse-thrust"]
    assert resolve("cse-thrust#Diagram", {}, SLUGS) == SLUGS["cse-thrust"]
    assert resolve("cse-thrust|alias", {}, SLUGS) == SLUGS["cse-thrust"]


def test_qualified_reference_resolves_on_its_last_segment():
    """`used_in: kb/cse-thrust` must reach the note.

    Both index maps are keyed by bare name/stem, so a namespaced value misses
    the exact lookup even though the note is sitting right there — which is
    why the artifact→note edge did not form before this fallback existed.
    """
    slugs = {k: v for k, v in SLUGS.items() if k != "kb/cse-thrust"}
    assert resolve("kb/cse-thrust", {}, slugs) == slugs["cse-thrust"]
    assert resolve("publish/cable-thrust", {}, {"cable-thrust": "P.md"}) == "P.md"


def test_an_exact_match_beats_the_last_segment_fallback():
    """A note literally named `kb/cse-thrust` must still win over the fallback."""
    assert resolve("kb/cse-thrust", {}, SLUGS) == "weird/literally-named.md"


def test_unresolvable_values_stay_unresolved():
    assert resolve("", {}, SLUGS) is None
    assert resolve("no-such-note", {}, SLUGS) is None
    # The fallback must not invent an edge for a path whose tail matches nothing.
    assert resolve("kb/no-such-note", {}, SLUGS) is None


def test_used_in_is_a_ref_field():
    """Without this the artifact's own provenance produces no edge at all."""
    assert "used_in" in VG.FRONTMATTER_REF_FIELDS


@pytest.mark.parametrize(
    "entry,expected",
    [
        ({"properties": {"title": "CSE thrust elevation schematic"}, "name": "record"},
         "CSE thrust elevation schematic"),
        # No title -> the historical stem behaviour, hyphens spaced.
        ({"properties": {}, "name": "cable-rating"}, "cable rating"),
        ({"properties": {"title": "   "}, "name": "record"}, "record"),
    ],
)
def test_node_label_prefers_declared_title_over_filename_stem(entry, expected):
    """Every `<app>/outputs/<id>/record.md` shares the stem `record`, so without
    this the graph shows N nodes all labelled the same and none identifiable."""
    assert VG.VaultGraphApp._node_label(entry) == expected
