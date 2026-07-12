"""Unit tests for VaultIndex.find() property matching — offline, no daemon.

Pins the boolean-query fix: _parse_fm stores YAML scalars as strings ("true"),
so find(active=True) must still match a note whose frontmatter is `active: true`.
String/int callers must be byte-for-byte unchanged.
"""

from __future__ import annotations

import asyncio

from emptyos.runtime.vault_index import VaultIndex


def _idx(entries):
    vi = VaultIndex(kernel=None)  # __init__ only stores the ref + empty _files
    for path, props in entries.items():
        vi._files[path] = {
            "name": path,
            "folder": path.rsplit("/", 1)[0] if "/" in path else "",
            "ext": "md",
            "size": 0,
            "modified": 0,
            "properties": props,
            "tags": props.get("_tags", []),
        }
    return vi


def test_prop_eq_boolean_matches_lowercase_string():
    # the regression: True vs stored "true"
    assert VaultIndex._prop_eq("true", True) is True
    assert VaultIndex._prop_eq("True", True) is True
    assert VaultIndex._prop_eq("false", False) is True
    assert VaultIndex._prop_eq("true", False) is False
    assert VaultIndex._prop_eq("false", True) is False


def test_prop_eq_string_and_int_unchanged():
    assert VaultIndex._prop_eq("offer", "offer") is True
    assert VaultIndex._prop_eq("Open", "open") is False  # NOT loosened to case-insensitive
    assert VaultIndex._prop_eq("5", 5) is True
    assert VaultIndex._prop_eq("2026", 2026) is True
    assert VaultIndex._prop_eq(None, "x") is False


def test_find_boolean_property_now_matches():
    vi = _idx(
        {
            "a.md": {"published": "true", "kind": "doc"},
            "b.md": {"published": "false", "kind": "doc"},
        }
    )
    hits = vi.find(published=True)
    assert [e["name"] for e in hits] == ["a.md"]
    hits_false = vi.find(published=False)
    assert [e["name"] for e in hits_false] == ["b.md"]


def test_find_string_property_unchanged():
    vi = _idx({"a.md": {"status": "offer"}, "b.md": {"status": "applied"}})
    assert [e["name"] for e in vi.find(status="offer")] == ["a.md"]
    assert vi.find(status="OFFER") == []  # exact match preserved


def _linear_paths(vi, *, tags=None, folder=None, **properties):
    """Reference implementation from before the inverted tag index."""
    paths = []
    for entry in vi._files.values():
        if tags and not all(vi._tag_matches(tag, entry.get("tags", [])) for tag in tags):
            continue
        if folder is not None and entry["folder"] != folder:
            continue
        props = entry.get("properties", {})
        if properties and not all(vi._prop_eq(props.get(k), v) for k, v in properties.items()):
            continue
        paths.append(entry["path"])
    return sorted(paths)


def test_inverted_tag_index_matches_linear_find(tmp_path):
    notes = {
        "a.md": "---\ntags:\n  - people\n  - active\nstatus: open\n---\nA",
        "b.md": "---\ntags:\n  - people/friend\n  - active\nstatus: closed\n---\nB",
        "c.md": "---\ntags: [people/family, archived]\nstatus: open\n---\nC",
        "sub/d.md": "---\ntags:\n  - place\n  - active\nstatus: open\n---\nD",
        "e.md": "---\ntags:\n  - peoples\nstatus: open\n---\nE",
    }
    for rel, content in notes.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    vi = VaultIndex(kernel=None)
    vi._vault = tmp_path
    assert asyncio.run(vi._full_scan()) == len(notes)

    cases = [
        {"tags": ["people"]},                 # exact + hierarchical descendants
        {"tags": ["people/friend"]},          # exact leaf
        {"tags": ["people", "active"]},      # intersection across tags
        {"tags": ["people"], "status": "open"},
        {"tags": ["active"], "folder": "sub"},
        {"tags": ["missing"]},
    ]
    for case in cases:
        expected = _linear_paths(vi, **case)
        actual = sorted(entry["path"] for entry in vi.find(**case))
        assert actual == expected

    assert vi.tag_counts() == {
        "active": 3,
        "people": 1,
        "people/friend": 1,
        "people/family": 1,
        "archived": 1,
        "place": 1,
        "peoples": 1,
    }


def test_reindex_removes_stale_tag_membership(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("---\ntags: [old, shared]\n---\nA", encoding="utf-8")
    vi = VaultIndex(kernel=None)
    vi._vault = tmp_path
    asyncio.run(vi._full_scan())

    path.write_text("---\ntags: [new, shared]\n---\nA", encoding="utf-8")
    vi.index_file("a.md")
    assert vi.find(tags=["old"]) == []
    assert [e["path"] for e in vi.find(tags=["new"])] == ["a.md"]
    assert vi.tag_counts() == {"new": 1, "shared": 1}


def test_note_lock_normalizes_slashes_and_leading_separator():
    vi = VaultIndex(kernel=None)
    assert vi.note_lock("folder\\note.md") is vi.note_lock("/folder/note.md")


# ---------------------------------------------------------------------------
# Result ordering. The inverted tag index resolves candidates through a set;
# set iteration order varies with PYTHONHASHSEED, while the pre-index
# implementation walked _files in stable scan order. Callers that take
# results[0] must not get a different note run-to-run.
# ---------------------------------------------------------------------------

def _tagged_vault(tmp_path, names, tag="kb"):
    for n in names:
        (tmp_path / f"{n}.md").write_text(f"---\ntags:\n  - {tag}\n---\nbody", encoding="utf-8")
    vi = VaultIndex(kernel=None)
    vi._vault = tmp_path
    for n in names:
        vi._index_one(f"{n}.md", tmp_path / f"{n}.md")
    return vi


def test_tag_find_returns_deterministic_order(tmp_path):
    names = ["zeta", "alpha", "mike", "bravo", "yankee", "charlie"]
    vi = _tagged_vault(tmp_path, names)
    paths = [e["path"] for e in vi.find(tags=["kb"])]
    assert paths == sorted(paths)


def test_tag_find_order_is_stable_across_calls(tmp_path):
    vi = _tagged_vault(tmp_path, ["c", "a", "b"])
    first = [e["path"] for e in vi.find(tags=["kb"])]
    for _ in range(5):
        assert [e["path"] for e in vi.find(tags=["kb"])] == first
