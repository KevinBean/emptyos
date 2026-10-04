"""Unit tests for the shared KB few-shot pattern injector (offline, no daemon)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from emptyos.sdk.pattern_examples import resolve_pattern_examples


class _StubApp:
    """Minimal BaseApp stand-in: a kb get_note responder + a dead vault_root."""

    def __init__(self, notes: dict):
        self._notes = notes  # {slug: (title, body)}
        self.vault_root = Path("/nonexistent-vault-root")

    async def call_app(self, app: str, method: str, **kw):
        if app == "kb" and method == "get_note":
            slug = kw.get("slug")
            if slug in self._notes:
                title, body = self._notes[slug]
                return {"body": body, "properties": {"title": title}}
            return {"error": "not found", "slug": slug}
        return None


_BRACKET = (
    "# Mounting bracket\n\nA plate with through-holes.\n\n"
    "```json\n{\"schema\": \"eos-cad/1\", \"name\": \"Bracket\"}\n```\n\n"
    "```python\nprint('not for cad')\n```\n"
)


def _run(coro):
    return asyncio.run(coro)


def test_resolves_and_filters_by_language():
    app = _StubApp({"eos-cad-bracket": ("Mounting bracket", _BRACKET)})
    out = _run(resolve_pattern_examples(app, ["eos-cad-bracket"], langs={"json"}, heading="Reference parts"))
    assert "# Reference parts" in out
    assert "## Example: Mounting bracket" in out
    assert "eos-cad/1" in out          # json block kept
    assert "not for cad" not in out    # python block filtered out


def test_default_langs_accept_all():
    app = _StubApp({"p": ("P", _BRACKET)})
    out = _run(resolve_pattern_examples(app, ["p"]))   # langs=None
    assert "eos-cad/1" in out
    assert "not for cad" in out


def test_empty_names_returns_blank():
    app = _StubApp({})
    assert _run(resolve_pattern_examples(app, [])) == ""
    assert _run(resolve_pattern_examples(app, [None, ""])) == ""


def test_unknown_note_returns_blank():
    app = _StubApp({})
    # Unknown slug → kb error + dead vault glob → nothing resolves → "".
    assert _run(resolve_pattern_examples(app, ["does-not-exist"], langs={"json"})) == ""


def test_note_without_matching_lang_is_skipped():
    app = _StubApp({"p": ("P", "# P\n\n```python\nx=1\n```\n")})
    # Only a python block, asking for json → no chunk → "".
    assert _run(resolve_pattern_examples(app, ["p"], langs={"json"})) == ""


def test_multiple_notes_and_custom_intro():
    app = _StubApp({
        "a": ("Alpha", "# Alpha\n```json\n{\"a\":1}\n```\n"),
        "b": ("Beta", "# Beta\n```json\n{\"b\":2}\n```\n"),
    })
    out = _run(resolve_pattern_examples(app, ["a", "b"], langs={"json"}, intro="CUSTOM-INTRO"))
    assert "CUSTOM-INTRO" in out
    assert "## Example: Alpha" in out and "## Example: Beta" in out
    assert out.index("Alpha") < out.index("Beta")   # order preserved
