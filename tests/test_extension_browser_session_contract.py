from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[1] / "tools" / "chrome-extension"


def test_manifest_has_public_store_permission_floor():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert int(manifest["minimum_chrome_version"]) >= 116
    assert manifest["host_permissions"] == ["http://localhost:9000/*", "http://127.0.0.1:9000/*"]
    assert set(manifest["optional_host_permissions"]) == {"http://*/*", "https://*/*"}
    assert "content_scripts" not in manifest
    forbidden = {"debugger", "nativeMessaging", "history", "bookmarks", "downloads"}
    assert forbidden.isdisjoint(manifest["permissions"])


def test_token_is_never_written_to_sync_storage():
    sources = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ["daemon-client.js", "options.js", "background.js", "sidepanel.js"])
    assert "chrome.storage.sync.set({ host: normalized, name:" in sources
    assert "chrome.storage.sync.set({ host, token" not in sources
    assert 'chrome.storage.sync.remove("token")' in sources


def test_page_bridge_has_bounds_and_no_arbitrary_eval_command():
    bridge = (ROOT / "page-bridge.js").read_text(encoding="utf-8")
    assert ".slice(0, 12000)" in bridge
    assert ".slice(0, 250)" in bridge
    assert "blocked_sensitive_field" in bridge
    assert "attachShadow({ mode: \"closed\" })" in bridge
    assert 'action === "eval"' not in bridge


def test_reading_policy_loads_before_the_script_that_reads_it():
    """reading-assist.js reads globalThis.EOS_READING_POLICY at its top.

    If the policy is not registered before it — in BOTH the dynamic registration and
    the executeScript fallback for already-open tabs — the reading layer goes dormant
    with no error at all. Exactly the kind of ordering a grep test is good for.
    """
    source = (ROOT / "browser-session.js").read_text(encoding="utf-8")
    ordered = '"reading-config.js", "reading-policy.js", "reading-assist.js"'
    assert source.count(ordered) == 2, "policy must precede assist in BOTH script lists"
    assert '"reading-config.js", "reading-assist.js"' not in source


def test_chat_socket_recovers_instead_of_going_red():
    """A daemon restart drops the chat socket. The panel used to answer that with a
    sticky red "WebSocket error" that never cleared and never retried — the chat
    looked broken until the panel was reloaded, when nothing was broken at all.

    A WebSocket `error` event carries NO detail by design, so "WebSocket error" is
    not a cause, it is a restatement of the question. The panel must ask the daemon
    whether it is there, say which of the two problems it actually is, and reconnect.
    """
    panel = (ROOT / "sidepanel.js").read_text(encoding="utf-8")
    assert 'setStatus("WebSocket error", "err")' not in panel, "a cause, not a restatement"
    assert "scheduleWSReconnect" in panel and "wsReconnectDelay" in panel
    assert "EmptyOS is not reachable" in panel and "Chat connection lost" in panel
    # Our own close must not be mistaken for a drop — and the mark has to live on the
    # socket, because onclose fires on a later task than the close() that caused it.
    assert "ws._eosDeliberate" in panel


def test_the_chat_says_what_is_wrong_and_opens_the_place_to_fix_it():
    """Four causes, four different actions from the reader — and they used to look
    the same, or say nothing at all.

    * no token / rejected token -> the fix is one field away, so SAY so and open it.
      The boot path was `if (r.ok) {...}` with no else, so a rejected token produced
      no session, no socket, and no message: a silently empty panel.
    * session gone -> the daemon's sessions live in data/, and anything that resets
      them leaves the panel holding an id that will never exist again. The server
      accepts the socket, says "Session not found" and closes CLEANLY, so it is
      indistinguishable from a dropped connection — and reconnecting is a loop that
      can only fail. Start a new chat instead.
    * genuinely dropped -> reconnect.

    /api/health cannot tell these apart: it is auth-exempt, so it answers 200 to a
    client with no token at all. The probe must be an AUTHENTICATED endpoint.
    """
    panel = (ROOT / "sidepanel.js").read_text(encoding="utf-8")
    assert "diagnoseChat" in panel
    assert '"/api/health"' not in panel.split("diagnoseChat")[1].split("}")[0]
    assert "/assistant/api/sessions" in panel
    for kind in ["no-token", "rejected", "session-gone", "down"]:
        assert f'"{kind}"' in panel, f"the {kind} cause must be named and handled"
    assert "chatNeedsToken" in panel and "openOptionsPage" in panel
    # A bad token is not a blip: retrying it every 15s changes nothing and buries the
    # one message that would have told the reader what to do.
    assert "if (why.kind === \"no-token\" || why.kind === \"rejected\") return;" in panel
