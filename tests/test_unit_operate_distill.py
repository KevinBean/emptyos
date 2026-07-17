"""Unit tests for the operate app's pure logic — no daemon, no real screen.

Covers the operation-manual note round-trip + validation (what the Phase-2
distiller emits and the executor reads), the demonstration event-log parser, and
the screen-capture plugin's pure parsers (foreground text, key naming, modifier
normalisation). These are the byte-level contracts the recorder/distiller depend
on; they must hold offline so a regression is caught in CI without a desktop.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(mod_name: str, rel: str):
    """Load a module file standalone (no package import, no daemon)."""
    spec = importlib.util.spec_from_file_location(mod_name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# emptyos.sdk must be importable for shared.py's own imports; it is (repo on path
# via conftest). shared.py imports only stdlib, so load it directly.
shared = _load("_operate_shared", "apps/public/labs/operate/shared.py")
capture_win = _load("_sc_capture_win", "plugins/screen-capture/capture_win.py")


# ── manual round-trip ──────────────────────────────────────────────────────────
DRAFT = {
    "name": "Run a steady-state case",
    "subject_app": "cymcap",
    "goal": "Solve a single steady-state cable rating case",
    "when_to_use": "when a new conductor size needs a rating",
    "inputs": [{"name": "conductor_mm2", "label": "Conductor size (mm²)", "required": True}],
    "preconditions": ["a project is open"],
    "steps": [
        {"n": 1, "action": "menu", "target": {"role": "menu", "name": "File > New Case"},
         "value": "", "verify": "the New Case dialog is open", "consequential": False},
        {"n": 2, "action": "type", "target": {"role": "field", "name": "Conductor size"},
         "value": "{input.conductor_mm2}", "verify": "the field shows the value",
         "consequential": False},
        {"n": 3, "action": "click", "target": {"role": "button", "name": "Solve"},
         "value": "", "verify": "the Results tab appears", "consequential": True},
    ],
    "verify": ["the Results grid shows an ampacity"],
    "outputs": [{"name": "ampacity_a", "where": "Results grid, Ampacity column"}],
}


class TestManualRoundTrip:
    def test_render_then_parse_preserves_spec(self):
        note = shared.render_manual_note(DRAFT, source_session="sess-1", created="2026-07-15")
        assert note.startswith("---")
        assert "tags:" in note and shared.MANUAL_TAG in note
        assert "```json" in note  # machine spec lives in a body fence
        # Simulate what VaultLibrary.detail() hands parse_manual: frontmatter
        # fields + the raw body.
        body = note.split("---", 2)[2]
        detail = {
            "id": "run-a-steady-state-case", "name": DRAFT["name"],
            "subject_app": "cymcap", "goal": DRAFT["goal"],
            "source_session": "sess-1", "author": "ai", "body": body,
        }
        parsed = shared.parse_manual(detail)
        assert parsed["name"] == DRAFT["name"]
        assert len(parsed["steps"]) == 3
        assert parsed["steps"][1]["value"] == "{input.conductor_mm2}"
        assert parsed["steps"][2]["consequential"] is True
        assert parsed["outputs"][0]["name"] == "ampacity_a"

    def test_author_is_ai(self):
        note = shared.render_manual_note(DRAFT, source_session="", created="2026-07-15")
        assert "author: ai" in note

    def test_apostrophe_survives_body_json(self):
        d = dict(DRAFT, name="Kevin's case", goal="don't lose the apostrophe")
        note = shared.render_manual_note(d, source_session="", created="2026-07-15")
        body = note.split("---", 2)[2]
        parsed = shared.parse_manual({"name": "Kevin's case", "body": body})
        # steps came through the json fence untouched
        assert parsed["steps"][0]["target"]["name"] == "File > New Case"


class TestManualValidation:
    def test_valid_draft(self):
        assert shared.validate_manual(DRAFT) == []

    def test_missing_name(self):
        assert "missing name" in shared.validate_manual(dict(DRAFT, name=""))

    def test_empty_steps(self):
        assert any("steps" in r for r in shared.validate_manual(dict(DRAFT, steps=[])))

    def test_unknown_action_flagged(self):
        bad = dict(DRAFT, steps=[{"n": 1, "action": "teleport"}])
        assert any("teleport" in r for r in shared.validate_manual(bad))

    def test_non_dict(self):
        assert shared.validate_manual("nope") == ["draft is not an object"]


# ── event log parsing ──────────────────────────────────────────────────────────
class TestEventParsing:
    def test_parse_events_skips_garbage(self):
        text = (
            '{"seq":1,"kind":"click","x":10,"y":20,"frame":"00001.png"}\n'
            "not json at all\n"
            '{"seq":2,"kind":"type","text":"hi","frame":"00002.png"}\n'
            "\n"
        )
        evs = shared.parse_events(text)
        assert len(evs) == 2
        assert evs[0]["kind"] == "click"
        assert evs[1]["text"] == "hi"

    def test_summarize(self):
        evs = [
            {"kind": "click", "ts": 0.5, "foreground": {"proc": "cymcap"}},
            {"kind": "type", "ts": 1.2, "foreground": {"proc": "cymcap"}},
            {"kind": "click", "ts": 2.0, "foreground": {"proc": "notepad"}},
        ]
        s = shared.summarize_events(evs)
        assert s["count"] == 3
        assert s["by_kind"]["click"] == 2
        assert s["apps"]["cymcap"] == 2
        assert s["first_ts"] == 0.5 and s["last_ts"] == 2.0


# ── screen-capture pure parsers ────────────────────────────────────────────────
class TestForegroundParser:
    def test_full(self):
        r = capture_win.parse_foreground("chrome\nDocs - Google Chrome\n0,0,1920,1080")
        assert r == {"proc": "chrome", "title": "Docs - Google Chrome", "rect": [0, 0, 1920, 1080]}

    def test_no_rect(self):
        r = capture_win.parse_foreground("notepad\nUntitled - Notepad")
        assert r["proc"] == "notepad" and r["rect"] is None

    def test_empty(self):
        r = capture_win.parse_foreground("")
        assert r == {"proc": "", "title": "", "rect": None}

    def test_bad_rect_is_none(self):
        r = capture_win.parse_foreground("app\ntitle\nnot,a,rect")
        assert r["rect"] is None


class TestRecordHelpers:
    def test_norm_mod_collapses_variants(self):
        record = _load("_sc_record", "plugins/screen-capture/record.py")
        assert record._norm_mod("ctrl_l") == "ctrl"
        assert record._norm_mod("alt_gr") == "alt"
        assert record._norm_mod("shift_r") == "shift"

    def test_key_name_special(self):
        record = sys.modules["_sc_record"]
        # A fake special key with .name and no .char
        fake = types.SimpleNamespace(name="enter")
        assert record._key_name(fake) == "enter"


class TestFrameSelection:
    def _events(self, n, framed=True, kinds=None):
        out = []
        for i in range(n):
            out.append({
                "seq": i + 1, "kind": (kinds[i] if kinds else "type"),
                "frame": f"{i:05d}.png" if framed else "",
            })
        return out

    def test_all_when_under_cap(self):
        evs = self._events(5)
        assert shared.select_frame_indices(evs, max_frames=12) == [0, 1, 2, 3, 4]

    def test_caps_and_keeps_first_last(self):
        evs = self._events(30)
        idxs = shared.select_frame_indices(evs, max_frames=8)
        assert len(idxs) <= 8
        assert 0 in idxs and 29 in idxs  # first + last framed always kept
        assert idxs == sorted(idxs)      # chronological

    def test_prefers_high_value_actions(self):
        kinds = ["type"] * 20
        kinds[5] = "click"; kinds[10] = "menu"; kinds[15] = "hotkey"
        evs = self._events(20, kinds=kinds)
        idxs = shared.select_frame_indices(evs, max_frames=6)
        # the three state-changing frames should be selected
        assert 5 in idxs and 10 in idxs and 15 in idxs

    def test_skips_frameless_events(self):
        evs = self._events(4, framed=False)
        assert shared.select_frame_indices(evs) == []


class TestActResolution:
    """The auto-run coordinate math — a bug here clicks the wrong place."""

    def test_bind_value(self):
        assert shared.bind_value("{input.note_text}", {"note_text": "hi"}) == "hi"
        assert shared.bind_value("prefix {input.x} suffix", {"x": "Y"}) == "prefix Y suffix"
        assert shared.bind_value("{input.missing}", {}) == "{input.missing}"  # left as-is
        assert shared.bind_value("no tokens", {}) == "no tokens"

    def test_click_uses_ref_rect_centre(self):
        snap = {"elements": [{"ref": "e5", "rect": [100, 200, 300, 240]}]}
        spec, why = shared.build_act_spec(
            {"action": "click"}, {"action": "click", "ref": "e5"}, snap, {})
        assert why is None
        assert spec == {"op": "click", "x": 200, "y": 220, "button": "left", "clicks": 1}

    def test_double_and_right_click(self):
        snap = {"elements": [{"ref": "e1", "rect": [0, 0, 10, 10]}]}
        s2, _ = shared.build_act_spec({"action": "double_click"}, {"ref": "e1"}, snap, {})
        assert s2["clicks"] == 2
        s3, _ = shared.build_act_spec({"action": "right_click"}, {"ref": "e1"}, snap, {})
        assert s3["button"] == "right"

    def test_click_coords_fallback(self):
        spec, why = shared.build_act_spec(
            {"action": "click"}, {"action": "click", "xy": [50, 60]}, {"elements": []}, {})
        assert why is None and spec["x"] == 50 and spec["y"] == 60

    def test_click_unlocatable_blocks(self):
        spec, why = shared.build_act_spec(
            {"action": "click"}, {"action": "click"}, {"elements": []}, {})
        assert spec is None and "locate" in why

    def test_type_binds_input(self):
        spec, why = shared.build_act_spec(
            {"action": "type", "value": "{input.note_text}"}, {}, {}, {"note_text": "hello"})
        assert spec == {"op": "type", "text": "hello"}

    def test_hotkey(self):
        spec, why = shared.build_act_spec(
            {"action": "hotkey", "value": "Enter"}, {}, {}, {})
        assert spec["op"] == "hotkey" and spec["combo"] == "Enter"


class TestCaseNote:
    def test_case_note_parts(self):
        fm, body = shared.case_note_parts(
            name="Rating case #1", subject_tool="cymcap",
            inputs={"conductor_mm2": "240"}, outputs={"ampacity_a": "612"},
            source_manual="run-a-case", created="2026-07-16")
        # frontmatter goes to vault_create_note (indexed KB case note)
        assert fm["tag"] == "kb" and fm["kind"] == "case" and fm["author"] == "ai"
        assert fm["subject_tool"] == "cymcap"
        assert "| conductor_mm2 | 240 |" in body
        assert "| ampacity_a | 612 |" in body
        assert "```json" in body  # machine spec for downstream conformance

    def test_null_output_renders_dash(self):
        _fm, body = shared.case_note_parts(
            name="c", subject_tool="t", inputs={"x": "1"}, outputs={"y": None},
            source_manual="m", created="2026-07-16")
        assert "| y | — |" in body


class TestSnapshotText:
    def test_empty(self):
        assert "vision-only" in shared.snapshot_text({"elements": []})
        assert "vision-only" in shared.snapshot_text({})

    def test_renders_elements(self):
        snap = {"available": True, "elements": [
            {"ref": "e1", "role": "Button", "name": "Solve", "value": "", "state": []},
            {"ref": "e2", "role": "Edit", "name": "Conductor", "value": "240", "state": ["disabled"]},
        ]}
        txt = shared.snapshot_text(snap)
        assert "e1 [Button] \"Solve\"" in txt
        assert "e2 [Edit/disabled] \"Conductor\" = \"240\"" in txt


class TestEventLine:
    def test_click(self):
        line = shared.event_line({"seq": 3, "ts": 1.2, "kind": "click",
                                  "button": "left", "x": 10, "y": 20,
                                  "foreground": {"title": "Untitled - Notepad"}})
        assert "#3" in line and "click" in line and "(10,20)" in line
        assert "Untitled - Notepad" in line

    def test_type(self):
        line = shared.event_line({"seq": 1, "kind": "type", "text": "hello"})
        assert "type" in line and "'hello'" in line

    def test_hotkey(self):
        line = shared.event_line({"seq": 2, "kind": "hotkey", "combo": "ctrl+s"})
        assert "ctrl+s" in line


class TestRecorderTrailingFlush:
    """The awaited stop() must land the trailing typed-run in the log before it
    returns — the count-undercount bug found in the first real recording."""

    def test_awaited_stop_flushes_typed_buffer(self, tmp_path):
        import asyncio

        record = _load("_sc_record2", "plugins/screen-capture/record.py")

        async def stub_grab(dest):
            Path(dest).write_bytes(b"\x89PNG\r\n")  # a non-empty "frame"
            return True

        async def stub_fg():
            return {"proc": "notepad", "title": "Untitled - Notepad", "rect": None}

        async def run():
            loop = asyncio.get_running_loop()
            rec = record.InputRecorder(loop, stub_grab, stub_fg)
            # Wire up an active session by hand (no real pynput listeners).
            rec._active = True
            rec._dir = tmp_path
            rec._frames_dir = tmp_path / "frames"
            rec._frames_dir.mkdir()
            rec._events_path = tmp_path / "events.jsonl"
            rec._events_path.write_text("", encoding="utf-8")
            rec._started_at = 0.0
            rec._type_buffer = list("hello")  # a pending typed run
            rec._kb_listener = None
            rec._ms_listener = None
            status = await rec.stop()
            return status

        status = asyncio.run(run())
        # The trailing "type" event + its frame are in the log/dir, and counted.
        events_text = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip()
        assert '"kind": "type"' in events_text
        assert '"hello"' in events_text
        assert status["events"] == 1 and status["frames"] == 1
        assert status["active"] is False
