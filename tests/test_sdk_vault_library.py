"""Unit tests for emptyos.sdk.vault_library — find_file (public) + write_body.

Pure in-process — no daemon required. Uses the directory-scan fallback
(no VaultIndex service in the mock kernel).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from emptyos.sdk.vault_library import VaultLibrary


class _NoteLibrary(VaultLibrary):
    tag = "test-note"
    fields = {"title": str, "status": str}
    fallback_folder = "notes"


def _mk_lib(tmp_path: Path) -> _NoteLibrary:
    config = MagicMock()
    config.notes_path = tmp_path
    services = MagicMock()
    services.get_optional.return_value = None  # no VaultIndex → fallback scan
    kernel = SimpleNamespace(config=config, services=services)
    app = SimpleNamespace(kernel=kernel)
    return _NoteLibrary(app)


FM = "---\ntitle: Demo\nstatus: draft\ntags:\n  - test-note\n---\n"


def _seed(tmp_path: Path, name: str = "demo.md", body: str = "old body\n") -> Path:
    d = tmp_path / "notes"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(FM + "\n" + body, encoding="utf-8")
    return p


def test_find_file_is_public(tmp_path):
    lib = _mk_lib(tmp_path)
    p = _seed(tmp_path)
    assert lib.find_file("demo.md") == p


def test_find_file_private_alias_still_works(tmp_path):
    lib = _mk_lib(tmp_path)
    p = _seed(tmp_path)
    assert lib._find_file("demo.md") == p


def test_write_body_replaces_body_preserving_frontmatter(tmp_path):
    lib = _mk_lib(tmp_path)
    p = _seed(tmp_path)
    res = lib.write_body("demo.md", "new body line 1\nline 2")
    assert res == {"ok": True, "file": "demo.md"}
    content = p.read_text(encoding="utf-8")
    assert content.startswith(FM)  # frontmatter byte-for-byte
    assert content == FM + "\nnew body line 1\nline 2\n"


def test_write_body_appends_md_extension(tmp_path):
    lib = _mk_lib(tmp_path)
    _seed(tmp_path)
    assert lib.write_body("demo", "x")["ok"] is True


def test_write_body_missing_file_errors(tmp_path):
    lib = _mk_lib(tmp_path)
    assert lib.write_body("nope.md", "x") == {"error": "not found"}


def test_write_body_no_frontmatter_file(tmp_path):
    lib = _mk_lib(tmp_path)
    d = tmp_path / "notes"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "bare.md"
    p.write_text("just prose\n", encoding="utf-8")
    lib.write_body("bare.md", "replaced")
    assert p.read_text(encoding="utf-8") == "\nreplaced\n"


def test_update_escapes_embedded_quotes(tmp_path):
    """A value containing double quotes must survive an update round-trip.

    The old writer emitted f'{k}: "{v}"' without escaping — a JSON-encoded
    value (e.g. a checklist column) corrupted the YAML on the next read.
    """
    from emptyos.sdk.utils import parse_frontmatter

    lib = _mk_lib(tmp_path)
    p = _seed(tmp_path)
    payload = '[{"text": "Draft the spec: v1", "done": true}]'
    res = lib.update("demo.md", {"title": payload})
    assert res == {"ok": True, "file": "demo.md"}
    fm = parse_frontmatter(p.read_text(encoding="utf-8"))
    assert fm["title"] == payload  # exact round-trip, quotes intact
    # And a second update must not double-escape.
    lib.update("demo.md", {"status": "x"})
    fm2 = parse_frontmatter(p.read_text(encoding="utf-8"))
    assert fm2["title"] == payload
