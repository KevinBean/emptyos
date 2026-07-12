"""Tail-and-transform helper for claude-cli stream.jsonl files.

Apps that spawn ``agent-runtime.claude_cli_run(stdout_path=stream.jsonl)``
write line-delimited Anthropic stream-json events to disk. Both fix-agent
and dogfood-agent (and any future runner) want the same UX: a live ndjson
endpoint that drives ``EOS_UI.streamPane`` in the browser.

The transform reads **two dialects** off the same ``stream.jsonl``: Anthropic
stream-json (``{"type": "assistant"|"user", ...}``, written by claude-cli) AND
the eos-agent runner's flat ``{"type": "agent:*", ...}`` lines (written by
``EosAgentRunner._RunnerEventRecorder``). Both normalize to the canonical shape
below, so an eos-agent run renders the same as a claude-cli run instead of
being dropped as "agent did nothing".

This helper owns the file-tail loop + the canonical event transform so
each app's endpoint stays tiny — it composes the started/done envelope
and yields from this helper for everything in between.

Canonical event shape (matches the EOS_UI.streamPane consumer):

    {"type": "chunk",       "text": "...",                  "elapsed_ms": int}
    {"type": "tool_use",    "tool": "Bash"|"Read"|...,
                            "input": {...}, "id": "...",    "elapsed_ms": int}
    {"type": "tool_result", "tool_use_id": "...",
                            "preview": "...",               "elapsed_ms": int}

``elapsed_ms`` is wall-clock since ``meta[started_at_field]`` so replays
of finished runs report real durations, not 0ms. Falls back to ``time.time()``
at gen-start when started_at is missing.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Iterable


def _parse_started_at(meta: dict, field: str) -> float:
    """ISO timestamp from meta[field] → unix seconds. Falls back to time.time()."""
    raw = meta.get(field)
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return time.time()


def transform_stream_json_line(raw: str, t0_wall_s: float) -> list[dict]:
    """One Anthropic stream-json line → 0+ canonical events.

    Exposed so callers that want one-shot transforms (no tailing) can
    drive the same shape — e.g. replaying a recorded run without
    streaming, or a unit test.
    """
    try:
        evt = json.loads(raw)
    except Exception:
        return []
    elapsed = max(0, int((time.time() - t0_wall_s) * 1000))
    etype = evt.get("type")
    out: list[dict] = []
    if etype == "assistant":
        for block in (evt.get("message", {}) or {}).get("content", []) or []:
            btype = block.get("type")
            if btype == "text":
                text = block.get("text") or ""
                if text:
                    out.append({"type": "chunk", "text": text, "elapsed_ms": elapsed})
            elif btype == "tool_use":
                out.append({
                    "type": "tool_use",
                    "tool": block.get("name") or "?",
                    "input": block.get("input") or {},
                    "id": block.get("id"),
                    "elapsed_ms": elapsed,
                })
    elif etype == "user":
        for block in (evt.get("message", {}) or {}).get("content", []) or []:
            if block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(
                        str(c.get("text", c)) for c in content if c
                    )
                preview = str(content)[:300] if content else ""
                out.append({
                    "type": "tool_result",
                    "tool_use_id": block.get("tool_use_id"),
                    "preview": preview,
                    "elapsed_ms": elapsed,
                })
    elif etype and etype.startswith("agent:"):
        # eos-agent runner dialect. `EosAgentRunner`'s `_RunnerEventRecorder`
        # writes flat `{"type": "agent:*", ...}` lines — NOT Anthropic
        # stream-json. Map them to the same canonical shape so a native
        # eos-agent run renders identically to a claude-cli run in the run
        # drawer, instead of being silently dropped (which read as "agent did
        # nothing"). See .claude/rules/test-fix-verify-loop.md + AGENT-RUNNER-MIGRATION.md.
        if etype == "agent:text":
            text = evt.get("delta") or ""
            if text:
                out.append({"type": "chunk", "text": text, "elapsed_ms": elapsed})
        elif etype == "agent:tool_call":
            out.append({
                "type": "tool_use",
                "tool": evt.get("name") or "?",
                "input": evt.get("input") or {},
                "id": evt.get("id"),
                "elapsed_ms": elapsed,
            })
        elif etype == "agent:tool_result":
            content = evt.get("content")
            if content is None:
                content = evt.get("error_snippet")
            if content is None:
                disp = evt.get("display")
                content = disp if isinstance(disp, str) else (json.dumps(disp) if disp else "")
            preview = str(content)[:300] if content else ""
            out.append({
                "type": "tool_result",
                "tool_use_id": evt.get("id"),
                "preview": preview,
                "elapsed_ms": elapsed,
            })
    return out


def last_tool_result_json(lines: Iterable[str], tool_name: str) -> dict:
    """JSON of the LAST ``tool_name`` tool_result in a claude-cli stream-json run.

    The programmatic counterpart to ``transform_stream_json_line``: where that
    yields a *truncated 300-char preview* for the live ``EOS_UI.streamPane``,
    this returns the tool's **full** return value parsed as JSON — for a daemon
    that drives ``claude -p --allowedTools <tool>`` and needs the real result
    (e.g. the claude-design connector's DesignSync round-trip, where a
    ``get_file`` can be 256 KiB).

    Correlates ``tool_use`` blocks named ``tool_name`` to their ``tool_result``
    by id. Tolerant of heartbeat / partial / non-JSON lines. Returns ``{}`` when
    the tool was never called, or ``{"ok": False, "raw": <text>}`` when the
    result text isn't JSON.
    """
    ids: set[str] = set()
    last: dict | None = None
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            ev = json.loads(raw)
        except Exception:
            continue
        msg = ev.get("message") or {}
        for block in (msg.get("content") or []):
            if (isinstance(block, dict) and block.get("type") == "tool_use"
                    and block.get("name") == tool_name and block.get("id")):
                ids.add(block["id"])
        for block in (msg.get("content") or ev.get("content") or []):
            if (isinstance(block, dict) and block.get("type") == "tool_result"
                    and block.get("tool_use_id") in ids):
                txt = block.get("content")
                if isinstance(txt, list):
                    txt = "".join(b.get("text", "") for b in txt if isinstance(b, dict))
                try:
                    last = json.loads(txt)
                except Exception:
                    last = {"ok": False, "raw": str(txt)[:2000]}
    return last or {}


async def stream_claude_run_events(
    *,
    stream_path: Path,
    meta_path: Path,
    terminal_statuses: Iterable[str],
    started_at_field: str = "started_at",
    poll_interval_s: float = 0.2,
) -> AsyncIterator[dict]:
    """Tail ``stream_path`` (jsonl), yielding canonical chunk/tool_use/tool_result
    events. Polls until ``meta_path``'s ``status`` field is in
    ``terminal_statuses``, then drains the remainder and returns.

    Does NOT emit ``started`` or ``done`` events — the caller composes
    those around the helper so each app can include its own envelope
    fields (run_id, filename, branch, commits, …).

    The transform reads ``meta[started_at_field]`` (ISO timestamp) to
    anchor elapsed_ms to wall-clock since the run began, which makes
    replays of finished runs report real durations.
    """
    terminal = set(terminal_statuses)

    def _read_meta() -> dict:
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    meta = _read_meta()
    t0_wall_s = _parse_started_at(meta, started_at_field)
    offset = 0

    while True:
        if stream_path.exists():
            try:
                with stream_path.open("r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset = f.tell()
            except Exception:
                chunk = ""
            if chunk:
                # Only process complete lines; trailing partial waits for next pass.
                if not chunk.endswith("\n"):
                    last_nl = chunk.rfind("\n")
                    if last_nl >= 0:
                        offset -= len(chunk) - (last_nl + 1)
                        chunk = chunk[: last_nl + 1]
                    else:
                        offset -= len(chunk)
                        chunk = ""
                for line in chunk.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    for ev in transform_stream_json_line(line, t0_wall_s):
                        yield ev
        status = _read_meta().get("status", "")
        if status in terminal:
            break
        await asyncio.sleep(poll_interval_s)

    # Drain any lines written between the last poll and status flipping terminal.
    if stream_path.exists():
        try:
            with stream_path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                rest = f.read()
        except Exception:
            rest = ""
        for line in rest.splitlines():
            line = line.strip()
            if not line:
                continue
            for ev in transform_stream_json_line(line, t0_wall_s):
                yield ev
