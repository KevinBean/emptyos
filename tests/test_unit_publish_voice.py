"""Unit tests for the publish app's per-site voice-guide injection.

No daemon — `_voice_block` / `api_ai_write` / `api_voice_status` are
module-level functions taking `self`, so a SimpleNamespace over a tmp_path
vault stands in for PublishApp. Covers:

- presence-gating: no `_voice.md` -> "" -> prompts byte-identical to the
  constants in prompts.py (the "ships dark by absence" contract)
- frontmatter stripping + verbatim body in the injected block
- the builder leak guard: `_voice.md` can never appear in scan() output,
  even with `publish: true` set
- adapt_linkedin dispatch + voice injection into the system prompt
- /api/voice-status shape
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from apps.publish import writer
from apps.publish.builder import SiteBuilder
from apps.publish.prompts import ADAPT_LINKEDIN_PROMPT, WRITER_SYSTEM

SOURCE = "30_Resources/Published"

VOICE_NOTE = """---
title: Voice guide
tags:
  - style-guide
publish: false
---

## Voice principles

- Open with a concrete first-person experiment.
- SENTINEL-VOICE-PHRASE for grep checks.
"""


async def _fs_read(path):
    """Stands in for BaseApp.read (the read capability's filesystem provider)."""
    return Path(path).read_text(encoding="utf-8")


def make_self(tmp_path, site_id="default"):
    return SimpleNamespace(
        _vault_dir=lambda: str(tmp_path),
        _source_folder=lambda: SOURCE,
        _active_site_id=lambda: site_id,
        read=_fs_read,
    )


def write_voice(tmp_path, content=VOICE_NOTE):
    src = tmp_path / SOURCE
    src.mkdir(parents=True, exist_ok=True)
    (src / "_voice.md").write_text(content, encoding="utf-8")
    return src


# ── _voice_block ─────────────────────────────────────────────────────


def test_no_note_returns_empty(tmp_path):
    assert asyncio.run(writer._voice_block(make_self(tmp_path))) == ""


def test_note_body_injected_frontmatter_stripped(tmp_path):
    write_voice(tmp_path)
    block = asyncio.run(writer._voice_block(make_self(tmp_path)))
    assert block.startswith("\n\n## Author voice guide")
    assert "SENTINEL-VOICE-PHRASE" in block
    assert "Open with a concrete first-person experiment." in block
    assert "title: Voice guide" not in block
    assert "publish: false" not in block


def test_frontmatter_only_note_returns_empty(tmp_path):
    write_voice(tmp_path, "---\ntitle: Voice guide\n---\n\n   \n")
    assert asyncio.run(writer._voice_block(make_self(tmp_path))) == ""


def test_missing_vault_or_source_returns_empty(tmp_path):
    fake = SimpleNamespace(_vault_dir=lambda: "", _source_folder=lambda: SOURCE, read=_fs_read)
    assert asyncio.run(writer._voice_block(fake)) == ""
    fake = SimpleNamespace(_vault_dir=lambda: str(tmp_path), _source_folder=lambda: "", read=_fs_read)
    assert asyncio.run(writer._voice_block(fake)) == ""


def test_read_capability_failure_fails_soft(tmp_path):
    write_voice(tmp_path)
    fake = make_self(tmp_path)

    async def boom(path):
        raise RuntimeError("provider down")

    fake.read = boom
    assert asyncio.run(writer._voice_block(fake)) == ""


# ── builder leak guard ───────────────────────────────────────────────


def test_voice_note_never_scanned_even_with_publish_true(tmp_path):
    src = write_voice(
        tmp_path,
        "---\npublish: true\ntitle: Malicious voice\n---\n\nSENTINEL-VOICE-PHRASE\n",
    )
    (src / "real-post.md").write_text(
        "---\ntitle: Real post\npublish: true\n---\n\nHello.\n", encoding="utf-8"
    )
    builder = SiteBuilder(str(tmp_path), SOURCE, str(tmp_path / "out"), {})
    items = builder.scan(include_drafts=True)
    names = [Path(i["path"]).name for i in items]
    assert names == ["real-post.md"]


# ── api_ai_write dispatch + injection ────────────────────────────────


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def make_think_self(tmp_path, captured):
    base = make_self(tmp_path)

    async def think(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["system"] = kwargs.get("system", "")
        return "DRAFT-OUTPUT"

    base.think = think
    base.last_provenance = lambda: {"mode": "local"}
    base._voice_block = lambda: writer._voice_block(base)
    return base


def test_adapt_linkedin_dispatch_and_voice_injection(tmp_path):
    write_voice(tmp_path)
    captured = {}
    fake = make_think_self(tmp_path, captured)
    req = FakeRequest({"action": "adapt_linkedin", "text": "Post body", "parent": "post.md"})
    res = asyncio.run(writer.api_ai_write(fake, req))
    assert res["action"] == "adapt_linkedin"
    assert res["text"] == "DRAFT-OUTPUT"
    assert captured["system"].startswith(ADAPT_LINKEDIN_PROMPT)
    assert "SENTINEL-VOICE-PHRASE" in captured["system"]
    assert "post.md" in captured["prompt"]


def test_polish_system_byte_identical_without_note(tmp_path):
    captured = {}
    fake = make_think_self(tmp_path, captured)
    req = FakeRequest({"action": "polish", "text": "Some text"})
    res = asyncio.run(writer.api_ai_write(fake, req))
    assert res["action"] == "polish"
    assert captured["system"] == WRITER_SYSTEM  # byte-identical: the dark contract


def test_polish_gets_voice_translate_stays_neutral(tmp_path):
    write_voice(tmp_path)
    captured = {}
    fake = make_think_self(tmp_path, captured)
    asyncio.run(writer.api_ai_write(fake, FakeRequest({"action": "polish", "text": "T"})))
    assert "SENTINEL-VOICE-PHRASE" in captured["system"]
    asyncio.run(writer.api_ai_write(fake, FakeRequest({"action": "translate", "text": "T"})))
    assert captured["system"] == WRITER_SYSTEM


# ── /api/voice-status ────────────────────────────────────────────────


def test_voice_status_absent_and_present(tmp_path):
    fake = make_self(tmp_path, site_id="default")
    res = asyncio.run(writer.api_voice_status(fake, FakeRequest({})))
    assert res == {"exists": False, "path": f"{SOURCE}/_voice.md", "site": "default"}
    write_voice(tmp_path)
    res = asyncio.run(writer.api_voice_status(fake, FakeRequest({})))
    assert res["exists"] is True
    assert res["path"] == f"{SOURCE}/_voice.md"
