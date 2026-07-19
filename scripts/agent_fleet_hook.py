#!/usr/bin/env python3
"""agent_fleet hook — one hook, two obligations, per coding-agent event.

Called from Claude Code / Codex hook configs with the event name as argv[1] and
the agent's event payload as JSON on stdin. Two jobs, in this order:

  1. WezTerm status  — write the OSC user-var to the REAL console device so the
     tab title colours. This runs on EVERY transition, unconditionally. It is
     NOT a fallback for daemon downtime: the console write is the primary UX and
     the daemon POST is the optional half (the reverse of the first plan draft).
  2. Fleet POST      — send a normalized, allowlisted envelope to the local
     daemon's agent_fleet app. Best-effort: any failure is silent, and a daemon
     that is down costs one refused localhost connect (~1ms).

Design constraints, all measured (see `.claude/plans/agent-fleet.md` §7):

  * stdlib only, imports nothing from `emptyos` — same rule as every other hook
    in this directory: it must keep working when the kernel or daemon is broken.
  * this replaces `~/.claude/wezterm-status.ps1`, which costs ~172 ms per event
    (powershell -NoProfile startup). Measured end-to-end against the live daemon
    — the real script, the real wired command line — this hook is **~60 ms p50
    (min 51)**: roughly **2.9x cheaper** than the status hook it replaces, while
    also feeding the fleet. So fleet observation still *reduces* per-event cost;
    it is not a tax.
    Honest breakdown: ~35 ms python startup + imports, ~7 ms p50 daemon
    round-trip (tail to ~27 ms under load). An earlier draft of this comment
    claimed ~36 ms / 4.8x — that was measured before `_post` waited for the
    reply, i.e. against a daemon that was never actually ingesting anything.
    The number went up because the hook started *working*.
  * `urllib.request` costs ~27 ms to import — more than the whole python startup
    floor. The POST is therefore a raw `socket` write (~3 ms to import) of a
    fixed-shape HTTP/1.1 request. It DOES wait for the reply: closing without
    reading makes uvicorn cancel the ingest handler, so the event is silently
    never recorded (see `_post`). ~11 ms well spent.
  * `tomllib` costs ~14 ms to import — the single biggest avoidable cost, paid
    to read two values out of a 35 KB file. `_daemon()` hand-scans the
    `[network]` table instead (no import; the parse itself was only ~1.8 ms).
    `tests/test_unit_agent_fleet_hook.py` pins the scan against tomllib's answer
    on the real emptyos.toml, so the shortcut can't silently drift.
  * `base64` costs ~2.6 ms to import to encode one of four fixed strings, so the
    four OSC payloads are precomputed constants instead.
  * NEVER forwards prompt text or tool payloads. Only the allowlist in
    `ENVELOPE_KEYS` leaves this machine's daemon boundary.

Usage (from a hook config) — argv[1] is the event, argv[2] the agent:
    python -S "<repo>/scripts/agent_fleet_hook.py" UserPromptSubmit claude
    python -S "<repo>/scripts/agent_fleet_hook.py" UserPromptSubmit codex

The source is passed EXPLICITLY, never inferred: the only env var that could
distinguish them (`CODEX_PROJECT_DIR`) is set by a runner this hook bypasses, so
sniffing it labelled every codex session "claude".

Both agents are wired **user-globally** (`~/.claude/settings.json`,
`~/.codex/hooks.json`), not per-repo. Codex runs in worktrees whose checked-out
`.codex/hooks.json` predates any change, so a repo-local wiring never reaches
the sessions that need it — and codex merges both configs, so wiring both would
double-fire.
"""

from __future__ import annotations

import json
import os
import sys
import time

# NB: no `pathlib` import — it costs ~4 ms to load (measured with -X importtime)
# and this hook needs exactly one path join. `os.path` is already free via `os`.
# For the same reason the hook configs invoke this with `python -S`: skipping
# site-packages init saves a further ~7 ms and is safe *because* this file is
# stdlib-only. Both facts are pinned by tests/test_unit_agent_fleet_hook.py.

