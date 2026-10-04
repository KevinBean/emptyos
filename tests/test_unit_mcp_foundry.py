"""Outbound MCP foundry — offline gate + resource-path logic.

Pure-python (``test_unit_`` prefix → runs offline per conftest). Drives
``mcp_outbound_server``'s gate flow against a temp autopilot store with the
flag, advertised-verb set, and daemon dispatch monkeypatched. No daemon needed.

Covers the five gate steps (flag → allowlist → eligibility hard-floor → grant
match → dispatch), the closed resource-path allowlist, the KB
kinds-allowlist gate (resources + prompts primitives), and the grant SDK
round-trip the CLI + agent panel rely on.
"""

import pathlib
import tempfile

import pytest

from emptyos import mcp_outbound_server as mcp
from emptyos.sdk import autopilot


@pytest.fixture
def store(monkeypatch):
    d = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(mcp, "_data_dir_cache", None)
    monkeypatch.setattr(mcp, "_resolve_data_dir", lambda: d)
    monkeypatch.setattr(mcp, "MCP_CLIENT_ID", "codex")
    autopilot.load_policy(d)  # seed policy.json with defaults (incl. task.add)
    return d


def _call(verb, args=None):
    return mcp._handle_call({"name": verb, "arguments": args or {}})


def _text(result):
    return result["content"][0]["text"]


