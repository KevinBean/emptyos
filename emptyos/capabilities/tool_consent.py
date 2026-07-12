"""Tool consent manager — permission gate for agent tool calls.

Shape-for-shape parallel to `CloudConsentManager` but per-(session, tool)
instead of per-provider. An agent session is a trust boundary: approving a
tool once during a session means "I trust the model to use this tool in this
conversation" — not "trust this tool globally, forever."

Approval options:
    once    — one-shot approval, re-prompt next call
    session — approve for the rest of this session's calls to this tool
    (no "always" — deliberately absent; matches the with-you-not-for-you principle)

Policies (set via kernel settings `agent.tool_policy`):
    ask  — prompt on every non-session-approved call (default)
    auto — auto-approve everything (useful for scripted CI runs)
    deny — reject everything (kill switch)

Per-tool policy defaults come from `Tool.permission` (auto | ask | deny).
`auto` tools skip the prompt; `ask` tools go through this manager; `deny`
tools are rejected before the manager even sees them.

In CLI mode a `PermissionUI` callable can be attached — it fires instead of
the event emit, so the terminal can block on a local prompt without the
daemon. In daemon/web mode, requests surface as `agent:permission_requested`
events and are resolved via `/api/agent/permission/{id}/{approve|deny}`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from emptyos.kernel.event_bus import EventBus


Policy = Literal["ask", "auto", "deny"]
Scope = Literal["once", "session"]


@dataclass
class _PendingToolRequest:
    id: str
    session_id: str
    tool: str
    input: dict
    summary: str
    future: asyncio.Future
    created_at: float
    extra: dict = field(default_factory=dict)


# A PermissionUI is an optional in-process handler (for `eos chat` in local
# mode). It takes the pending request and returns (approved: bool, scope).
# When present, it fires instead of the event emit — the terminal blocks
# inline without a daemon round-trip.
PermissionUI = Callable[[_PendingToolRequest], Awaitable[tuple[bool, Scope]]]


class ToolConsentManager:
    """Decides whether an agent tool call may proceed.

    One instance per kernel. The agent loop checks with this manager before
    dispatching each tool_use that isn't class-level `auto`.
    """

    DEFAULT_TIMEOUT_SECONDS = 300  # tools wait longer than cloud consent — user may walk away

    def __init__(
        self,
        policy: Policy = "ask",
        events: EventBus | None = None,
        ui: PermissionUI | None = None,
    ):
        self.policy = policy if policy in ("ask", "auto", "deny") else "ask"
        self.events = events
        self.ui = ui
        # Session-scoped approvals: (session_id, tool_name) set
        self._session_approved: set[tuple[str, str]] = set()
        # In-flight requests by id
        self._pending: dict[str, _PendingToolRequest] = {}
        # Latest decision per (session_id, tool_name) — for UI display
        self._last_decision: dict[tuple[str, str], dict[str, Any]] = {}

    def set_policy(self, policy: Policy):
        if policy in ("ask", "auto", "deny"):
            self.policy = policy

    def set_ui(self, ui: PermissionUI | None):
        """Attach or remove the in-process UI handler (for local-mode CLI)."""
        self.ui = ui

    def reset_session(self, session_id: str):
        """Clear all approvals for a session (e.g. on session close)."""
        self._session_approved = {(s, t) for (s, t) in self._session_approved if s != session_id}

    async def check(
        self,
        *,
        session_id: str,
        tool: str,
        input: dict | None = None,
        summary: str = "",
        tool_default: Policy = "ask",
        timeout: float | None = None,
    ) -> bool:
        """Return True if this tool call may proceed.

        `tool_default` is the tool class's declared permission. When the tool
        declares `auto`, this short-circuits true without prompting. `deny`
        short-circuits false.
        """
        input = input or {}

        # Hard kill switch — trumps everything, including auto tools
        if self.policy == "deny":
            self._last_decision[(session_id, tool)] = {
                "decision": "denied",
                "reason": "policy=deny",
                "at": time.time(),
            }
            return False

        # Tool says "deny" → never allow
        if tool_default == "deny":
            self._last_decision[(session_id, tool)] = {
                "decision": "denied",
                "reason": "tool=deny",
                "at": time.time(),
            }
            return False

        # Tool says "auto" → skip the prompt gate (policy was already checked above)
        if tool_default == "auto":
            return True

        # Global auto (e.g. CI scripted runs)
        if self.policy == "auto":
            return True

        # Session-level approval short-circuit
        if (session_id, tool) in self._session_approved:
            return True

        # Otherwise: ask.
        req_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        pending = _PendingToolRequest(
            id=req_id,
            session_id=session_id,
            tool=tool,
            input=input,
            summary=summary or f"{tool}(...)",
            future=future,
            created_at=time.time(),
        )
        self._pending[req_id] = pending

        # In-process UI handler (terminal mode) takes precedence over events
        if self.ui is not None:
            try:
                approved, scope = await self.ui(pending)
                self._resolve(pending, approved, scope)
                return bool(approved)
            except Exception as e:
                self._last_decision[(session_id, tool)] = {
                    "decision": "denied",
                    "reason": f"ui error: {e}",
                    "at": time.time(),
                }
                if not future.done():
                    future.set_result(False)
                self._pending.pop(req_id, None)
                return False

        # Daemon/web path: emit event, await approval via HTTP endpoint
        if self.events:
            try:
                await self.events.emit(
                    "agent:permission_requested",
                    {
                        "id": req_id,
                        "session_id": session_id,
                        "tool": tool,
                        "input": input,
                        "summary": pending.summary,
                    },
                    source="tool_consent",
                )
            except Exception:
                pass

        try:
            result = await asyncio.wait_for(
                future,
                timeout=timeout or self.DEFAULT_TIMEOUT_SECONDS,
            )
            return bool(result)
        except TimeoutError:
            self._last_decision[(session_id, tool)] = {
                "decision": "denied",
                "reason": "timeout",
                "at": time.time(),
            }
            return False
        finally:
            self._pending.pop(req_id, None)

    def approve(self, request_id: str, *, scope: Scope = "once") -> bool:
        """Approve a pending request. `scope=session` remembers for this session."""
        pending = self._pending.get(request_id)
        if not pending:
            return False
        return self._resolve(pending, True, scope)

    def deny(self, request_id: str) -> bool:
        pending = self._pending.get(request_id)
        if not pending:
            return False
        return self._resolve(pending, False, "once")

    def _resolve(self, pending: _PendingToolRequest, approved: bool, scope: Scope) -> bool:
        key = (pending.session_id, pending.tool)
        if approved and scope == "session":
            self._session_approved.add(key)
        self._last_decision[key] = {
            "decision": "approved" if approved else "denied",
            "scope": scope,
            "at": time.time(),
        }
        if not pending.future.done():
            pending.future.set_result(approved)

        # Broadcast resolution for UIs that need to close their modals
        if self.events:
            try:
                import asyncio as _a

                loop = _a.get_event_loop()
                if loop.is_running():
                    loop.create_task(
                        self.events.emit(
                            "agent:permission_resolved",
                            {
                                "id": pending.id,
                                "session_id": pending.session_id,
                                "tool": pending.tool,
                                "approved": approved,
                                "scope": scope,
                            },
                            source="tool_consent",
                        )
                    )
            except Exception:
                pass

        return True

    def pending_list(self, session_id: str | None = None) -> list[dict]:
        out = []
        for p in self._pending.values():
            if session_id and p.session_id != session_id:
                continue
            out.append(
                {
                    "id": p.id,
                    "session_id": p.session_id,
                    "tool": p.tool,
                    "input": p.input,
                    "summary": p.summary,
                    "created_at": p.created_at,
                }
            )
        return out

    def status(self) -> dict:
        return {
            "policy": self.policy,
            "approved": sorted(f"{s}::{t}" for (s, t) in self._session_approved),
            "pending": self.pending_list(),
            "last_decisions": {f"{s}::{t}": d for (s, t), d in self._last_decision.items()},
        }
