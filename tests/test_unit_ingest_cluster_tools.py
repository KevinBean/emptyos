"""Unit pins for the T2 cluster-ingest tools (scripts/ingest_*.py).

Promoted out of gitignored ``data/imports/t2-work/`` on 2026-08-07 after three
clusters (EMFieldCalc 34, cable-tool 46, simplecalc 121) ran on them. These
pins cover the two pure rules whose failure modes actually shipped bugs:

* ``tag_in_frontmatter`` — cluster membership must be structural (a tag line
  in the leading frontmatter block), never a substring over the whole digest.
  The substring form let a cable-tool digest that *mentions* simplecalc in
  prose join the simplecalc cluster.
* ``derive_new_path`` — the writer's ``--<sid>.md`` suffix rule, which had
  been re-implemented in seven inline heredocs before extraction. Must be
  idempotent so pre-write and rebuilt manifests derive the same answer.

Daemon-free by design (fix-agent regression-gate compatible).
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Import a scripts/ingest_* module without touching the real vault.

    Both modules resolve the vault at import time; a stubbed ``vault_paths``
    keeps collection working on a clone with no ``emptyos.toml`` (CI runs
    ``--collect-only`` on every push).

    The stub is REMOVED again once the module under test has been executed.
    Leaving it in ``sys.modules`` leaked it into every test module collected
    after this one — it is a bare ``ModuleType`` with one attribute and no
    ``__file__``, so a later ``from vault_paths import vault_root`` failed with
    ``(unknown location)``. That took out ``test_unit_mv_library`` at
    COLLECTION, and one collection error aborts the whole run: both the Tests
    and Dogfood workflows were red from 2026-09-11 on. The modules loaded here
    keep their own reference in their globals, so restoring costs them nothing.
    """
    stub = types.ModuleType("vault_paths")
    stub.require_vault_root = lambda: Path(".")
    previous = sys.modules.get("vault_paths")
    sys.modules["vault_paths"] = stub
    try:
        spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("vault_paths", None)
        else:
            sys.modules["vault_paths"] = previous


bc = _load("ingest_build_cluster")
mr = _load("ingest_make_receipts")


DIGEST_WITH_TAG = """---
record_kind: conversation-digest
source_conversation_id: "0123abcd-0000-0000-0000-000000000000"
tags:
  - conversation
  - simplecalc
  - software-dev
---
# Title

## Digest

Body text here.
"""

DIGEST_PROSE_MENTION = """---
record_kind: conversation-digest
source_conversation_id: "4567abcd-0000-0000-0000-000000000000"
tags:
  - conversation
  - cable-tool
---
# Title

## Digest

Within days the simplecalc thread becomes the main line; a bare mention like
- simplecalc
in body prose must not create membership either.
"""


class TestTagInFrontmatter:
    def test_frontmatter_tag_joins(self):
        assert bc.tag_in_frontmatter(DIGEST_WITH_TAG, "simplecalc")

    def test_prose_mention_does_not_join(self):
        """The 2026-08-07 bug: substring-over-text made prose mentions members."""
        assert not bc.tag_in_frontmatter(DIGEST_PROSE_MENTION, "simplecalc")

    def test_other_tag_in_same_block_does_not_join(self):
        assert not bc.tag_in_frontmatter(DIGEST_WITH_TAG, "cable-tool")

    def test_tag_with_regex_chars_is_escaped(self):
        text = DIGEST_WITH_TAG.replace("- simplecalc", "- c++.notes")
        assert bc.tag_in_frontmatter(text, "c++.notes")
        assert not bc.tag_in_frontmatter(DIGEST_WITH_TAG, "c++.notes")

    def test_crlf_frontmatter_still_matches(self):
        assert bc.tag_in_frontmatter(DIGEST_WITH_TAG.replace("\n", "\r\n"), "simplecalc")


class TestDeriveNewPath:
    OLD = "30_Resources/conversations/2025-05-14-some-title.md"
    NEW = "30_Resources/conversations/2025-05-14-some-title--abcd1234.md"

    def test_unsuffixed_gains_suffix(self):
        assert mr.derive_new_path(self.OLD, "abcd1234") == self.NEW

    def test_already_suffixed_is_idempotent(self):
        """A rebuilt manifest names the new digest; deriving again must not double-suffix."""
        assert mr.derive_new_path(self.NEW, "abcd1234") == self.NEW


class TestDigestChars:
    def test_counts_digest_section_only(self):
        assert bc.digest_chars(DIGEST_WITH_TAG) == len("Body text here.")

    def test_no_digest_section_is_zero(self):
        assert bc.digest_chars("---\ntags: []\n---\n# T\n\nNo digest heading.") == 0