class TestGate:
    def test_flag_off_denies_all(self, store, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: False)
        monkeypatch.setattr(mcp, "_foundry_verbs", lambda: ["task.add"])
        assert "disabled" in _text(_call("task.add")).lower()

    def test_not_advertised_denied(self, store, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_foundry_verbs", lambda: ["task.add"])
        assert "not in foundry allowlist" in _text(_call("kb.tag"))

    def test_non_eligible_refused_even_if_advertised_and_granted(self, store, monkeypatch):
        # Force a non-eligible verb into the advertised set AND write a raw
        # grant covering it (bypassing save_grant's own floor). The is_eligible
        # HARD FLOOR must still refuse dispatch.
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_foundry_verbs", lambda: ["rooms.write_note"])
        from emptyos.sdk.autopilot import _store_root, _write_json, GRANTS_FILE
        _write_json(_store_root(store) / GRANTS_FILE, [{
            "id": "grant-raw", "actor": {"type": "mcp-client", "id": "codex"},
            "verb_pattern": "rooms.write_note", "scope": "mcp:codex",
            "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None,
        }])
        assert "not autopilot-eligible" in _text(_call("rooms.write_note"))

    def test_eligible_no_grant_denied(self, store, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_foundry_verbs", lambda: ["task.add"])
        assert "no autopilot grant" in _text(_call("task.add"))

    def test_eligible_with_grant_dispatches_and_audits(self, store, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_foundry_verbs", lambda: ["task.add"])
        autopilot.save_grant(store, actor_type="mcp-client", actor_id="codex",
                             verb_pattern="task.add", scope="mcp:codex")
        seen = {}

        def fake_dispatch(app_id, method, arguments):
            seen.update(app_id=app_id, method=method, arguments=arguments)
            return True, "task created"

        monkeypatch.setattr(mcp, "_dispatch_to_daemon", fake_dispatch)
        assert _text(_call("task.add", {"text": "hi"})) == "task created"
        assert seen == {"app_id": "task", "method": "add", "arguments": {"text": "hi"}}
        v = autopilot.verify_audit(store)
        assert v["ok"] and v["lines_checked"] >= 1


class TestResourcePath:
    def test_static(self):
        assert mcp._resolve_resource_path("eos://apps") == "/api/apps"
        assert mcp._resolve_resource_path("eos://topology") == "/api/topology"
        assert mcp._resolve_resource_path("eos://scheduler/jobs") == "/api/scheduler/jobs"

    def test_app_template(self):
        assert mcp._resolve_resource_path("eos://app/task") == "/api/apps/task"

    def test_vault_query_template(self):
        p = mcp._resolve_resource_path("eos://vault/query?tags=kb&folder=x")
        assert p.startswith("/api/vault/query?")
        assert "tags=kb" in p and "folder=x" in p

    def test_unknown_traversal_and_bodies_unmapped(self):
        assert mcp._resolve_resource_path("eos://nope") is None
        assert mcp._resolve_resource_path("eos://app/../secret") is None
        assert mcp._resolve_resource_path("eos://app/a/b") is None
        # /api/vault/read (full bodies) is deliberately never mapped.
        assert mcp._resolve_resource_path("eos://vault/read?path=x") is None

    def test_read_resource_flag_off(self, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: False)
        r = mcp._read_resource({"uri": "eos://apps"})
        assert "disabled" in r["contents"][0]["text"].lower()


class _FakeResponse:
    """Stand-in for the urlopen context manager _daemon_get returns."""

    def __init__(self, payload):
        import json
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_daemon_get(payload):
    return lambda path, timeout=5: _FakeResponse(payload)


PATTERN_NOTE = {
    "slug": "pattern-three-js-scene",
    "name": "Pattern — Three.js scene",
    "path": "30_Resources/KB/patterns/pattern-three-js-scene.md",
    "properties": {"kind": "pattern", "title": "Three.js scene anatomy"},
    "body": "## Anatomy\n\n```js\nconst scene = new THREE.Scene();\n```\n",
    "backlinks": [{"slug": "x"}],
    "outgoing": [{"slug": "y"}],
    "clauses": [],
    "implemented_in_status": [],
}

CLAUSE_NOTE = {
    "slug": "iec-60287-1-1-clause-2-1",
    "name": "IEC 60287-1-1 §2.1",
    "path": "30_Resources/KB/sources/iec-60287-1-1-clause-2-1.md",
    "properties": {"kind": "clause", "standard": "IEC 60287-1-1"},
    "body": "verbatim standard text",
}


class TestKbResources:
    """KB note exposure — fail-closed kinds allowlist (sibling of the
    vault_tags_allow gate pinned by test_unit_vault_query_gate.py)."""

    def test_fail_closed_empty_allowlist(self, monkeypatch):
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: [])
        assert mcp._resolve_resource_path("eos://kb/note/some-note") is None
        assert mcp._resolve_resource_path("eos://kb/notes?kind=pattern") is None
        # Templates hidden from resources/list while the allowlist is empty.
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_daemon_reachable", lambda: True)
        listed = mcp._list_resources()
        assert not any("kb" in t["uriTemplate"] for t in listed["resourceTemplates"])

    def test_resolution_and_advertisement_with_allowlist(self, monkeypatch):
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: ["pattern", "concept"])
        assert mcp._resolve_resource_path("eos://kb/note/x") == "/kb/api/notes/x"
        p = mcp._resolve_resource_path("eos://kb/notes?kind=pattern&domain=viz")
        assert p.startswith("/kb/api/notes?")
        assert "kind=pattern" in p and "domain=viz" in p
        # kind-less list and non-allowlisted kind both refuse.
        assert mcp._resolve_resource_path("eos://kb/notes") is None
        assert mcp._resolve_resource_path("eos://kb/notes?kind=clause") is None
        # Slug hygiene: nested paths and traversal refuse.
        assert mcp._resolve_resource_path("eos://kb/note/a/b") is None
        assert mcp._resolve_resource_path("eos://kb/note/../secret") is None
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_daemon_reachable", lambda: True)
        listed = mcp._list_resources()
        assert any(t["uriTemplate"].startswith("eos://kb/note") for t in listed["resourceTemplates"])

    def test_read_allowed_kind_returns_shrunk_body(self, monkeypatch):
        import json
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: ["pattern"])
        monkeypatch.setattr(mcp, "_daemon_get", _fake_daemon_get(PATTERN_NOTE))
        r = mcp._read_resource({"uri": f"eos://kb/note/{PATTERN_NOTE['slug']}"})
        c = r["contents"][0]
        assert c["mimeType"] == "application/json"
        data = json.loads(c["text"])
        assert data["body"] == PATTERN_NOTE["body"]
        # Heavy graph fields stripped.
        assert "backlinks" not in data and "outgoing" not in data

    def test_read_disallowed_kind_refused_post_fetch(self, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: ["pattern"])
        monkeypatch.setattr(mcp, "_daemon_get", _fake_daemon_get(CLAUSE_NOTE))
        r = mcp._read_resource({"uri": f"eos://kb/note/{CLAUSE_NOTE['slug']}"})
        text = r["contents"][0]["text"]
        assert text.startswith("error:") and "clause" in text


