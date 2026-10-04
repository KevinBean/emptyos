"""Unit tests for the vault_interest_profile primitive's pure parts.

The full method needs a live vault + index (exercised via daily-brief
/api/personalize and the hub next-move path); here we pin the pure helpers.
"""

from __future__ import annotations

from emptyos.sdk.base_app import INTEREST_SKIP_TAGS, _interest_folder_key


def test_folder_key_strips_para_prefix():
    assert _interest_folder_key("10_Projects") == "projects"
    assert _interest_folder_key("20_Areas") == "areas"
    assert _interest_folder_key("30_Resources") == "resources"


def test_folder_key_handles_paths_and_plain():
    assert _interest_folder_key("a/b/10_Projects") == "projects"
    assert _interest_folder_key("Notes") == "notes"
    assert _interest_folder_key("") == ""


def test_skip_tags_drop_structural_keep_interest():
    # structural tags are skipped...
    for t in ("daily", "kb", "project", "task", "web-clip"):
        assert t in INTEREST_SKIP_TAGS
    # ...real interest tags are not
    for t in ("cable", "dharma-log", "bess", "energy"):
        assert t not in INTEREST_SKIP_TAGS
