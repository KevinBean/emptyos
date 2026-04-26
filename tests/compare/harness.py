"""EOS agent harness — send one prompt, collect the full turn, return final text + metrics.

Usage:
    python tests/compare/harness.py <provider> <question_id> "<prompt>"

Emits a JSON blob to stdout:
    {"provider": ..., "qid": ..., "final": "...", "tool_calls": N, "elapsed_s": ..., "events": [...]}
"""
import asyncio
import io
import json
import sys
import time

import httpx
import websockets

# Force UTF-8 on Windows so → and other non-cp1252 chars don't crash json.dumps print.
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

BASE = "http://localhost:9000"
WS_BASE = "ws://localhost:9000"
TURN_TIMEOUT = 180  # seconds


async def run_one(provider: str, qid: str, prompt: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as http:
        # Create session with requested provider
        r = await http.post(
            f"{BASE}/agent/api/sessions",
            json={"name": f"compare-{qid}", "provider": provider},
        )
        r.raise_for_status()
        sess = r.json()
        sid = sess.get("id") or sess.get("session_id")
        if not sid:
            raise RuntimeError(f"no session id: {sess}")

    started = time.time()
    ws_url = f"{WS_BASE}/agent/ws/{sid}"
    final_text_parts: list[str] = []
    tool_calls = 0
    event_log: list[dict] = []
    done = False
    error_msg = ""

    try:
        async with websockets.connect(ws_url, max_size=None) as ws:
            await ws.send(json.dumps({"type": "message", "text": prompt}))
            while not done:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=TURN_TIMEOUT)
                except asyncio.TimeoutError:
                    error_msg = "turn_timeout"
                    break
                evt = json.loads(raw)
                t = evt.get("type", "")
                # keep a trimmed event log for debugging
                event_log.append({k: v for k, v in evt.items() if k != "messages"})
                if t == "agent:text":
                    final_text_parts.append(evt.get("delta", ""))
                elif t == "agent:tool_call":
                    tool_calls += 1
                elif t == "agent:done":
                    done = True
                elif t == "error":
                    error_msg = evt.get("message", "error")
                    break
                elif t == "agent:permission_requested":
                    # auto-deny permission prompts so the harness doesn't hang
                    req_id = evt.get("id") or evt.get("req_id") or ""
                    if req_id:
                        await ws.send(json.dumps({"type": "deny_permission", "id": req_id}))
    except Exception as e:
        error_msg = f"ws_error:{type(e).__name__}:{e}"

    elapsed = time.time() - started
    final = "".join(final_text_parts).strip()
    return {
        "provider": provider,
        "qid": qid,
        "session_id": sid,
        "final": final,
        "tool_calls": tool_calls,
        "elapsed_s": round(elapsed, 1),
        "error": error_msg,
        "done": done,
        "event_count": len(event_log),
    }


def main():
    if len(sys.argv) < 4:
        print("usage: harness.py <provider> <qid> <prompt>", file=sys.stderr)
        sys.exit(2)
    provider, qid, prompt = sys.argv[1], sys.argv[2], sys.argv[3]
    result = asyncio.run(run_one(provider, qid, prompt))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
