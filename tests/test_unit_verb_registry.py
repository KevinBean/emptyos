"""Unit tests for the verb registry primitive (emptyos/sdk/verb_registry.py).

Pure — no kernel, no daemon. Covers parse validation + VerbRegistry views.
Run: python -m pytest tests/test_unit_verb_registry.py -v
"""

from __future__ import annotations

import pytest

from emptyos.sdk.verb_registry import (
    VALID_ELIGIBILITY,
    VALID_SURFACES,
    VerbEntry,
    VerbRegistry,
    parse_verb_entry,
)


# ── parse_verb_entry — happy path ───────────────────────────────────────────

def test_parse_full_entry():
    raw = {
        "verb": "task.add",
        "method": "voice_add_task",
        "summary": "Capture a quick task",
        "args": {"text": "string", "due": "string?"},
        "eligibility": "stable",
        "surfaces": ["voice", "assistant", "mcp", "agent"],
        "voice": {"example": "add a task", "always": True, "card": "task-list",
                  "narrate": "narrate_after_add"},
        "assistant": {"slash": "/add", "arg": "text", "inverse": "reopen"},
    }
    entry, err = parse_verb_entry(raw, "task")
    assert err is None
    assert entry is not None
    assert entry.verb == "task.add"
    assert entry.app == "task"
    assert entry.name == "add"
    assert entry.method == "voice_add_task"
    assert entry.eligibility == "stable"
    assert entry.surfaces == ("voice", "assistant", "mcp", "agent")
    assert entry.voice["narrate"] == "narrate_after_add"
    assert entry.assistant["slash"] == "/add"


def test_parse_minimal_entry_defaults_to_gated():
    entry, err = parse_verb_entry(
        {"verb": "kb.tag", "method": "tag"}, "kb")
    assert err is None
    assert entry.eligibility == "gated"  # default
    assert entry.surfaces == ()
    assert entry.args == {}


def test_parse_hyphenated_app_id():
    # video-digest.queue — app-half may contain hyphens, method-half may not.
    entry, err = parse_verb_entry(
        {"verb": "video-digest.queue", "method": "voice_queue_video"},
        "video-digest")
    assert err is None
    assert entry.app == "video-digest"
    assert entry.name == "queue"


# ── parse_verb_entry — rejects ──────────────────────────────────────────────

@pytest.mark.parametrize("bad_verb", [
    "task",            # no dot
    "task.",           # empty method
    ".add",            # empty app
    "task.add.extra",  # method with a dot
    "Task.Add",        # uppercase
    "task add",        # space
    "video_digest.q",  # method-style underscore in app half + still ok? app allows no underscore
])
def test_parse_rejects_bad_verb_shape(bad_verb):
    entry, err = parse_verb_entry({"verb": bad_verb, "method": "m"}, bad_verb.split(".")[0])
    assert entry is None
    assert err is not None


def test_parse_rejects_cross_app_declaration():
    # verb app-half must equal the declaring app id.
    entry, err = parse_verb_entry(
        {"verb": "evil.delete", "method": "delete"}, "task")
    assert entry is None
    assert "app-half" in err


def test_parse_allows_alias_app_half():
    # aura.* declared by the voice-assistant app, with "aura" passed as an alias.
    entry, err = parse_verb_entry(
        {"verb": "aura.remember", "method": "voice_remember"},
        "voice-assistant",
        allowed_app_halves={"voice-assistant", "aura"})
    assert err is None
    assert entry.app == "aura"
    assert entry.method == "voice_remember"


def test_parse_rejects_non_alias_app_half():
    entry, err = parse_verb_entry(
        {"verb": "aura.remember", "method": "voice_remember"},
        "voice-assistant")  # no aura alias passed -> rejected
    assert entry is None
    assert "app-half" in err


def test_parse_rejects_missing_method():
    entry, err = parse_verb_entry({"verb": "task.add"}, "task")
    assert entry is None
    assert "method" in err


def test_parse_rejects_bad_eligibility():
    entry, err = parse_verb_entry(
        {"verb": "task.add", "method": "add", "eligibility": "always"}, "task")
    assert entry is None
    assert "eligibility" in err


