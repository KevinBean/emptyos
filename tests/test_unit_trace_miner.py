"""Unit tests for trace-miner's pure signature/classify/score helpers.

Pure in-process — no daemon, no kernel. Imports the app module directly by
path (apps/ aren't an installable package), exercising the module-level
functions that do the actual mining logic.
"""

from __future__ import annotations

import importlib.util

import pytest

from helpers import app_path

# trace-miner moved under extension/dev/ in the 2026-05-30 reorg — app_path
# resolves it wherever it lives rather than hardcoding apps/trace-miner (which
# breaks collection).
_APP = app_path("trace-miner") / "app.py"
_spec = importlib.util.spec_from_file_location("trace_miner_app", _APP)
tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tm)


class TestNormalizeSignature:
    def test_strips_numbers_and_quotes(self):
        a = tm.normalize_signature("video generate failed: 500, message='boom 42'")
        b = tm.normalize_signature("video generate failed: 503, message='boom 99'")
        assert a == b

    def test_collapses_event_names_to_root_cause(self):
        # Same root bug surfacing on different events must group together.
        a = tm.normalize_signature("Handler error for reader:highlight_added: Object of type Event is not JSON serializable")
        b = tm.normalize_signature("Handler error for dictionary:word_reviewed: Object of type Event is not JSON serializable")
        assert a == b
        assert tm.sig_hash(a) == tm.sig_hash(b)

    def test_distinct_errors_stay_distinct(self):
        a = tm.normalize_signature("'Event' object has no attribute 'get'")
        b = tm.normalize_signature("Object of type Event is not JSON serializable")
        assert tm.sig_hash(a) != tm.sig_hash(b)

    def test_strips_hex_and_addresses(self):
        a = tm.normalize_signature("crash at 0xdeadbeef ref a1b2c3d4e5f6")
        b = tm.normalize_signature("crash at 0xfeedface ref 0011223344ff")
        assert a == b

    def test_truncates_long_messages(self):
        assert len(tm.normalize_signature("x" * 500)) <= 160


class TestClassify:
    def test_comfyui_http_is_external(self):
        assert tm.classify("comfyui", "video generate failed: 500, message='...'") == "external"

    def test_connection_reset_is_external(self):
        assert tm.classify("agent", "ConnectionResetError: [WinError 10054] forcibly closed") == "external"

    def test_event_handler_error_is_code_bug(self):
        assert tm.classify("event_bus", "Handler error for people:assigned: 'Event' object has no attribute 'get'") == "code-bug"

    def test_json_serializable_is_code_bug(self):
        assert tm.classify("event_bus", "Object of type Event is not JSON serializable") == "code-bug"

    def test_unknown_when_no_signal(self):
        assert tm.classify("myapp", "something odd happened today") == "unknown"

    def test_cloud_http_message_external_even_unknown_source(self):
        assert tm.classify("agent", "openai-mini tool-call request failed (HTTP 400)") == "external"


class TestScore:
    def test_code_bug_outranks_unknown_same_volume(self):
        assert tm.score_issue(10, 2, "code-bug") > tm.score_issue(10, 2, "unknown")

    def test_external_is_floored(self):
        assert tm.score_issue(1000, 500, "external") <= 5

    def test_recency_boost(self):
        assert tm.score_issue(10, 5, "code-bug") > tm.score_issue(10, 0, "code-bug")


class TestAppAttribution:
    def test_handler_error_event_prefix(self):
        assert tm.app_for_message("Handler error for people:assigned: boom") == "people"

    def test_api_path(self):
        assert tm.app_for_message("GET /journal/api/today failed") == "journal"

    def test_empty_when_no_signal(self):
        assert tm.app_for_message("generic failure") == ""


class TestCoerceTs:
    def test_float_passthrough(self):
        assert tm._coerce_ts(1700000000.0) == 1700000000.0

    def test_numeric_string(self):
        assert tm._coerce_ts("1700000000") == 1700000000.0

    def test_iso_string(self):
        assert tm._coerce_ts("2026-05-22T00:00:00+00:00") == pytest.approx(1779408000, abs=86400)

    def test_garbage_falls_back_to_now(self):
        import time
        assert tm._coerce_ts("not-a-time") == pytest.approx(time.time(), abs=5)