# ── WezTerm status vocabulary ────────────────────────────────────────────────
# Preserves the exact mapping the retired wezterm-status.ps1 entries encoded in
# settings.json. This is a *UX* status (what colour is the tab), deliberately
# NOT the fleet reducer's state vocabulary (idle/working/blocked/ended) — those
# are different concerns with different consumers. Do not unify them.
#
# Values are the precomputed base64 of the status word (what the OSC carries) —
# see the module docstring: importing `base64` to encode one of four fixed
# strings costs more than every other line in this file combined.
_WEZTERM_B64: dict[str, str] = {
    "SessionStart": "aWRsZQ==",        # idle
    "UserPromptSubmit": "d29ya2luZw==",  # working
    "Notification": "d2FpdGluZw==",      # waiting
    "PermissionRequest": "d2FpdGluZw==",  # waiting
    "Stop": "ZG9uZQ==",                # done
    "SessionEnd": "aWRsZQ==",          # idle
}

#: The frozen envelope contract (`.claude/plans/agent-fleet-codex-prompt.md` §1).
#: Nothing outside this list is ever sent.
ENVELOPE_KEYS = (
    "source", "session_id", "event", "ts",
    "cwd", "turn_id", "agent_id", "notification_type",
)

_DEFAULT_PORT = 9000
_CONNECT_TIMEOUT = 0.4  # daemon down → refused fast; daemon wedged → we bail
#: Bytes of the reply to read. The VALUE is irrelevant (we never parse it) but
#: the READ is mandatory — see the note in _post(). Enough for the status line.
_RESPONSE_PEEK = 64


def _now_iso() -> str:
    """UTC ISO-8601 with MICROSECOND resolution.

    The resolution is load-bearing, not cosmetic. The ingest side dedups on
    ``event + ts``, so a second-resolution stamp makes two same-type events in
    the same second collide and the later one is dropped as a "duplicate". The
    case that actually bites: Claude emits ``Notification(idle_prompt)`` and
    ``Notification(permission_prompt)`` back to back — at second resolution the
    permission gate is silently discarded and the fleet never reports `blocked`,
    which is the one signal the whole app exists to surface.

    Built from `time` (already imported) rather than `datetime` — see the module
    docstring on import cost. Pinned by test_ts_has_subsecond_resolution.
    """
    t = time.time()
    return "%s.%06dZ" % (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)),
                         int((t % 1) * 1_000_000))


def _wezterm(b64: str) -> None:
    """Write the OSC user-var to the console device. Never raises.

    Opening ``CONOUT$`` reaches the real console even though the agent captures
    the hook's stdout — this is what ConOut.dll did for the powershell version,
    but python can open the device directly, so the powershell hop is gone.
    """
    try:
        with open("CONOUT$", "w", encoding="utf-8") as con:
            con.write(f"\x1b]1337;SetUserVar=claude_status={b64}\x07")
    except Exception:
        pass  # no console attached (piped/detached session) — status is cosmetic


