"""Unit tests for scripts/youtube_push_articles.py metadata helpers — pure, no daemon.

Covers the post-frontmatter parsing that feeds YouTube titles/descriptions/tags:
the clean-YAML path, the mangled-YAML fallback (an image_prompt field with
escaped quotes that makes yaml.safe_load throw — the real bug that dropped a
post's whole frontmatter), the no-frontmatter case, the body first-paragraph
fallback, and description/tag assembly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "youtube_push", REPO / "scripts" / "youtube_push_articles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


push = _load()


CLEAN = """---
title: "A Clean Title"
date: 2026-06-27
tags:
  - ai
  - llm
summary: The one-line summary.
---

First real paragraph of the body.

Second paragraph.
"""

# image_prompt carries escaped quotes that make yaml.safe_load raise — the whole
# block would be dropped without the targeted line-extraction fallback.
MANGLED = r"""---
title: "Mangled But Recoverable"
tags:
  - AI
  - EmptyOS
summary: "Survives despite the broken field below."
image_prompt: "a photo with \\\\\"quotes\\\\\" inside that break yaml"
cover: media/x.png
---

Body paragraph here.
"""

NO_FM = "Just a body, no frontmatter at all.\n"

SUMMARYLESS = """---
title: "No Summary Here"
tags:
  - x
---

The body paragraph that should become the description fallback.
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_clean_frontmatter(tmp_path):
    fm = push._frontmatter(_write(tmp_path, "clean.md", CLEAN))
    assert fm["title"] == "A Clean Title"
    assert fm["summary"] == "The one-line summary."
    assert fm["tags"] == ["ai", "llm"]
    assert fm["_body"] == "First real paragraph of the body."


def test_mangled_yaml_falls_back(tmp_path):
    # yaml.safe_load throws on the escaped-quote image_prompt; targeted extraction
    # must still recover title/summary/tags rather than dropping everything.
    fm = push._frontmatter(_write(tmp_path, "mangled.md", MANGLED))
    assert fm["title"] == "Mangled But Recoverable"
    assert fm["summary"] == "Survives despite the broken field below."
    assert fm["tags"] == ["AI", "EmptyOS"]


def test_no_frontmatter_returns_empty(tmp_path):
    assert push._frontmatter(_write(tmp_path, "plain.md", NO_FM)) == {}


def test_body_fallback_for_summaryless(tmp_path):
    fm = push._frontmatter(_write(tmp_path, "nosum.md", SUMMARYLESS))
    assert "summary" not in fm
    desc = push._build_description(fm, "nosum", "")
    assert desc.startswith("The body paragraph that should become")


def test_normalize_tags():
    assert push._normalize_tags({"tags": ["a", " b ", ""]}) == ["a", "b"]
    assert push._normalize_tags({"tags": "notalist"}) == []
    assert push._normalize_tags({}) == []


def test_build_description_summary_link_and_hashtags():
    fm = {"summary": "The hook.", "tags": ["AI", "multi-agent"]}
    desc = push._build_description(fm, "my-slug", "https://eos.binbian.net")
    assert desc.startswith("The hook.")
    assert "https://eos.binbian.net/my-slug" in desc
    # hashtags strip non-alphanumerics (multi-agent -> multiagent)
    assert "#AI" in desc and "#multiagent" in desc


def test_build_description_prefers_summary_over_body():
    fm = {"summary": "Real summary.", "_body": "Body fallback.", "tags": []}
    assert push._build_description(fm, "s", "").splitlines()[0] == "Real summary."
