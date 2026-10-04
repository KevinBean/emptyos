"""Behavioural tests for the extension's daemon-client config layer.

The sibling contract test greps the sources for `chrome.storage.sync.remove("token")`.
That is a claim about the *text*, and it happily passed while the read path wrote to
sync storage on every call — burning the 1800-writes/hour quota in ~36 minutes of Flow
reading, after which every getConfig() consumer (chat, capture, dictionary, badge,
heartbeat) rejected. So these tests EXECUTE getConfig() against a stubbed chrome.*
and count the writes it causes.

Node runs the harness; the tests skip when node is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CLIENT = Path(__file__).parents[1] / "tools" / "chrome-extension" / "daemon-client.js"

# Chrome's documented storage.sync ceilings. The read path must stay at zero.
SYNC_WRITES_PER_MINUTE = 120
SYNC_WRITES_PER_HOUR = 1800

HARNESS = """
const store = {{ sync: {sync}, local: {local}, session: {session} }};
const writes = {{ sync: 0, local: 0, session: 0 }};
const area = (k) => ({{
  get: async (defaults) => ({{ ...defaults, ...Object.fromEntries(
    Object.keys(defaults).filter((x) => x in store[k]).map((x) => [x, store[k][x]])) }}),
  set: async (o) => {{ writes[k]++; Object.assign(store[k], o); }},
  remove: async (x) => {{ writes[k]++; delete store[k][x]; }},
  setAccessLevel: async () => {{}},
}});
globalThis.chrome = {{ storage: {{ sync: area("sync"), local: area("local"), session: area("session") }} }};
require({client});
const {{ getConfig }} = globalThis.EOS_DAEMON;
(async () => {{
  let config = null;
  let threw = null;
  try {{
    for (let i = 0; i < {calls}; i++) config = await getConfig();
  }} catch (error) {{ threw = String(error.message || error); }}
  console.log(JSON.stringify({{ config, threw, writes, store }}));
}})();
"""


def _run(tmp_path: Path, calls: int = 1, sync: dict | None = None,
         local: dict | None = None, session: dict | None = None) -> dict:
    # tmp_path is required: the harness must never be written into the extension
    # directory, which ships to the Web Store and which the contract test reads.
    script = HARNESS.format(
        sync=json.dumps(sync or {}), local=json.dumps(local or {}),
        session=json.dumps(session or {}), client=json.dumps(str(CLIENT)), calls=calls,
    )
    path = tmp_path / "harness.js"
    path.write_text(script, encoding="utf-8")
    out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip(), "harness produced no output"
    return json.loads(out.stdout.strip().splitlines()[-1])


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def test_read_path_never_writes_to_sync_storage(tmp_path):
    """getConfig() is a read. The reading-consent poller calls it every 1.2s."""
    calls = SYNC_WRITES_PER_MINUTE + 1
    result = _run(tmp_path, calls=calls, sync={"host": "http://127.0.0.1:9000", "name": ""})
    assert result["threw"] is None
    assert result["writes"]["sync"] == 0, (
        f"{result['writes']['sync']} sync writes from {calls} reads — "
        f"the quota is {SYNC_WRITES_PER_MINUTE}/min, {SYNC_WRITES_PER_HOUR}/hr"
    )


def test_legacy_sync_token_migrates_once_then_the_read_path_goes_quiet(tmp_path):
    result = _run(tmp_path, calls=5, sync={"host": "http://127.0.0.1:9000", "token": "legacy-secret"})
    assert result["threw"] is None
    # The token leaves sync for session, and sync is written exactly once to drop it.
    assert "token" not in result["store"]["sync"]
    assert result["store"]["session"]["token"] == "legacy-secret"
    assert result["config"]["token"] == "legacy-secret"
    assert result["writes"]["sync"] == 1, "migration must be one-shot, not per-read"


def test_unusable_stored_host_degrades_instead_of_throwing(tmp_path):
    """The options page repairs a bad host — and it opens by calling getConfig()."""
    result = _run(tmp_path, sync={"host": "http://100.101.102.103:9000", "name": ""})
    assert result["threw"] is None, "a throw here leaves the options page blank and unfixable"
    assert result["config"]["host"] == "http://127.0.0.1:9000"
    assert result["config"]["storedHost"] == "http://100.101.102.103:9000"
    assert "HTTPS" in result["config"]["hostError"]


def test_remembered_token_reads_from_local_and_session_token_is_ephemeral(tmp_path):
    remembered = _run(tmp_path, local={"rememberToken": True, "token": "on-this-device"})
    assert remembered["config"]["token"] == "on-this-device"
    assert remembered["config"]["rememberToken"] is True

    ephemeral = _run(tmp_path, local={"rememberToken": False}, session={"token": "this-session-only"})
    assert ephemeral["config"]["token"] == "this-session-only"
    assert ephemeral["config"]["rememberToken"] is False
