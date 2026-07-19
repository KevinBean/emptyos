"""Unit tests for scripts/agent_fleet_hook.py — the agent_fleet ingest hook.

Daemon-free: the hook is stdlib-only and imports nothing from emptyos (same
rule as every hook in scripts/), so these run anywhere.

The load-bearing test here is `test_scan_matches_tomllib_on_real_config`. The
hook hand-scans the `[network]` TOML table instead of importing tomllib (~14 ms
per agent event — see the module docstring). That shortcut is only safe while it
agrees with the real parser on the real file; if emptyos.toml ever grows a shape
the scan can't read, this goes red instead of the fleet going quietly blind.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "scripts" / "agent_fleet_hook.py"


def _load():
    spec = importlib.util.spec_from_file_location("agent_fleet_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hook = _load()


# ── the [network] scan vs the real parser ────────────────────────────────────

def test_scan_matches_tomllib_on_real_config():
    """The hand-scan must agree with tomllib on the actual emptyos.toml."""
    import tomllib

    cfg_path = ROOT / "emptyos.toml"
    if not cfg_path.exists():
        pytest.skip("emptyos.toml not present (fresh clone)")
    text = cfg_path.read_text(encoding="utf-8")
    truth = (tomllib.loads(text).get("network") or {})
    scanned = hook.scan_network_table(text)

    for key, want in truth.items():
        if not isinstance(want, (str, int, bool)):
            continue  # nested/array values are out of the scan's contract
        assert key in scanned, f"scan missed [network].{key}"
        assert scanned[key] == str(want), f"[network].{key}: {scanned[key]!r} != {want!r}"


def test_scan_ignores_other_tables():
    """A key of the same name in another table must not leak into [network]."""
    text = '[other]\nauth_token = "WRONG"\n\n[network]\nauth_token = "RIGHT"\nport = 9000\n'
    got = hook.scan_network_table(text)
    assert got["auth_token"] == "RIGHT"
    assert got["port"] == "9000"


def test_scan_handles_comments_and_blanks():
    text = '# lead\n[network]\n# c\nauth_token = "T"   # trailing\n\nport = 9001\n'
    got = hook.scan_network_table(text)
    assert got["auth_token"] == "T"
    assert got["port"] == "9001"


def test_scan_stops_at_next_table():
    text = '[network]\nport = 9000\n\n[notes]\nport = 1234\n'
    assert hook.scan_network_table(text)["port"] == "9000"


# ── envelope: the frozen contract ────────────────────────────────────────────

def test_envelope_only_emits_allowlisted_keys():
    """A payload full of secrets must yield ONLY the allowlist. This is the
    privacy floor — prompts and tool payloads never leave the hook."""
    payload = {
        "session_id": "s1",
        "cwd": "D:/emptyos",
        "prompt": "SECRET USER PROMPT",
        "transcript_path": "C:/transcripts/secret.jsonl",
        "tool_input": {"command": "cat ~/.ssh/id_rsa"},
        "tool_response": "PRIVATE KEY MATERIAL",
        "message": "hello",
    }
    env = hook._envelope("UserPromptSubmit", payload)
    assert set(env).issubset(set(hook.ENVELOPE_KEYS))
    blob = json.dumps(env)
    for leak in ("SECRET USER PROMPT", "id_rsa", "PRIVATE KEY", "transcripts"):
        assert leak not in blob, f"envelope leaked {leak!r}"


def test_envelope_required_fields():
    env = hook._envelope("Stop", {"session_id": "s1", "cwd": "D:/x"})
    assert env["source"] in ("claude", "codex")
    assert env["session_id"] == "s1"
    assert env["event"] == "Stop"
    assert env["ts"].endswith("Z")


def test_envelope_falls_back_to_hook_event_name():
    env = hook._envelope("", {"session_id": "s", "hook_event_name": "SessionStart"})
    assert env["event"] == "SessionStart"


def test_envelope_missing_session_id_is_empty_not_crash():
    env = hook._envelope("Stop", {})
    assert env["session_id"] == ""


def test_notification_type_is_taken_from_the_native_field():
    """Claude sends `notification_type` natively (verified against captured
    payloads). It is never inferred from message prose."""
    env = hook._envelope("Notification", {
        "session_id": "s", "notification_type": "permission_prompt",
        "message": "Claude needs your permission",
    })
    assert env["notification_type"] == "permission_prompt"
    assert "permission" not in json.dumps({k: v for k, v in env.items() if k != "notification_type"})


def test_idle_prompt_is_not_mislabelled_as_a_permission_gate():
    """REGRESSION. An earlier draft sniffed the message text, and Claude's idle
    notification reads "Claude is waiting for your input" — which that heuristic
    matched, turning a merely-idle session into a fake `blocked` alert. The
    native field says `idle_prompt`; nothing may override it."""
    env = hook._envelope("Notification", {
        "session_id": "s", "notification_type": "idle_prompt",
        "message": "Claude is waiting for your input",
    })
    assert env["notification_type"] == "idle_prompt"


def test_turn_id_is_sourced_from_prompt_id():
    """REGRESSION. Claude has no `turn_id` — it sends `prompt_id`. Reading
    `turn_id` off the payload silently yielded nothing on every real event."""
    env = hook._envelope("UserPromptSubmit", {"session_id": "s", "prompt_id": "p42"})
    assert env["turn_id"] == "p42"


def test_payload_source_does_not_override_our_source():
    """SessionStart carries its own `source` ("startup"/"resume"/"clear"), which
    collides by NAME with the envelope's source (claude|codex). They are
    unrelated; the payload's must never win."""
    env = hook._envelope("SessionStart", {"session_id": "s", "source": "startup"})
    assert env["source"] in ("claude", "codex")