class TestPrompts:
    """MCP prompts primitive — KB pattern notes, gated on "pattern" being
    in kb_kinds_allow."""

    def test_initialize_advertises_prompts(self):
        r = mcp._handle({"method": "initialize"})
        assert "prompts" in r["capabilities"]

    def test_list_fail_closed(self, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_daemon_reachable", lambda: True)
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: ["concept"])  # no pattern
        assert mcp._list_prompts() == {"prompts": []}
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: ["pattern"])
        monkeypatch.setattr(mcp, "_flag_on", lambda: False)
        assert mcp._list_prompts() == {"prompts": []}

    def test_list_maps_pattern_notes(self, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_daemon_reachable", lambda: True)
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: ["pattern"])
        listing = {"notes": [{"slug": "pattern-a", "title": "Pattern A", "kind": "pattern"},
                             {"slug": "pattern-b", "name": "Pattern B", "kind": "pattern"}],
                   "count": 2}
        monkeypatch.setattr(mcp, "_daemon_get", _fake_daemon_get(listing))
        r = mcp._list_prompts()
        assert [p["name"] for p in r["prompts"]] == ["pattern-a", "pattern-b"]
        assert r["prompts"][0]["description"] == "Pattern A"

    def test_get_prompt_shape(self, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: ["pattern"])
        monkeypatch.setattr(mcp, "_daemon_get", _fake_daemon_get(PATTERN_NOTE))
        r = mcp._get_prompt({"name": PATTERN_NOTE["slug"]})
        assert r["description"] == "Three.js scene anatomy"
        msg = r["messages"][0]
        assert msg["role"] == "user"
        assert msg["content"] == {"type": "text", "text": PATTERN_NOTE["body"]}

    def test_get_non_pattern_refused(self, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: ["pattern"])
        monkeypatch.setattr(mcp, "_daemon_get", _fake_daemon_get(CLAUSE_NOTE))
        r = mcp._get_prompt({"name": CLAUSE_NOTE["slug"]})
        assert r["error"]["code"] == -32602

    def test_get_unknown_or_gated_refused(self, monkeypatch):
        monkeypatch.setattr(mcp, "_flag_on", lambda: True)
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: ["pattern"])
        monkeypatch.setattr(mcp, "_daemon_get",
                            _fake_daemon_get({"error": "not found", "slug": "nope"}))
        assert mcp._get_prompt({"name": "nope"})["error"]["code"] == -32602
        # Name hygiene refused before any fetch.
        assert mcp._get_prompt({"name": "../x"})["error"]["code"] == -32602
        # "pattern" missing from the allowlist gates the whole primitive.
        monkeypatch.setattr(mcp, "_kb_kinds_allow", lambda: [])
        assert mcp._get_prompt({"name": "pattern-a"})["error"]["code"] == -32602


class TestGrantSDK:
    def test_round_trip(self):
        d = pathlib.Path(tempfile.mkdtemp())
        g = autopilot.save_grant(d, actor_type="mcp-client", actor_id="codex",
                                 verb_pattern="task.add", scope="mcp:codex")
        assert autopilot.match(d, actor_type="mcp-client", actor_id="codex",
                               verb="task.add", scope_candidates=["mcp:codex", "global"])
        assert autopilot.revoke_grant(d, g["id"])
        assert autopilot.match(d, actor_type="mcp-client", actor_id="codex",
                               verb="task.add", scope_candidates=["mcp:codex", "global"]) is None

    def test_non_eligible_refused(self):
        d = pathlib.Path(tempfile.mkdtemp())
        with pytest.raises(ValueError):
            autopilot.save_grant(d, actor_type="mcp-client", actor_id="codex",
                                 verb_pattern="rooms.write_note", scope="mcp:codex")
