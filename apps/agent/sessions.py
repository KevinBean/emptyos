"""Session management mixin for AgentApp.

Groups session storage (create/get/append/persist/load via ChatSessionStore),
per-session edit-stack (push + bulk revert), per-session run_turn limit
overrides, and the vault archive flow. Extracted from app.py to keep
the main app file focused on the turn loop, transports, and slash commands.

Everything here is stateless on the module — all state lives on the AgentApp
instance (`self._sessions`, `self._edit_stacks`, `self._edit_limits`,
`self._iter_limits`).
"""

from __future__ import annotations

import json

from emptyos.sdk.agent_loop import DEFAULT_MAX_ITERS, EDIT_PATH_LIMIT

from apps.agent.prompts import SESSION_ARCHIVE_PROMPT, SESSION_ARCHIVE_SYSTEM


class SessionMixin:
    """Session storage + archive + edit-stack for AgentApp.

    Requires the host class to have run `setup()` such that these attributes
    exist: `self._sessions` (ChatSessionStore), `self._edit_stacks`,
    `self._edit_limits`, `self._iter_limits`.
    """

    # ── Session storage (ChatSessionStore passthrough) ────────────

    def _create_session(self, name: str = "", provider: str = "") -> dict:
        default_provider = provider or self._default_provider_name()
        return self._sessions.create_session(
            name=name, extras={"provider": default_provider},
        )

    def _get_session(self, sid: str) -> dict | None:
        return self._sessions.get_session(sid)

    def _append_message(self, sid: str, role: str, content, provider_kind: str):
        self._sessions.append_message(
            sid, role, content, extras={"provider_kind": provider_kind},
        )

    def _persist_message(self, sid: str, message: dict, provider_kind: str):
        """Persist the FULL message dict (role + content + any provider-specific
        fields like `tool_calls` or `tool_call_id`). Needed because OpenAI requires
        matching tool_calls ↔ tool messages across turn boundaries — dropping those
        fields on save causes a 400 on the next user turn.
        """
        role = message.get("role", "")
        rest = {k: v for k, v in message.items() if k != "role"}
        self._sessions.append_message(
            sid, role, rest, extras={"provider_kind": provider_kind},
        )

    def _load_provider_messages(self, sid: str) -> list[dict]:
        return self._sessions.load_provider_messages(sid)

    # ── Per-session run_turn limit overrides ──────────────────────

    def _edit_limit_for(self, session_id: str) -> int:
        """Per-session override of the edit-loop-guard cap.

        Returns the default EDIT_PATH_LIMIT unless the user has raised it
        via `/grant-edits N` in this session.
        """
        return self._edit_limits.get(session_id, EDIT_PATH_LIMIT)

    def _iter_limit_for(self, session_id: str) -> int:
        """Per-session max_iters for run_turn.

        Precedence: /grant-iters override → agent.max_iters setting → DEFAULT_MAX_ITERS.
        """
        override = self._iter_limits.get(session_id)
        if override is not None:
            return override
        settings = self.service("settings")
        if settings:
            try:
                return int(settings.get("agent.max_iters") or DEFAULT_MAX_ITERS)
            except (TypeError, ValueError):
                pass
        return DEFAULT_MAX_ITERS

    # ── Edit stack (push + bulk revert) ───────────────────────────

    def _push_edit(self, sid: str, entry: dict) -> None:
        """Append to the per-session edit-history stack. Called by run_turn
        whenever Write/Edit succeeds with a `previous_content` display field."""
        if not entry or not entry.get("path"):
            return
        self._edit_stacks.setdefault(sid, []).append(entry)

    def _revert_last_edits(self, sid: str, n: int = 1) -> dict:
        """Pop up to `n` entries from the edit stack and restore each file.
        Shared by the CLI /revert handler and the web REST endpoint — one
        code path, one set of bugs.

        Returns a structured summary: `{reverted: [{path, action, ok, error?}],
        remaining: int, python_edits: bool}`. Python-edit flag tells callers
        to remind the user the daemon still has stale bytecode.
        """
        from pathlib import Path as _P
        stack = self._edit_stacks.get(sid) or []
        if not stack:
            return {"reverted": [], "remaining": 0, "python_edits": False, "empty": True}
        try:
            n = max(1, min(int(n), len(stack)))
        except (TypeError, ValueError):
            n = 1
        reverted: list[dict] = []
        python_edits = False
        for _ in range(n):
            if not stack:
                break
            entry = stack.pop()
            p = _P(entry["path"])
            action = (entry.get("action") or "edit").lower()
            before = entry.get("previous_content", "")
            outcome: dict = {"path": str(p), "action": action, "ok": False}
            try:
                if action == "create":
                    if p.exists():
                        p.unlink()
                    outcome["ok"] = True
                    outcome["mode"] = "deleted"
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(before, encoding="utf-8")
                    outcome["ok"] = True
                    outcome["mode"] = "restored"
                if str(p).endswith(".py"):
                    python_edits = True
            except Exception as e:
                outcome["error"] = f"{type(e).__name__}: {e}"
            reverted.append(outcome)
        self._edit_stacks[sid] = stack
        return {
            "reverted": reverted,
            "remaining": len(stack),
            "python_edits": python_edits,
            "empty": False,
        }

    # ── Vault archive ─────────────────────────────────────────────

    async def _archive_session(self, sid: str) -> dict:
        """Summarise a session via think() and write it to the vault.

        Returns {ok, path, url, note_path} on success, {ok, error} on failure.
        The vault note lands at:
            {vault}/30_Resources/EmptyOS/agent/sessions/{YYYY-MM-DD}-{sid[:8]}.md
        """
        from datetime import datetime, timezone

        session = self._get_session(sid)
        if not session:
            return {"ok": False, "error": "session not found"}

        msgs = session.get("messages", [])
        if not msgs:
            return {"ok": False, "error": "no messages to archive"}

        lines = []
        for m in msgs:
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        t = block.get("type", "")
                        if t == "text":
                            parts.append(block.get("text", ""))
                        elif t == "tool_use":
                            inp = block.get("input", {})
                            parts.append(f"[tool:{block.get('name')} {json.dumps(inp)[:120]}]")
                        elif t == "tool_result":
                            parts.append(f"[tool_result:{str(block.get('content',''))[:120]}]")
                content = " ".join(p for p in parts if p)
            text_line = f"{role.upper()}: {str(content)[:600]}"
            lines.append(text_line)
        conversation = "\n\n".join(lines)

        prompt = SESSION_ARCHIVE_PROMPT.format(conversation=conversation)
        try:
            summary_md = await self.think(
                prompt,
                system=SESSION_ARCHIVE_SYSTEM,
                domain="text",
                temperature=0.4,
            )
        except Exception as e:
            return {"ok": False, "error": f"think failed: {e}"}

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        session_name = session.get("name") or sid
        provider = session.get("provider") or ""
        fm_lines = [
            "---",
            f"type: agent-session",
            f"session_id: {sid}",
            f"session_name: \"{session_name}\"",
            f"provider: {provider}",
            f"date: {date_str}",
            f"archived_at: {now.isoformat()}",
            "tags:",
            "  - agent-session",
            "---",
            "",
            f"# Session: {session_name}",
            "",
        ]
        note_body = "\n".join(fm_lines) + summary_md.strip() + "\n"
        filename = f"sessions/{date_str}-{sid[:8]}.md"
        try:
            self.vault_write(filename, note_body)
        except Exception as e:
            return {"ok": False, "error": f"vault write failed: {e}"}

        note_path = self.vault_path(filename)
        # Route through the viewer service (CLAUDE.md §Obsidian) so the scheme
        # + template come from whichever plugin registered service "viewer".
        url = ""
        try:
            viewer = self.service("viewer")
            if viewer and hasattr(viewer, "uri_templates"):
                import urllib.parse
                tmpl = viewer.uri_templates().get("open", "")
                if tmpl:
                    rel = note_path.relative_to(self.vault_root)
                    rel_str = str(rel).replace("\\", "/")
                    url = (tmpl
                        .replace("{vault}", urllib.parse.quote(self.vault_root.name, safe=""))
                        .replace("{path}", urllib.parse.quote(rel_str, safe="")))
        except Exception:
            url = ""

        return {"ok": True, "path": str(note_path), "url": url, "note_path": filename}