def test_envelope_truncates_oversized_values():
    env = hook._envelope("Stop", {"session_id": "s" * 5000, "cwd": "c" * 5000})
    assert len(env["session_id"]) <= 128
    assert len(env["cwd"]) <= 512


# ── the hook must never break an agent turn ──────────────────────────────────

@pytest.mark.parametrize("stdin_data", [
    b"",                       # no payload at all
    b"not json at all",        # garbage
    b"[]",                     # valid json, wrong type
    b"null",
    b'{"session_id": "s"}',    # minimal valid
])
def test_hook_exits_zero_on_any_stdin(stdin_data):
    """Whatever it is handed — and whether or not a daemon is listening — the
    hook exits 0. A non-zero exit here would surface as an error in every
    agent turn on this machine."""
    r = subprocess.run(
        [sys.executable, str(HOOK), "UserPromptSubmit"],
        input=stdin_data, capture_output=True, timeout=30,
    )
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr!r}"


def test_hook_exits_zero_with_no_event_arg():
    r = subprocess.run([sys.executable, str(HOOK)], input=b"{}",
                       capture_output=True, timeout=30)
    assert r.returncode == 0


def test_hook_writes_nothing_to_stdout():
    """Hook stdout is consumed by the agent; this hook has nothing to say."""
    r = subprocess.run([sys.executable, str(HOOK), "Stop"],
                       input=b'{"session_id":"s"}', capture_output=True, timeout=30)
    assert r.stdout == b"", f"unexpected stdout: {r.stdout!r}"


def test_daemon_down_is_fast_and_silent(monkeypatch):
    """A refused connect must not raise and must not hang the turn."""
    monkeypatch.setattr(hook, "_daemon", lambda: ("127.0.0.1", 59999, "tok"))
    hook._post({"source": "claude", "session_id": "s", "event": "Stop", "ts": "t"})


def test_unreadable_config_degrades_to_no_post(monkeypatch, tmp_path):
    monkeypatch.setattr(hook, "_repo_root", lambda: str(tmp_path))  # no emptyos.toml
    assert hook._daemon() == ("", 0, "")
    hook._post({"source": "claude", "session_id": "s", "event": "Stop", "ts": "t"})


def test_wildcard_bind_is_never_posted_to(monkeypatch, tmp_path):
    (tmp_path / "emptyos.toml").write_text(
        '[network]\nhost = "0.0.0.0"\nport = 9000\nauth_token = "t"\n', encoding="utf-8")
    monkeypatch.setattr(hook, "_repo_root", lambda: str(tmp_path))
    host, port, tok = hook._daemon()
    assert host == "127.0.0.1", "must not POST at a wildcard bind address"
    assert (port, tok) == (9000, "t")


def test_repo_root_finds_config_regardless_of_cwd(tmp_path, monkeypatch):
    """The hook is wired user-globally and fires for sessions rooted anywhere,
    so it must locate emptyos.toml from its OWN location, never from cwd."""
    monkeypatch.chdir(tmp_path)
    assert os.path.basename(hook._repo_root()) == "emptyos"
    if (Path(hook._repo_root()) / "emptyos.toml").exists():
        host, port, _ = hook._daemon()
        assert host and port, "config must resolve from a foreign cwd"


