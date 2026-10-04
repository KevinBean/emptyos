"""Regression tests for the reusable Blender × ComfyUI video QA script."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parent.parent
    / ".agents"
    / "skills"
    / "tool-blender-comfyui-video"
    / "scripts"
    / "check_hybrid_video.py"
)


def _qa_module():
    spec = importlib.util.spec_from_file_location("_hybrid_qa_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_split_line_freeze_events_do_not_expand_to_the_whole_video():
    qa = _qa_module()
    log = "\n".join([
        "lavfi.freezedetect.freeze_start: 0",
        "lavfi.freezedetect.freeze_duration: 1.083333",
        "lavfi.freezedetect.freeze_end: 1.083333",
        "lavfi.freezedetect.freeze_start: 10.416667",
    ])

    assert qa.parse_freeze_events(log, 11.541667, 0.5) == [
        {
            "start": 0.0,
            "end": 1.083333,
            "duration": 1.083333,
        },
        {
            "start": 10.416667,
            "end": 11.541667,
            "duration": 1.125,
        },
    ]
