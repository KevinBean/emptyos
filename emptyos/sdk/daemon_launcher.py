"""Shared daemon-readiness poll for launchers and the release boot smoke.

Three consumers coupled to ``emptyos.sdk``: ``scripts/eos_desktop.py`` (attach to
a running daemon), ``scripts/plekto_launcher.py`` (boot in-process), and
``scripts/check_snapshot_boot.py`` (boot a release snapshot and inspect it).

Three layers, each with a real caller:

- ``fetch_health`` — one GET, parsed. The boot smoke uses it directly for
  ``?full=true``, whose payload carries the per-app load errors.
- ``poll_health`` — retry until ready / the subprocess dies / timeout, returning
  *why* it stopped and the last payload seen. The boot smoke needs both: it
  prints the daemon's exit code on death and the last payload on a hang.
- ``wait_health`` — the original bool wrapper the two launchers use.

A 200 is not the same as ready: a tree whose ``apps/`` never resolved answers
``{"status": "starting", "apps": 0}``. Pass ``ready=`` when that matters; the
default (any 200) preserves the launchers' original behaviour.

Pure stdlib (urllib, json, time) — no kernel import, so it is safe to import
from a standalone launcher script and from a PyInstaller-frozen entrypoint
(``emptyos/sdk/`` is bundled; see ``.claude/rules/daemon-handling.md``).

Deliberately NOT consumed by ``products/*/launcher.py``: those shells are
decoupled by design (they never import ``emptyos`` in-process — they only spawn
``python -m emptyos start``), so they keep their own copy rather than gain an
in-process import dependency. ``free_port`` likewise stays per-launcher, and the
two strategies are not interchangeable: ``plekto_launcher._free_port`` scans
upward from :9000 because a product *wants* the standard port, whereas
``check_snapshot_boot.pick_free_port`` binds :0 for an ephemeral port precisely
to stay off :9000-:9009 (the user's daemon, the dogfood sidecar, sandbox-pool).
Small, self-contained duplication across deliberately standalone entrypoints is
the right trade.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from typing import NamedTuple


class HealthPoll(NamedTuple):
    """Outcome of :func:`poll_health`.

    ``reason`` is ``"ready"`` | ``"proc_died"`` | ``"timeout"``. ``payload`` is
    the last body successfully parsed (``None`` if the daemon never answered, or
    answered with something that isn't JSON).
    """

    ok: bool
    payload: dict | None
    reason: str


def _get(base: str, query: str, timeout: float) -> tuple[bool, dict | None]:
    """(answered, payload). A 200 with a non-JSON body answers but has no payload."""
    url = f"{base}/api/health" + (f"?{query}" if query else "")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            raw = r.read()
    except Exception:
        return False, None
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        return True, None
    return True, body if isinstance(body, dict) else None


def fetch_health(base: str, *, query: str = "", timeout: float = 2.0) -> dict | None:
    """GET ``<base>/api/health[?<query>]``. Returns the parsed body, else None.

    ``base`` is like ``http://127.0.0.1:9000`` — use the IPv4 literal, not
    ``localhost``: Python's resolver tries IPv6 ``::1`` first and a local-mode
    daemon binds IPv4 loopback only.
    """
    return _get(base, query, timeout)[1]


def poll_health(
    base: str,
    *,
    proc=None,
    timeout: float = 30.0,
    interval: float = 0.25,
    request_timeout: float = 2.0,
    ready: Callable[[dict | None], bool] | None = None,
) -> HealthPoll:
    """Poll ``<base>/api/health`` until ready, the subprocess dies, or timeout.

    If ``proc`` (a ``Popen`` of a daemon subprocess) is given, abort early when
    it dies during boot — a busy port or bad config then surfaces as a dead
    process instead of a full-timeout hang.

    ``ready`` decides what counts as up. The default accepts any 200 (including
    a non-JSON body, whose ``payload`` is None), which is what the launchers
    have always done. Callers that must distinguish "listening" from "fully
    booted" pass their own predicate.
    """
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return HealthPoll(False, last, "proc_died")
        answered, body = _get(base, "", request_timeout)
        if body is not None:
            last = body
        if answered if ready is None else (body is not None and ready(body)):
            return HealthPoll(True, last, "ready")
        time.sleep(interval)
    return HealthPoll(False, last, "timeout")


def wait_health(base: str, proc=None, timeout: float = 30.0) -> bool:
    """Poll ``<base>/api/health`` until it answers, or time out.

    Thin bool wrapper over :func:`poll_health`, kept because the launchers only
    ever ask "is it up?". Returns ``True`` on the first 200 from the health
    route; aborts early when ``proc`` dies.
    """
    return poll_health(base, proc=proc, timeout=timeout).ok