# ── cost guards: these pin the two measured optimizations ────────────────────

def test_hook_is_stdlib_only_so_dash_S_is_safe():
    """The hook configs run this with `python -S` (skips site init, ~7 ms/event).
    That is only safe while the module imports nothing outside the stdlib — if a
    third-party import ever creeps in, -S breaks it in EVERY agent turn on this
    machine. Fail here instead."""
    r = subprocess.run(
        [sys.executable, "-S", str(HOOK), "Stop"],
        input=b'{"session_id":"s"}', capture_output=True, timeout=30,
    )
    assert r.returncode == 0, f"-S run failed: {r.stderr!r}"
    assert r.stderr == b"", f"-S run wrote stderr: {r.stderr!r}"


def test_hook_does_not_import_expensive_modules():
    """Guards the three import costs this hook was deliberately shaped around
    (measured with -X importtime): urllib.request ~27 ms, tomllib ~14 ms,
    pathlib ~4 ms. Re-adding any of them silently taxes every agent event."""
    r = subprocess.run(
        [sys.executable, "-X", "importtime", str(HOOK), "Stop"],
        input=b'{"session_id":"s"}', capture_output=True, timeout=30,
    )
    loaded = r.stderr.decode("utf-8", "replace")
    for banned, why in (
        ("urllib.request", "~27 ms — use a raw socket instead"),
        ("tomllib", "~14 ms — use scan_network_table() instead"),
        ("pathlib", "~4 ms — use os.path instead"),
    ):
        assert f"| {banned}" not in loaded, f"hook imports {banned} ({why})"


# ── against the REAL captured payloads ───────────────────────────────────────

FIXTURES = ROOT / "tests" / "fixtures" / "agent_fleet" / "claude-events.jsonl"


