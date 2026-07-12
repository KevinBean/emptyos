"""viz — SSE generate + iterate event streams.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: All ndjson streaming surfaces: live token streaming for generate, the agent-driven (claude-cli) iterate path, and the think-based iterate fallback for deployments without agent-runtime..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._system_for / self._think_html_stream / self._reject_reason / self._persist (generation).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from emptyos.sdk import web_route, ndjson_response
from .shared import PRESETS, VIZ_ITERATE_SYSTEM, _shape_min_ability, _shape_max_tokens, _extract_html, _looks_like_html, _looks_truncated, _rewrite_user_msg, _new_id
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import VizApp  # noqa: F401 — for type hints only


# ─── Bind to VizApp class as ────────────────────────────────
#   _generate_stream_events    = _streaming._generate_stream_events
#   api_generate_stream        = _streaming.api_generate_stream
#   _iterate_via_think_stream  = _streaming._iterate_via_think_stream
#   _iterate_stream_events     = _streaming._iterate_stream_events
#   api_iterate_stream         = _streaming.api_iterate_stream
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def _generate_stream_events(
    self,
    prompt: str,
    *,
    shape: str | None = None,
    examples: list[str] | None = None,
):
    """Concrete streaming impl used by api_generate_stream. Tracks the
    full accumulated text alongside the flushed buffer so the final
    validation + persist step has the whole document.
    """
    import time

    prompt = (prompt or "").strip()
    shape = shape or self.app_config("default_shape", "3d-scene")
    if not prompt:
        yield {"type": "error", "error": "prompt is required"}
        return
    if shape not in PRESETS:
        yield {"type": "error", "error": f"unknown shape '{shape}' (have: {sorted(PRESETS)})"}
        return

    system = self._system_for(shape)
    examples_block = await self._build_examples_block(examples or [], shape)
    if examples_block:
        system = system + examples_block

    yield {"type": "started", "shape": shape, "examples_used": len(examples or [])}

    t0 = time.monotonic()
    last_emit = t0
    full_parts: list[str] = []
    pending: list[str] = []
    total_bytes = 0
    try:
        async for text, _done in self._think_html_stream(
            system, prompt, min_ability=_shape_min_ability(shape), max_tokens=_shape_max_tokens(shape)
        ):
            full_parts.append(text)
            pending.append(text)
            total_bytes += len(text.encode("utf-8"))
            now = time.monotonic()
            if (now - last_emit) >= 0.1:
                last_emit = now
                yield {
                    "type": "chunk",
                    "text": "".join(pending),
                    "bytes": total_bytes,
                    "elapsed_ms": int((now - t0) * 1000),
                }
                pending.clear()
    except Exception as exc:
        yield {"type": "error", "error": f"think_stream failed: {exc}"}
        return

    if pending:
        yield {
            "type": "chunk",
            "text": "".join(pending),
            "bytes": total_bytes,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }

    raw = "".join(full_parts)
    html = _extract_html(raw)
    reason = self._reject_reason(html)
    if reason:
        yield {"type": "error", "error": reason}
        return

    rid = _new_id()
    meta = await self._persist(rid, html, prompt, shape, is_update=False)
    await self.emit("viz:created", {"id": rid, "shape": shape})
    yield {"type": "done", **meta, "elapsed_ms": int((time.monotonic() - t0) * 1000)}


@web_route("POST", "/api/generate-stream")
async def api_generate_stream(self, request):
    body = await request.json()
    return ndjson_response(self._generate_stream_events(
        body.get("prompt", ""),
        shape=body.get("shape"),
        examples=body.get("examples") or [],
    ))


async def _iterate_via_think_stream(
    self, rid: str, prompt: str, *, shape: str, prior_prompt: str, html_path,
):
    """Fallback iterate path for deployments without agent-runtime/claude-cli
    (cloud, demo). Rewrites the whole file via the app's configured think
    provider and streams the same event shape the agent path emits, so the
    frontend's `consumeNdjson` handler works unchanged.

    This is the streaming sibling of `api_iterate`. The agent path is
    preferred where available (surgical edits, no output-budget risk); this
    path regenerates the full file, so the truncation guard matters more.
    """
    import time

    t0 = time.monotonic()
    yield {"type": "started", "shape": shape, "iterate": True, "agent": False}
    yield {
        "type": "chunk",
        "text": (
            "No agentic CLI on this deployment — rewriting the full "
            "artifact via the configured model. Larger scenes may hit the "
            "output limit; regenerate instead if it truncates.\n\n"
        ),
        "elapsed_ms": 0,
    }

    try:
        prior_html = html_path.read_text(encoding="utf-8")
    except Exception as exc:
        yield {"type": "error", "error": f"failed to read artifact: {exc}"}
        return

    user_msg = _rewrite_user_msg(prior_prompt, prompt, prior_html)

    acc = ""
    last_report = 0
    try:
        async for text, _done in self._think_html_stream(
            self._system_for(shape), user_msg, min_ability=_shape_min_ability(shape),
            max_tokens=_shape_max_tokens(shape),
        ):
            acc += text
            kb = len(acc.encode("utf-8")) / 1024
            if kb - last_report >= 2:  # progress ping every ~2 KB, not raw HTML
                last_report = kb
                yield {
                    "type": "chunk",
                    "text": f"…generating ({kb:.1f} KB)\n",
                    "elapsed_ms": int((time.monotonic() - t0) * 1000),
                }
    except Exception as exc:
        yield {"type": "error", "error": f"think failed: {str(exc)[:200]}"}
        return

    html = _extract_html(acc)
    reason = self._reject_reason(html)
    if reason:
        yield {"type": "error", "error": reason}
        return

    meta = await self._persist(rid, html, prompt, shape, is_update=True)
    await self.emit("viz:updated", {"id": rid, "shape": shape})
    yield {"type": "done", **meta, "elapsed_ms": int((time.monotonic() - t0) * 1000)}


async def _iterate_stream_events(self, rid: str, prompt: str):
    """Agent-driven iterate. Spawns claude-cli with Read+Edit tools
    scoped to the artifact's record dir; the CLI reads scene.html,
    makes minimal surgical edits, writes them back. Events stream as
    they happen (tool_use / tool_result / text chunks) so the user
    sees what the agent is doing live.

    Why this shape (not the prior 'rewrite the whole file via
    think_stream' approach): iteration is an *edit*, not a regenerate.
    Asking the LLM to emit the whole 12-18 KB file every time blew
    the 8192-token output budget and lost surgical precision. The
    agent path edits in place — only the diff costs output tokens,
    truncation is impossible because the agent reads-then-edits in
    small chunks, and unrelated code is preserved by construction.

    Event shape:
        {"type": "started", "shape", "iterate": True, "agent": True}
        {"type": "chunk", "text": "..."}              # agent's prose
        {"type": "tool_use", "tool": "Read"|"Edit"|"Glob", "input": {...}}
        {"type": "tool_result", "tool_use_id": "...", "preview": "..."}
        {"type": "done", "id", "html_path", "size_kb", "elapsed_ms"}
        {"type": "error", "error": "..."}
    """
    import asyncio
    import json
    import time

    rid = (rid or "").strip()
    prompt = (prompt or "").strip()
    if not rid or not prompt:
        yield {"type": "error", "error": "id and prompt are required"}
        return

    record_dir = self._record_dir(rid)
    html_path = record_dir / "scene.html"
    if not html_path.exists():
        yield {"type": "error", "error": f"no artifact with id '{rid}'"}
        return

    existing = self.vault_get_properties(self._rel_record(rid)) or {}
    shape = existing.get("shape", "3d-scene")
    prior_prompt = existing.get("prompt", "")

    try:
        runtime = self.require("agent-runtime")
    except Exception:
        # No agent-runtime (e.g. cloud/demo deployments without claude-cli).
        # Fall back to a think-based whole-file rewrite using the app's
        # configured provider (gpt-4o-mini etc.) so iterate still works.
        async for evt in self._iterate_via_think_stream(
            rid, prompt, shape=shape, prior_prompt=prior_prompt, html_path=html_path,
        ):
            yield evt
        return

    # Scoped system prompt — agent only knows about this one file and
    # is told *not* to rewrite it. Mirrors the rooms CLI pattern but
    # narrower: this is an editor, not a chat participant.
    system_prompt = VIZ_ITERATE_SYSTEM.format(shape=shape, prior_prompt=prior_prompt)

    yield {"type": "started", "shape": shape, "iterate": True, "agent": True}

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_line(raw: bytes) -> None:
        try:
            evt = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, evt)
        except RuntimeError:
            pass

    async def driver():
        try:
            result = await runtime.claude_cli_run(
                prompt=prompt,
                system_prompt=system_prompt,
                allowed_tools="Read,Edit,Glob",
                cwd=str(record_dir),
                on_stdout_line=on_line,
                timeout_s=300.0,
            )
            if isinstance(result, dict) and "error" in result:
                await queue.put({"_error": result["error"]})
            else:
                await queue.put({"_done": True})
        except Exception as e:
            await queue.put({"_error": str(e)[:200]})
        finally:
            await queue.put(None)

    t0 = time.monotonic()
    task = asyncio.create_task(driver())
    try:
        while True:
            evt = await queue.get()
            if evt is None:
                break
            if "_error" in evt:
                yield {"type": "error", "error": evt["_error"]}
                continue
            if "_done" in evt:
                continue
            etype = evt.get("type")
            elapsed = int((time.monotonic() - t0) * 1000)
            if etype == "assistant":
                for block in (evt.get("message", {}) or {}).get("content", []) or []:
                    btype = block.get("type")
                    if btype == "text":
                        t = block.get("text", "")
                        if t:
                            yield {"type": "chunk", "text": t, "elapsed_ms": elapsed}
                    elif btype == "tool_use":
                        yield {
                            "type": "tool_use",
                            "tool": block.get("name") or "?",
                            "input": block.get("input") or {},
                            "id": block.get("id"),
                            "elapsed_ms": elapsed,
                        }
            elif etype == "user":
                for block in (evt.get("message", {}) or {}).get("content", []) or []:
                    if block.get("type") == "tool_result":
                        content = block.get("content")
                        if isinstance(content, list):
                            content = " ".join(
                                str(c.get("text", c)) for c in content if c
                            )
                        preview = str(content)[:300] if content else ""
                        yield {
                            "type": "tool_result",
                            "tool_use_id": block.get("tool_use_id"),
                            "preview": preview,
                            "elapsed_ms": elapsed,
                        }
    finally:
        try:
            await task
        except Exception:
            pass

    # Agent has exited. Read the file from disk, validate, persist record.md.
    try:
        new_html = html_path.read_text(encoding="utf-8")
    except Exception as exc:
        yield {"type": "error", "error": f"failed to read edited file: {exc}"}
        return
    if not _looks_like_html(new_html):
        yield {"type": "error", "error": "edited file no longer looks like HTML"}
        return
    truncated, t_why = _looks_truncated(new_html)
    if truncated:
        yield {"type": "error", "error": f"edited file appears truncated: {t_why}"}
        return
    ok, why = self._check_size(new_html)
    if not ok:
        yield {"type": "error", "error": why}
        return

    meta = await self._persist(rid, new_html, prompt, shape, is_update=True)
    await self.emit("viz:updated", {"id": rid, "shape": shape})
    yield {"type": "done", **meta, "elapsed_ms": int((time.monotonic() - t0) * 1000)}


@web_route("POST", "/api/iterate-stream")
async def api_iterate_stream(self, request):
    body = await request.json()
    return ndjson_response(self._iterate_stream_events(
        (body.get("id") or "").strip(),
        (body.get("prompt") or "").strip(),
    ))