def _repo_root() -> str:
    """This script's own repo root — scripts/agent_fleet_hook.py -> repo/.

    Deliberately NOT ``cwd`` or ``CLAUDE_PROJECT_DIR``: this hook is wired
    user-globally and fires for sessions rooted anywhere, but the daemon config
    always lives beside the script. Keeps rule 13 (no personal paths in code).
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def scan_network_table(text: str) -> dict[str, str]:
    """Extract the `[network]` table's scalar string/int values from TOML text.

    A deliberate 15-line hand-scan instead of `import tomllib` (~14 ms — the
    biggest single cost in this hook, paid per agent event, to read two values).
    Only handles what `[network]` actually contains: `key = "value"` and
    `key = 1234` at the top level of one table. Anything fancier is not our
    config's shape, and `tests/test_unit_agent_fleet_hook.py` asserts this
    returns what tomllib returns for the real emptyos.toml — if the config ever
    grows a shape this can't read, that test goes red rather than the fleet
    going quietly blind.
    """
    out: dict[str, str] = {}
    in_network = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_network = line.replace(" ", "") == "[network]"
            continue
        if not in_network or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if val[:1] in ('"', "'"):
            # Quoted: take the matching close quote, so a trailing `# comment`
            # can never end up inside the token. (A real bug the tests caught.)
            end = val.find(val[0], 1)
            val = val[1:end] if end > 0 else val[1:]
        else:
            val = val.split("#")[0].strip()
        out[key.strip()] = val
    return out


def _daemon() -> tuple[str, int, str]:
    """Return (host, port, token) from emptyos.toml. ("",0,"") when unreadable."""
    try:
        with open(os.path.join(_repo_root(), "emptyos.toml"), encoding="utf-8") as f:
            net = scan_network_table(f.read())
    except Exception:
        return ("", 0, "")
    host = net.get("host") or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"  # never post to a wildcard bind
    try:
        port = int(net.get("port") or _DEFAULT_PORT)
    except ValueError:
        port = _DEFAULT_PORT
    return (host, port, net.get("auth_token") or "")


#: Envelope key -> the payload keys it may be sourced from, in priority order.
#: Verified against REAL captured payloads (2026-07-17, see the fixtures in
#: tests/fixtures/agent_fleet/) — NOT from the docs, and not guessed:
#:   * there is no `turn_id`; Claude sends `prompt_id`.
#:   * `notification_type` is a native field (`permission_prompt`/`idle_prompt`),
#:     so it is never inferred from message prose. An earlier draft sniffed the
#:     message text and would have mislabelled the *idle* prompt ("Claude is
#:     waiting for your input") as a permission gate.
_SOURCED_FROM: dict[str, tuple[str, ...]] = {
    "turn_id": ("prompt_id", "turn_id"),
    "agent_id": ("agent_id",),
    "notification_type": ("notification_type",),
}


def _sniff_source() -> str:
    """Fallback for hand-invocation only — both hook configs pass argv[2].

    `CODEX_PROJECT_DIR` does NOT identify a real codex session: it is set by
    scripts/run_codex_hook.py, a *runner* this hook bypasses. It is honoured
    here only in case someone later routes this hook through that runner.
    Sniffing it as the *primary* signal is exactly what labelled every codex
    row "claude" — silently, and with a probe that had faked the variable.
    Hence argv. If this guesses wrong, the fix is to pass the source, not to
    add another env heuristic.
    """
    return "codex" if os.environ.get("CODEX_PROJECT_DIR") else "claude"


def _envelope(event: str, payload: dict, source: str = "") -> dict:
    """Normalize an agent payload to the frozen envelope. Allowlist only.

    NB `env["source"]` is *ours* (claude|codex) and has nothing to do with
    `payload["source"]`, which SessionStart uses for its own purpose
    ("startup"/"resume"/"clear"). Don't wire them together.
    """
    env = {
        "source": source or _sniff_source(),
        "session_id": str(payload.get("session_id") or "")[:128],
        "event": event or str(payload.get("hook_event_name") or ""),
        "ts": _now_iso(),
        "cwd": str(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "")[:512],
    }
    for key, sources in _SOURCED_FROM.items():
        for src_key in sources:
            v = payload.get(src_key)
            if v:
                env[key] = str(v)[:128]
                break
    return {k: v for k, v in env.items() if k in ENVELOPE_KEYS}


def _post(env: dict) -> None:
    """Fire the envelope at the daemon. Never raises, never reads the response."""
    host, port, token = _daemon()
    if not host or not port:
        return
    try:
        import socket

        body = json.dumps(env, ensure_ascii=False).encode("utf-8")
        head = (
            f"POST /agent_fleet/api/hook HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
        )
        if token:
            head += f"Authorization: Bearer {token}\r\n"
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT) as s:
            s.sendall(head.encode("ascii") + b"\r\n" + body)
            # We MUST wait for the reply. This is not politeness and not
            # instrumentation — it is the difference between the event being
            # recorded and silently vanishing.
            #
            # An earlier draft closed here without reading ("fire and forget:
            # we don't care about the reply, and waiting costs latency"). That
            # was wrong in a way no test caught: uvicorn cancels the request
            # handler when the client disconnects before the response, so the
            # ingest was aborted mid-flight and NOTHING was ever persisted. The
            # hook still exited 0 in ~1 ms and looked perfect; the fleet was
            # simply always empty. Proven by an A/B against the live daemon --
            # close-without-reading: does not land; read-then-close: lands.
            #
            # The cost is ~11 ms per event, which buys correctness and still
            # leaves the hook ~3.5x cheaper than the powershell status hook it
            # replaces. Do not "optimise" this back.
            s.recv(_RESPONSE_PEEK)
    except Exception:
        pass  # daemon down / wedged / auth wrong — the session must not care


def main(argv: list[str]) -> int:
    event = argv[1] if len(argv) > 1 else ""
    source = argv[2].strip().lower() if len(argv) > 2 else ""
    if source not in ("claude", "codex"):
        source = ""  # unknown -> fall back to the sniff rather than send garbage
    # 1. Console status FIRST — cheap, local, and the thing the human sees.
    b64 = _WEZTERM_B64.get(event)
    if b64:
        _wezterm(b64)
    # 2. Fleet POST — optional half.
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    if event:
        _post(_envelope(event, payload, source))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)  # a hook must never fail an agent turn