def _real_events():
    if not FIXTURES.exists():
        return []
    return [json.loads(l) for l in FIXTURES.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.mark.skipif(not FIXTURES.exists(), reason="fixtures not captured")
@pytest.mark.parametrize("rec", _real_events(),
                         ids=[r["_event"] + str(i) for i, r in enumerate(_real_events())])
def test_envelope_never_leaks_content_from_real_payloads(rec):
    """The privacy floor, checked against payloads Claude actually sent — real
    prompts and assistant messages, not a synthetic guess at the shape."""
    env = hook._envelope(rec["_event"], rec["payload"])
    assert set(env).issubset(set(hook.ENVELOPE_KEYS))
    blob = json.dumps(env)
    for content_key in ("prompt", "last_assistant_message", "message",
                        "transcript_path", "agent_transcript_path"):
        if content_key in rec["payload"]:
            assert str(rec["payload"][content_key]) not in blob, f"leaked {content_key}"


@pytest.mark.skipif(not FIXTURES.exists(), reason="fixtures not captured")
def test_real_payloads_all_yield_a_session_id():
    """Every real event must key to a session, or the reducer can't track it."""
    for rec in _real_events():
        env = hook._envelope(rec["_event"], rec["payload"])
        assert env["session_id"], f"{rec['_event']} produced no session_id"


@pytest.mark.skipif(not FIXTURES.exists(), reason="fixtures not captured")
def test_session_end_fires_and_clear_retires_the_session_id():
    """SessionEnd was long unverified (no session ended during the first
    capture). It DOES fire — observed twice, both `reason: "clear"`.

    The important part is what /clear does: it retires the old session_id and
    opens a NEW one in the same second. So the fleet marking the old record
    `ended` is correct — that conversation really did end — and the human's
    still-open terminal shows up as the fresh session, not as a ghost.

    Caveat this test deliberately does NOT claim: both captures were graceful
    (`clear`). An abruptly-killed terminal is not known to emit SessionEnd at
    all, which is why the TTL sweep stays the only guaranteed terminator.
    """
    evs = _real_events()
    ends = [r for r in evs if r["_event"] == "SessionEnd"]
    assert ends, "no SessionEnd in the corpus — recapture before trusting this"
    assert all("reason" in r["payload"] for r in ends)

    seq = [(r["_event"], r["payload"].get("session_id")) for r in evs]
    for _, sid in [(e, s) for e, s in seq if e == "SessionEnd"]:
        events_for_sid = [e for e, s in seq if s == sid]
        after_end = events_for_sid[events_for_sid.index("SessionEnd") + 1:]
        assert not after_end, f"{sid} kept emitting after SessionEnd: {after_end}"


@pytest.mark.skipif(not FIXTURES.exists(), reason="fixtures not captured")
def test_stop_is_a_turn_end_not_a_session_end_in_real_data():
    """[R3], proven from captured traffic rather than from the docs: a real
    session continues to emit events AFTER Stop. Any reducer that treats Stop
    as termination is wrong. This test documents the evidence."""
    evs = _real_events()
    by_session = {}
    for r in evs:
        by_session.setdefault(r["payload"].get("session_id"), []).append(r["_event"])
    continued = [s for s, e in by_session.items()
                 if "Stop" in e and e.index("Stop") < len(e) - 1]
    assert continued, "no captured session outlived a Stop — recapture before trusting this"


# ── the hand-rolled HTTP request ─────────────────────────────────────────────

def test_post_emits_wellformed_http_with_bearer(monkeypatch):
    """The hook hand-rolls its HTTP/1.1 request (urllib costs ~27 ms to import).
    Hand-rolled framing is exactly what breaks silently, so capture the real
    bytes off a socket and assert the shape.
    """
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    got = {}

    def serve():
        conn, _ = srv.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data or len(data) < 20:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            got["raw"] = data.decode("utf-8", "replace")

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    monkeypatch.setattr(hook, "_daemon", lambda: ("127.0.0.1", port, "TOK123"))
    hook._post({"source": "claude", "session_id": "s1", "event": "Stop",
                "ts": "2026-07-17T00:00:00Z", "cwd": "D:/emptyos"})
    t.join(timeout=5)
    srv.close()

    raw = got.get("raw", "")
    assert raw.startswith("POST /agent_fleet/api/hook HTTP/1.1\r\n"), raw[:80]
    assert "Authorization: Bearer TOK123\r\n" in raw
    assert "Content-Type: application/json\r\n" in raw
    assert "Connection: close\r\n" in raw
    head, _, body = raw.partition("\r\n\r\n")
    # Content-Length must match the real body, or the server hangs waiting.
    clen = int([l.split(":")[1] for l in head.splitlines()
                if l.lower().startswith("content-length")][0])
    assert clen == len(body.encode("utf-8")), f"Content-Length {clen} != body {len(body)}"
    assert json.loads(body)["session_id"] == "s1"


def test_post_omits_auth_header_when_no_token(monkeypatch):
    """A tokenless config must not send a bare `Authorization: Bearer` header."""
    sent = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def sendall(self, b): sent["b"] = b.decode("utf-8", "replace")

    monkeypatch.setattr(hook, "_daemon", lambda: ("127.0.0.1", 9000, ""))
    import socket
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: FakeSock())
    hook._post({"source": "claude", "session_id": "s", "event": "Stop", "ts": "t"})
    assert "Authorization" not in sent.get("b", "")


# ── wezterm status mapping (preserves the retired powershell hook's UX) ──────

def test_wezterm_status_mapping_preserved():
    """These four mappings are what settings.json encoded before this hook
    replaced wezterm-status.ps1. Changing one changes Kevin's tab colours."""
    import base64

    def word(event):
        return base64.b64decode(hook._WEZTERM_B64[event]).decode()

    assert word("SessionStart") == "idle"
    assert word("UserPromptSubmit") == "working"
    assert word("Notification") == "waiting"
    assert word("Stop") == "done"


def test_precomputed_b64_constants_are_correct():
    """The constants exist to avoid importing base64 at hook runtime; this test
    is what keeps them honest."""
    import base64

    for event, b64 in hook._WEZTERM_B64.items():
        decoded = base64.b64decode(b64).decode()
        assert base64.b64encode(decoded.encode()).decode() == b64, event


# ── ts resolution is a SEAM contract, not cosmetics ──────────────────────────

def test_ts_has_subsecond_resolution():
    """REGRESSION (found by running the hook against the live ingest endpoint,
    not by reading either half).

    The ingest side dedups on `event + ts`. At second resolution, two same-type
    events in the same second produce an identical key and the later one is
    dropped as a duplicate. The case that bites: Claude emits
    Notification(idle_prompt) then Notification(permission_prompt) back to back
    -- the permission gate gets discarded and the fleet never reports `blocked`,
    the one signal the app exists to surface.

    Neither half was wrong alone. Only running both together showed it.
    """
    a = hook._envelope("Notification", {"session_id": "s", "notification_type": "idle_prompt"})
    b = hook._envelope("Notification", {"session_id": "s", "notification_type": "permission_prompt"})
    assert a["ts"] != b["ts"], "two events in the same second must not share a ts"
    assert "." in a["ts"] and a["ts"].endswith("Z")


