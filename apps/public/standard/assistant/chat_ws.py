"""Assistant — main WebSocket chat handler.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the long-running `/ws/{session_id}` loop that drives a
streaming chat turn end-to-end — receives the user message + attachments,
extracts files, routes slash / research / tool-use, streams provider
chunks, persists on stream end, and emits ``assistant:message``.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules:
  - slash.py:    self._handle_slash
  - context.py:  self._build_context / self._build_system / self._build_chat_messages
  - tools.py:    self._chat_with_tools / self._use_tools_default
  - research_ws.py: self._run_research_ws
  - sessions.py: self._get_session / self._add_message / self._auto_name / self._sessions_lock / self._export_session
  - spine:       self._pick_provider_label / self._cancel_flags
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from emptyos.sdk import ws_route

from .files import DEFAULT_MAX_CHARS as FILES_DEFAULT_MAX_CHARS
from .files import extract_file, format_block
from .prompts import FALLBACK_THINK_TIMEOUT
from .vision import resolve_images

if TYPE_CHECKING:
    from .app import AssistantApp  # noqa: F401 — for type hints only


# ─── Bind to AssistantApp class as ──────────────────────────────────
#   ws_chat = _chat_ws.ws_chat
# Module-level constant (used only here):
#   DEFAULT_VISION_PROVIDER
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


# When images are attached but the user is on "auto" (or a non-vision tier),
# pin to this provider so the request hits a vision-capable model. Override
# via [apps.assistant] vision_provider = "openai".
DEFAULT_VISION_PROVIDER = "openai-mini"


@ws_route("/ws/{session_id}")
async def ws_chat(self, websocket):
    """Main chat WebSocket — streaming AI responses."""
    session_id = websocket.path_params.get("session_id", "")
    session = self._get_session(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "Session not found"})
        return

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")

            if msg_type == "message":
                text = (data.get("text") or "").strip()
                image_paths = [
                    p for p in (data.get("images") or []) if isinstance(p, str) and p
                ]
                file_paths = [
                    p for p in (data.get("files") or []) if isinstance(p, str) and p
                ]
                if not text and not image_paths and not file_paths:
                    continue
                # Capture freshness before persisting this turn. The recalled
                # block is injected once, on the first turn only, and never
                # written into chat history.
                is_first_turn = not bool(session.get("messages"))
                episodic_block = ""
                if is_first_turn:
                    try:
                        episodic_block = await self._cross_session_recall_block(
                            session_id, text
                        )
                    except Exception:
                        episodic_block = ""
                # Extract attached files (PDF/docx/txt/md). Persist the
                # user's typed text untouched; only the augmented version
                # (file content prepended) goes to the model so chat history
                # stays human-readable.
                file_extracts: list[str] = []
                if file_paths:
                    vault_root = self.kernel.config.notes_path or ""
                    max_chars = int(
                        self.app_config("max_file_chars", FILES_DEFAULT_MAX_CHARS)
                    )
                    any_truncated = False
                    any_errors: list[str] = []
                    for fp in file_paths:
                        ex = extract_file(vault_root, fp, max_chars=max_chars)
                        file_extracts.append(format_block(ex))
                        if ex.truncated:
                            any_truncated = True
                        if ex.error:
                            any_errors.append(f"{ex.name}: {ex.error}")
                    if any_truncated:
                        await websocket.send_json(
                            {
                                "type": "agent-reply",
                                "agent": "system",
                                "text": (
                                    "⚠ Some attached files exceeded the per-file "
                                    f"character cap ({max_chars:,}) and were truncated."
                                ),
                            }
                        )
                    if any_errors:
                        await websocket.send_json(
                            {
                                "type": "agent-reply",
                                "agent": "system",
                                "text": "⚠ File extraction errors: " + "; ".join(any_errors),
                            }
                        )
                # Resolve vault image paths to data URLs once; if anything
                # dropped (missing/oversized), tell the user.
                image_urls: list[str] = []
                if image_paths:
                    vault_root = self.kernel.config.notes_path or ""
                    image_urls = resolve_images(vault_root, image_paths)
                    dropped = len(image_paths) - len(image_urls)
                    if dropped:
                        await websocket.send_json(
                            {
                                "type": "agent-reply",
                                "agent": "system",
                                "text": (
                                    f"⚠ {dropped} image{'s' if dropped > 1 else ''} "
                                    "could not be loaded (missing, empty, or larger than 12 MB)."
                                ),
                            }
                        )

                # Save user message
                async with self._sessions_lock:
                    self._add_message(session_id, "user", text)

                # Streaming /research is handled inline (the normal slash
                # dispatcher returns a single string and can't stream).
                if text.startswith("/research"):
                    query = text[len("/research"):].strip()
                    if not query:
                        await websocket.send_json(
                            {
                                "type": "agent-reply",
                                "agent": "system",
                                "text": "Usage: `/research <question>`",
                            }
                        )
                        continue
                    await self._run_research_ws(websocket, session_id, query)
                    continue

                # Check slash commands first
                if text.startswith("/"):
                    result = await self._handle_slash(text)
                    if result == "__EXPORT__":
                        export = await self._export_session(session_id)
                        if export.get("ok"):
                            result = f"Session exported to `{export['filename']}`"
                        else:
                            result = f"Export failed: {export.get('error', 'unknown')}"
                    if result is not None:
                        await websocket.send_json(
                            {"type": "agent-reply", "agent": "system", "text": result}
                        )
                        async with self._sessions_lock:
                            self._add_message(session_id, "assistant", result, agent="system")
                        continue

                # Reload session for latest messages
                session = self._get_session(session_id) or session

                # Tool-use retrieval path — non-streaming, replaces the
                # standard stream below. Per-message flag wins; otherwise
                # falls back to the assistant.use_tools setting default.
                use_tools = bool(data.get("use_tools", self._use_tools_default()))
                if use_tools:
                    await websocket.send_json({"type": "agent-thinking", "agent": "tools"})
                    _stream_text = ""

                    async def _stream_to_ws(ev_type: str, ev_data: dict):
                        nonlocal _stream_text
                        try:
                            if ev_type == "agent:text":
                                _stream_text += ev_data.get("delta", "")
                                await websocket.send_json(
                                    {
                                        "type": "agent-stream",
                                        "agent": "tools",
                                        "text": _stream_text,
                                    }
                                )
                            elif ev_type == "agent:tool_call":
                                await websocket.send_json(
                                    {
                                        "type": "agent-status",
                                        "agent": "tools",
                                        "status": f"using {ev_data.get('name', 'tool')}",
                                        "tool": ev_data.get("name", ""),
                                    }
                                )
                        except Exception:
                            pass

                    try:
                        tool_text, tool_prov, tool_log = await self._chat_with_tools(
                            text,
                            session,
                            on_event=_stream_to_ws,
                            episodic_context=episodic_block,
                        )
                    except Exception as e:
                        tool_text, tool_prov, tool_log = f"Tool-use error: {e}", "error", []
                    label = f"tools:{tool_prov}"
                    await websocket.send_json(
                        {
                            "type": "agent-reply",
                            "agent": label,
                            "text": tool_text,
                            "tool_calls": tool_log,
                        }
                    )
                    await websocket.send_json({"type": "agent-done"})
                    async with self._sessions_lock:
                        self._add_message(session_id, "assistant", tool_text, agent=label)
                    await self.emit(
                        "assistant:message", {"session": session_id, "provider": label}
                    )
                    if session.get("name", "").startswith("New chat"):
                        self.spawn_background(self._auto_name(session_id, text, websocket))
                    continue

                provider, explicit_backend = self._pick_provider_label(session)

                # When images are attached, the chosen provider must support
                # vision. "auto" and non-vision tiers are silently rerouted
                # to a configured vision-capable provider so the request
                # actually answers instead of falling through the chain.
                pinned_provider: str | None = None
                if image_urls:
                    vision_pin = (
                        self.app_config("vision_provider", DEFAULT_VISION_PROVIDER)
                        or DEFAULT_VISION_PROVIDER
                    )
                    if provider in ("auto", "ollama", "claude-cli") or not provider:
                        pinned_provider = vision_pin
                        provider = vision_pin
                        explicit_backend = False
                    else:
                        pinned_provider = provider

                # Build prompt with context
                await websocket.send_json({"type": "agent-thinking", "agent": provider})

                # When the caller passes ``context: false`` (extension side
                # panel does this when browser tabs are shared into the
                # turn), skip vault keyword-grep — the page content the
                # user wants Aura to read is already in the user message,
                # and pulling unrelated vault notes that share keywords
                # confuses the model (e.g. "show" → wrong TV-show note).
                # Two-phase companion (dark): answer fast from a scoped slice,
                # verify against the whole vault concurrently. When the flag is
                # off — or no usable scoped slice — this falls to today's path.
                verify_task = None
                prelim_provider = ""
                scoped_paths: list = []
                want_ctx = data.get("context", True)
                if want_ctx and self.app_config("feature.companion-twophase.enabled", False):
                    scoped = None
                    try:
                        scoped, scoped_paths = await self._scoped_context(text, session=session)
                    except Exception:
                        scoped, scoped_paths = None, []
                    if scoped is not None:
                        context = scoped
                        # The fast answer comes from a narrow, relevant slice — a
                        # bounded grounded-Q&A task a cheaper model handles well.
                        # Phase 2 (_full_answer) keeps the normal chain, so the
                        # authoritative answer stays on the better model.
                        prelim_provider = str(
                            self.app_config("companion.preliminary_provider", "") or ""
                        )
                        verify_task = asyncio.create_task(
                            self._full_answer(text, session=session)
                        )
                        try:
                            await websocket.send_json({"type": "verify-checking"})
                        except Exception:
                            pass
                    else:
                        context = await self._build_context(text, session=session)
                elif want_ctx:
                    context = await self._build_context(text, session=session)
                else:
                    context = ""

                system = await self._build_system(session)
                if episodic_block:
                    system += "\n\n" + episodic_block

                chat_messages = self._build_chat_messages(session, vault_context=context)
                if file_extracts and chat_messages and chat_messages[-1]["role"] == "user":
                    chat_messages[-1] = {
                        "role": "user",
                        "content": (
                            "\n\n".join(file_extracts)
                            + "\n\n"
                            + chat_messages[-1]["content"]
                        ),
                    }

                # Stream response with cancel support
                full_text = ""
                original_provider = provider
                self._cancel_flags[session_id] = False
                think_kwargs: dict = {
                    "messages": chat_messages,
                    "system": system,
                    "domain": "text",
                }
                if image_urls:
                    think_kwargs["images"] = image_urls
                if pinned_provider:
                    think_kwargs["provider"] = pinned_provider
                # Cheap fast preliminary: only in auto mode (respect an explicit
                # user provider choice) and never on a vision turn (needs its pin).
                elif prelim_provider and not image_urls and not explicit_backend:
                    think_kwargs["provider"] = prelim_provider
                try:
                    async for chunk in self.think_stream(**think_kwargs):
                        if self._cancel_flags.get(session_id):
                            self._cancel_flags.pop(session_id, None)
                            break

                        if "provider_used" in chunk:
                            used = chunk["provider_used"]
                            if used and used != provider:
                                await websocket.send_json(
                                    {
                                        "type": "provider-resolved",
                                        "from": original_provider,
                                        "to": used,
                                        "was_switch": explicit_backend,
                                    }
                                )
                                provider = used
                            continue

                        if "tool_status" in chunk:
                            await websocket.send_json(
                                {
                                    "type": "agent-status",
                                    "agent": provider,
                                    "status": chunk["tool_status"],
                                    "tool": chunk.get("tool", ""),
                                }
                            )
                            continue

                        if "usage" in chunk:
                            await websocket.send_json(
                                {
                                    "type": "agent-usage",
                                    "agent": provider,
                                    **chunk["usage"],
                                }
                            )
                            continue

                        delta = chunk.get("text", "")
                        if delta:
                            full_text += delta
                            await websocket.send_json(
                                {
                                    "type": "agent-stream",
                                    "agent": provider,
                                    "text": full_text,
                                }
                            )
                except Exception as stream_err:
                    # Streaming chain exhausted — fall back to the blocking
                    # think() which walks the default chain. Skip retrying
                    # the same provider that just failed.
                    self.log_warn(f"stream failed: {type(stream_err).__name__}: {stream_err}")
                    if not full_text:
                        try:
                            await websocket.send_json(
                                {
                                    "type": "agent-status",
                                    "agent": provider,
                                    "status": "Streaming failed — retrying without streaming…",
                                }
                            )
                        except Exception:
                            pass
                        fb_kwargs: dict = {
                            "messages": chat_messages,
                            "system": system,
                            "domain": "text",
                        }
                        if image_urls:
                            fb_kwargs["images"] = image_urls
                        try:
                            full_text = await asyncio.wait_for(
                                self.think(**fb_kwargs),
                                timeout=FALLBACK_THINK_TIMEOUT,
                            )
                        except TimeoutError:
                            full_text = (
                                f"Error: provider did not respond within {FALLBACK_THINK_TIMEOUT:.0f}s. "
                                "Try again or switch backend in settings."
                            )
                            self.log_error(
                                f"think fallback timed out after {FALLBACK_THINK_TIMEOUT:.0f}s"
                            )
                        except Exception as e:
                            full_text = f"Error: {e}"
                            self.log_error(f"think fallback failed: {type(e).__name__}: {e}")
                finally:
                    self._cancel_flags.pop(session_id, None)
                    # Persist FIRST, before the final WS sends. If the WS
                    # has dropped mid-stream, the send_json calls below
                    # raise and the assistant text would otherwise vanish
                    # on refresh — user msg saved at line 500, reply lost.
                    if full_text:
                        try:
                            async with self._sessions_lock:
                                self._add_message(
                                    session_id, "assistant", full_text, agent=provider
                                )
                        except Exception as _persist_err:
                            self.log_warn(f"persist on stream-end failed: {_persist_err}")

                try:
                    await websocket.send_json(
                        {"type": "agent-reply", "agent": provider, "text": full_text}
                    )
                    await websocket.send_json({"type": "agent-done"})
                except Exception:
                    pass  # WS already gone — message is already persisted above

                # Phase 2 reconcile (two-phase companion): the fast answer above
                # came from a scoped slice. Compare it against the always-on
                # whole-vault answer, RESOLVED BY SOURCE-NOTE RECENCY (not by which
                # scope). consistent/prefer_scoped → fast answer stands; prefer_full
                # → whole-vault corrects it; conflict → surface both, pick neither.
                if verify_task is not None:
                    try:
                        full_v, _, full_paths = await asyncio.wait_for(
                            verify_task, timeout=FALLBACK_THINK_TIMEOUT
                        )
                        differ = (full_v or "").strip() and full_v.strip() != (full_text or "").strip()
                        verdict = await self._reconcile(
                            text, full_text, full_v, scoped_paths, full_paths
                        ) if differ else "consistent"
                        if verdict == "prefer_full" and differ:
                            try:
                                async with self._sessions_lock:
                                    self._add_message(
                                        session_id, "assistant", full_v, agent="verified",
                                    )
                            except Exception as _pe:
                                self.log_warn(f"persist verify-correction failed: {_pe}")
                            await websocket.send_json({
                                "type": "verify-correction", "agent": provider, "text": full_v,
                                "sources": self._provenance_items(full_paths, text),
                                "stale_paths": scoped_paths, "source_paths": full_paths,
                            })
                        elif verdict == "conflict" and differ:
                            await websocket.send_json({
                                "type": "verify-conflict",
                                "scoped": full_text, "full": full_v,
                                "scoped_sources": self._provenance_items(scoped_paths, text),
                                "full_sources": self._provenance_items(full_paths, text),
                                "scoped_paths": scoped_paths, "full_paths": full_paths,
                            })
                        else:
                            # consistent or prefer_scoped → the fast answer is current
                            await websocket.send_json({
                                "type": "verify-ok",
                                "confirmed": verdict == "prefer_scoped",
                            })
                    except Exception as _ve:
                        verify_task.cancel()
                        try:
                            await websocket.send_json({"type": "verify-ok"})
                        except Exception:
                            pass

                await self.emit(
                    "assistant:message", {"session": session_id, "provider": provider}
                )

                # Auto-name session after first exchange
                if session.get("name", "").startswith("New chat"):
                    self.spawn_background(self._auto_name(session_id, text, websocket))

            elif msg_type == "cancel":
                self._cancel_flags[session_id] = True

            elif msg_type == "set-backend":
                self.db.execute(
                    "UPDATE sessions SET backend = ? WHERE id = ?",
                    (data.get("backend", "auto"), session_id),
                )
                self.db.commit()
                session = self._get_session(session_id) or session

            elif msg_type == "set-system-prompt":
                self.db.execute(
                    "UPDATE sessions SET system_prompt = ? WHERE id = ?",
                    (data.get("system_prompt", ""), session_id),
                )
                self.db.commit()
                session = self._get_session(session_id) or session

    except Exception as e:
        # Client disconnected or unexpected WS error — log so silent hangs
        # on the UI have a trace in syslog.
        if not isinstance(e, (asyncio.CancelledError,)):
            try:
                self.log_warn(f"assistant ws loop ended: {type(e).__name__}: {e}")
            except Exception:
                pass