def test_parse_rejects_unknown_surface():
    entry, err = parse_verb_entry(
        {"verb": "task.add", "method": "add", "surfaces": ["voice", "telepathy"]},
        "task")
    assert entry is None
    assert "surfaces" in err


def test_parse_rejects_assistant_arg_not_in_args():
    entry, err = parse_verb_entry(
        {"verb": "task.add", "method": "add",
         "args": {"text": "string"},
         "assistant": {"slash": "/add", "arg": "missing"}},
        "task")
    assert entry is None
    assert "assistant.arg" in err


def test_parse_accepts_assistant_arg_in_args():
    entry, err = parse_verb_entry(
        {"verb": "task.add", "method": "add",
         "args": {"text": "string"},
         "assistant": {"slash": "/add", "arg": "text"}},
        "task")
    assert err is None
    assert entry.assistant["arg"] == "text"


def test_parse_rejects_non_dict():
    entry, err = parse_verb_entry("not a table", "task")
    assert entry is None


# ── VerbRegistry views ──────────────────────────────────────────────────────

def _reg():
    raws = [
        ({"verb": "task.add", "method": "voice_add_task", "eligibility": "stable",
          "surfaces": ["voice", "assistant", "mcp", "agent"]}, "task"),
        ({"verb": "task.list_today", "method": "voice_list_today", "eligibility": "stable",
          "surfaces": ["voice", "agent"]}, "task"),
        ({"verb": "rooms.write_note", "method": "write_note", "eligibility": "never",
          "surfaces": ["agent"]}, "rooms"),
        ({"verb": "kb.tag", "method": "tag", "eligibility": "gated",
          "surfaces": ["mcp", "agent"]}, "kb"),
    ]
    entries = []
    for raw, app in raws:
        e, err = parse_verb_entry(raw, app)
        assert err is None, err
        entries.append(e)
    return VerbRegistry(entries)


def test_registry_len_and_get():
    reg = _reg()
    assert len(reg) == 4
    assert reg.get("task.add").method == "voice_add_task"
    assert reg.get("nope.verb") is None


def test_registry_apps():
    assert _reg().apps() == {"task", "rooms", "kb"}


def test_registry_for_surface():
    reg = _reg()
    voice = {e.verb for e in reg.for_surface("voice")}
    assert voice == {"task.add", "task.list_today"}
    mcp = {e.verb for e in reg.for_surface("mcp")}
    assert mcp == {"task.add", "kb.tag"}
    agent = {e.verb for e in reg.for_surface("agent")}
    assert agent == {"task.add", "task.list_today", "rooms.write_note", "kb.tag"}


def test_registry_apps_for_surface():
    reg = _reg()
    assert reg.apps_for_surface("voice") == {"task"}
    assert reg.apps_for_surface("mcp") == {"task", "kb"}
    assert reg.apps_for_surface("assistant") == {"task"}
    assert reg.apps_for_surface("agent") == {"task", "rooms", "kb"}
    assert reg.apps_for_surface("nope") == set()


def test_registry_eligible_verbs_only_stable():
    # never + gated excluded; only stable verbs feed the autopilot floor.
    assert _reg().eligible_verbs() == {"task.add", "task.list_today"}


def test_registry_eligible_excludes_never_even_if_other_surfaces():
    # rooms.write_note is on the agent surface but eligibility=never -> not a floor verb.
    assert "rooms.write_note" not in _reg().eligible_verbs()


def test_registry_menu_shape():
    rows = _reg().menu("mcp")
    verbs = {r["verb"] for r in rows}
    assert verbs == {"task.add", "kb.tag"}
    for r in rows:
        assert set(r) >= {"verb", "app", "method", "summary", "eligibility", "surfaces"}


def test_method_for_surface_override():
    # task.add: canonical 'add' for agent/mcp, 'voice_add_task' wrapper for voice.
    entry, err = parse_verb_entry(
        {"verb": "task.add", "method": "add",
         "surfaces": ["voice", "agent"],
         "voice": {"method": "voice_add_task", "narrate": "narrate_after_add"}},
        "task")
    assert err is None
    assert entry.method_for("agent") == "add"
    assert entry.method_for("mcp") == "add"
    assert entry.method_for("voice") == "voice_add_task"
    assert entry.method_for("assistant") == "add"  # no override -> canonical