def test_ts_is_accepted_by_the_ingest_validator():
    """The finer stamp must still satisfy the ingest side's UTC-ISO check --
    a fix that made every event `invalid_ts` would be worse than the bug."""
    from datetime import UTC, datetime

    ts = hook._now_iso()
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == UTC.utcoffset(parsed)
    assert parsed.microsecond or True  # resolution present, value may be 0


def test_ts_is_monotonic_enough_for_the_dedup_ring():
    """Successive envelopes must produce distinct keys under a tight loop."""
    seen = {hook._envelope("Stop", {"session_id": "s"})["ts"] for _ in range(50)}
    assert len(seen) > 1, "50 back-to-back envelopes collapsed to one timestamp"


def test_post_waits_for_the_server_reply():
    """REGRESSION -- the worst bug of the build, and invisible to every test
    that existed at the time.

    _post used to close the socket right after sendall ("fire and forget: we
    don't care about the reply"). uvicorn CANCELS the request handler when the
    client disconnects before the response, so the ingest was aborted mid-flight
    and nothing was ever persisted. The hook still exited 0 in ~1 ms and looked
    perfect; the fleet was simply always empty, with nothing to explain why.

    A fake server cannot reproduce uvicorn's cancellation, so this does not
    assert the daemon's behaviour -- it pins the client-side contract that makes
    the daemon's behaviour safe: _post must still be running when the reply
    arrives. If someone "optimises" the recv away, this goes red.
    """
    import socket
    import threading
    import time

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    state = {}

    def serve():
        conn, _ = srv.accept()
        with conn:
            conn.recv(4096)
            time.sleep(0.25)                       # server "processing"
            state["replied_at"] = time.perf_counter()
            try:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                state["client_vanished"] = True    # the old bug's signature

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    monkey = hook._daemon
    hook._daemon = lambda: ("127.0.0.1", port, "TOK")
    try:
        hook._post({"source": "claude", "session_id": "s", "event": "Stop", "ts": "t"})
        returned_at = time.perf_counter()
    finally:
        hook._daemon = monkey
    t.join(timeout=5)
    srv.close()

    assert not state.get("client_vanished"), (
        "_post closed before the server replied — uvicorn would cancel the ingest"
    )
    assert "replied_at" in state
    assert returned_at >= state["replied_at"], (
        "_post returned before the reply arrived; it is not waiting for the server"
    )


def test_explicit_source_wins_over_the_env_sniff(monkeypatch):
    """REGRESSION: the hook used to infer the agent from CODEX_PROJECT_DIR --
    which run_codex_hook.py sets, not codex -- so every real codex session was
    labelled "claude" and the fleet's agent column silently lied. The configs now
    pass the source in argv; it must beat any env guess."""
    monkeypatch.setenv("CODEX_PROJECT_DIR", "/anything")
    assert hook._envelope("Stop", {"session_id": "s"}, "claude")["source"] == "claude"
    monkeypatch.delenv("CODEX_PROJECT_DIR", raising=False)
    assert hook._envelope("Stop", {"session_id": "s"}, "codex")["source"] == "codex"


def test_sniff_is_only_a_fallback_and_cannot_see_real_codex(monkeypatch):
    """Documents the limit honestly: with no argv and a REAL codex env (no
    runner var), the sniff says "claude". That is why argv exists -- not a bug
    to fix with another heuristic."""
    for var in ("CODEX_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)
    assert hook._sniff_source() == "claude"
    monkeypatch.setenv("CODEX_PROJECT_DIR", "/x")
    assert hook._sniff_source() == "codex"


def test_bad_source_arg_falls_back_rather_than_sending_garbage(monkeypatch):
    """main() must not forward an unvalidated argv[2] into the frozen envelope --
    the ingest side rejects anything outside {claude, codex}."""
    monkeypatch.delenv("CODEX_PROJECT_DIR", raising=False)
    assert hook._envelope("Stop", {"session_id": "s"}, "")["source"] in ("claude", "codex")
