import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1] / "scripts" / "audit_conversation_archive.py"
)
SPEC = importlib.util.spec_from_file_location("audit_conversation_archive", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ArchiveAuditTests(unittest.TestCase):
    def test_complete_source_contract(self):
        body = (
            "# Test\n\n## Messages\n\n"
            "### 001 · User · 2026-07-23\n\nHi\n\n"
            "### 002 · Claude · 2026-07-23\n\nHello\n"
        )
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        note = f"""---
record_kind: conversation-source
provider: claude
source_conversation_id: 12345678-1234-1234-1234-123456789012
source_url: https://claude.ai/chat/12345678-1234-1234-1234-123456789012
capture_fidelity: verbatim-text
raw_status: complete
message_count: 2
content_sha256: {digest}
---

{body}"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "originals" / "claude" / "test.md"
            path.parent.mkdir(parents=True)
            path.write_text(note, encoding="utf-8")
            report = MODULE.audit(root)
        self.assertEqual(report["counts"]["complete_sources"], 1)
        self.assertEqual(report["entries"][0]["hash_status"], "verified")

    def test_digest_is_not_promoted_to_source(self):
        note = """---
source: claude.ai
title: Old summary
---
# Old summary

## Digest

A useful summary.
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "old.md").write_text(note, encoding="utf-8")
            report = MODULE.audit(root)
        self.assertEqual(report["entries"][0]["record_kind"], "digest")
        self.assertEqual(report["entries"][0]["archive_status"], "digest-only")

    def test_integrity_verified_partial_source_is_not_a_contract_failure(self):
        body = (
            "# Test\n\n## Messages\n\n"
            "### 001 - User - 2026-07-23\n\nSee the unavailable file.\n"
        )
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        note = f"""---
record_kind: conversation-source
provider: claude
source_conversation_id: 12345678-1234-1234-1234-123456789012
source_url: https://claude.ai/chat/12345678-1234-1234-1234-123456789012
capture_fidelity: partial
raw_status: partial
message_count: 1
content_sha256: {digest}
---

{body}"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "originals" / "claude" / "partial.md"
            path.parent.mkdir(parents=True)
            path.write_text(note, encoding="utf-8")
            report = MODULE.audit(root)
        self.assertEqual(report["entries"][0]["archive_status"], "partial")
        self.assertEqual(report["counts"]["partial_sources"], 1)

    def test_legacy_turns_remain_unverified(self):
        note = """---
source: chatgpt
---
### User

One

### ChatGPT

Two

### User

Three

### ChatGPT

Four
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "legacy.md").write_text(note, encoding="utf-8")
            report = MODULE.audit(root)
        self.assertEqual(report["entries"][0]["record_kind"], "legacy-transcript")
        self.assertEqual(report["entries"][0]["archive_status"], "legacy-unverified")


if __name__ == "__main__":
    unittest.main()
