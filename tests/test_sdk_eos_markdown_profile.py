"""Conformance fixture for the durable EOS vault Markdown contract.

This is deliberately small: narrow regression files own parser and renderer edge
cases. Here one representative note must survive the storage→query→render path.
"""

from __future__ import annotations

import pytest

from emptyos.runtime.vault_index import _parse_fm
from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.markdown_render import HAS_MARKDOWN, render_markdown


BODY = """# Cable bonding

See [[sheath-losses|sheath losses]] and [[private-note|local notes]].

> [!note] Design check
> Confirm the bonding arrangement.

| Item | State |
|---|---|
| sheath | checked |

![[bonding-layout.png]]
"""


def _note(nested: str) -> str:
    return f"""---
title: Cable bonding
tags:
  - kb
  - concept
created: 2026-07-15
related:
  - sheath-losses
viz_embeds: {nested}
---

{BODY}"""


def test_profile_frontmatter_round_trips_flat_lists_and_nested_payload():
    nested = BaseApp.vault_encode_json([{"ref": "diagram-1", "position": 2}])
    fm = _parse_fm(_note(nested))

    assert fm["title"] == "Cable bonding"
    assert fm["tags"] == ["kb", "concept"]
    assert fm["related"] == ["sheath-losses"]
    assert BaseApp.vault_decode_json(fm["viz_embeds"]) == [
        {"ref": "diagram-1", "position": 2}
    ]


@pytest.mark.skipif(not HAS_MARKDOWN, reason="python-markdown missing")
def test_profile_body_renders_shared_extensions_without_losing_plain_markdown():
    html, _toc = render_markdown(
        BODY,
        published_slugs={"sheath-losses": ("sheath-losses", "page")},
        assets_prefix="assets/",
    )

    assert 'href="sheath-losses.html"' in html
    assert '>sheath losses</a>' in html
    assert '<span class="wikilink-private">local notes</span>' in html
    assert 'class="callout callout-note"' in html
    assert "Confirm the bonding arrangement." in html
    assert "<table>" in html and "<td>checked</td>" in html
    assert 'src="assets/bonding-layout.png"' in html