def test_dispatch_methods_collects_all_distinct():
    entry, _ = parse_verb_entry(
        {"verb": "task.add", "method": "add",
         "voice": {"method": "voice_add_task", "narrate": "narrate_after_add"},
         "assistant": {"slash": "/add", "method": "add"}},  # dup of canonical
        "task")
    # add (canonical + assistant dup collapses), voice_add_task, narrate_after_add
    assert entry.dispatch_methods() == ["add", "voice_add_task", "narrate_after_add"]


def test_dispatch_methods_voice_only_verb():
    # list_today is voice-only: canonical method IS the voice handler.
    entry, _ = parse_verb_entry(
        {"verb": "task.list_today", "method": "voice_list_today",
         "surfaces": ["voice"]},
        "task")
    assert entry.dispatch_methods() == ["voice_list_today"]
    assert entry.method_for("voice") == "voice_list_today"


def test_enums_are_frozen():
    assert "voice" in VALID_SURFACES
    assert "stable" in VALID_ELIGIBILITY
    assert isinstance(VerbEntry(verb="a.b", app_id="a", method="b").surfaces, tuple)


class TestMergedVoiceEntries:
    """merged_voice_entries — the shared registry+legacy dual-read
    (consumers: voice-assistant intents, telegram bridge allowlist)."""

    def _loader(self, entries, legacy):
        from emptyos.sdk.verb_registry import VerbRegistry

        class _L:
            def get_verbs(self):
                return VerbRegistry(entries)

            def get_contributions(self, target, slot):
                assert (target, slot) == ("voice-assistant", "intent")
                return legacy

        return _L()

    def test_registry_normalized_and_legacy_merged(self):
        from emptyos.sdk.verb_registry import VerbEntry, merged_voice_entries

        entries = [
            VerbEntry(verb="task.add", app_id="task", method="add",
                      args={"text": "string"}, surfaces=("voice", "agent"),
                      voice={"method": "voice_add_task", "example": "add a task",
                             "always": True, "narrate": "narrate_after_add"}),
            VerbEntry(verb="commons.share", app_id="commons", method="share",
                      surfaces=("agent",)),  # not voice → absent
        ]
        legacy = [
            {"verb": "note.create", "method": "voice_create_note", "_app_id": "note"},
            {"verb": "task.legacy", "method": "old", "_app_id": "task"},  # migrated → skipped
        ]
        recs = merged_voice_entries(self._loader(entries, legacy))
        by_verb = {r["verb"]: r for r in recs}
        assert set(by_verb) == {"task.add", "note.create"}
        t = by_verb["task.add"]
        assert t["method"] == "voice_add_task"    # per-surface dispatch resolved
        assert t["always"] is True and t["narrate"] == "narrate_after_add"
        assert t["args"] == {"text": "string"}

    def test_duplicate_verb_keeps_first(self):
        from emptyos.sdk.verb_registry import merged_voice_entries

        legacy = [
            {"verb": "x.do", "method": "first", "_app_id": "a"},
            {"verb": "x.do", "method": "second", "_app_id": "b"},
        ]
        recs = merged_voice_entries(self._loader([], legacy))
        assert [r["method"] for r in recs] == ["first"]

    def test_each_half_fails_soft(self):
        from emptyos.sdk.verb_registry import merged_voice_entries

        class _RegistryBoom:
            def get_verbs(self):
                raise RuntimeError("boom")

            def get_contributions(self, target, slot):
                return [{"verb": "note.create", "method": "voice_create_note", "_app_id": "note"}]

        recs = merged_voice_entries(_RegistryBoom())
        assert [r["verb"] for r in recs] == ["note.create"]

        class _NoContrib:
            def get_verbs(self):
                from emptyos.sdk.verb_registry import VerbRegistry
                return VerbRegistry([])
            # no get_contributions attr at all

        assert merged_voice_entries(_NoContrib()) == []
