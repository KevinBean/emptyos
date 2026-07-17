"""Rooms — pending-action lifecycle (sandbox / gate / apply / reject / undo).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the [DO:] review-gate flow: parse tokens, persist to ``data/apps/rooms/pending/``, render cards, apply/reject on user confirmation; also the action log and undo machinery.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._save_history`` (agents.py) when applying actions; calls back into chat for sub-app dispatch via ``self.call_app``.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.do_token import extract_do_tokens
from emptyos.sdk.sandbox import SandboxedWrite, StaleSandbox, load_sandbox
from emptyos.sdk.utils import config_flag, parse_llm_json

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# Verbs that ALWAYS route through the review-gate, regardless of agent
# gate_mode or allowlist. Destructive code-writes belong here — the diff
# preview IS the value of the gate; auto-executing them defeats the
# "with you, not for you" stance. Per .claude/rules/autopilot-grants.md
# these verbs are also never autopilot-grant-eligible.
ALWAYS_GATE_VERBS: set[tuple[str, str]] = {
    ("repo", "edit"),
    ("repo", "write"),
    ("repo", "exec"),
    ("rooms", "write_note"),  # already gated by sandbox-prep, listed for clarity
    ("promote", "apply_proposal"),  # applying a draft can fire an outbound webhook
}

_QUICK_DO_RE = re.compile(r'\[DO:([\w-]+)\.(\w+)\(')


def _has_always_gate_token(response: str) -> bool:
    """Cheap scan — does the response contain any token whose verb is in
    ALWAYS_GATE_VERBS? Used to force-route the whole turn through the
    review gate when even one destructive verb is present."""
    return any(
        (m.group(1), m.group(2)) in ALWAYS_GATE_VERBS
        for m in _QUICK_DO_RE.finditer(response)
    )


# ─── Bind to RoomsApp class as ───────────────────────────────
#   _actions_log_path          = _pending._actions_log_path
#   _pending_dir               = _pending._pending_dir
#   _pending_path              = _pending._pending_path
#   _sandbox_root              = _pending._sandbox_root
#   _prepare_write_note        = _pending._prepare_write_note
#   _prepare_repo_edit         = _pending._prepare_repo_edit
#   _prepare_repo_write        = _pending._prepare_repo_write
#   _prepare_repo_exec         = _pending._prepare_repo_exec
#   _save_pending              = _pending._save_pending
#   _load_pending              = _pending._load_pending
#   _lookup_inverse            = _pending._lookup_inverse
#   _method_signature          = _pending._method_signature
#   _execute_server_actions    = _pending._execute_server_actions
#   _summarize_server_actions  = _pending._summarize_server_actions  # @staticmethod
#   _gate_server_actions       = _pending._gate_server_actions
#   save_pending_action        = _pending.save_pending_action
#   list_pending               = _pending.list_pending
#   apply_pending              = _pending.apply_pending
#   reject_pending             = _pending.reject_pending
#   edit_pending               = _pending.edit_pending
#   api_undo                   = _pending.api_undo
#   api_room_pending           = _pending.api_room_pending
#   api_global_pending         = _pending.api_global_pending
#   api_apply_pending          = _pending.api_apply_pending
#   api_reject_pending         = _pending.api_reject_pending
#   api_edit_pending           = _pending.api_edit_pending
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _actions_log_path(self) -> Path:
    return self.data_dir / "actions.jsonl"


def _pending_dir(self) -> Path:
    return self.data_subdir("pending")


def _pending_path(self, action_id: str) -> Path:
    return self._pending_dir() / f"{action_id}.json"


def _sandbox_root(self) -> Path:
    """Root for per-action sandbox dirs used by `[DO:rooms.write_note]`."""
    return self.data_subdir("sandboxes")


def _prepare_write_note(self, action: dict) -> None:
    """For `[DO:rooms.write_note({path, content})]` tokens, capture the
    proposed write into a sandbox dir + attach `proposed_changes` to the
    action so the pending card can render a diff before any vault file
    changes. Mutates `action` in place.

    On any failure (bad args, path traversal, missing vault), attaches an
    `error` field so the UI can surface it without crashing the gate.
    """
    args = action.get("args") or {}
    rel_path = (args.get("path") or "").strip()
    content = args.get("content")
    if not rel_path or content is None:
        action["error"] = "write_note requires 'path' and 'content' args"
        return
    vault_root = self.kernel.config.notes_path
    if not vault_root:
        action["error"] = "no vault configured (set notes.path in emptyos.toml)"
        return
    try:
        sw = SandboxedWrite(
            action_id=action["id"],
            vault_root=vault_root,
            rel_path=rel_path,
            content=str(content),
            sandbox_root=self._sandbox_root(),
        )
        sw.capture()
    except ValueError as e:
        action["error"] = str(e)[:200]
        return
    except Exception as e:
        action["error"] = f"sandbox capture failed: {e!s:.200}"
        return
    action["proposed_changes"] = [{
        "path": rel_path,
        "sandbox_id": action["id"],
        "diff_lines": sw.diff_lines(),
    }]


def _prepare_repo_edit(self, action: dict) -> None:
    """For `[DO:repo.edit({path, old, new})]` tokens: read the current
    file, compute the post-replace content, sandbox it for diff preview."""
    args = action.get("args") or {}
    rel_path = (args.get("path") or "").strip()
    old = args.get("old")
    new = args.get("new")
    if not rel_path:
        action["error"] = "repo.edit requires 'path' arg"
        return
    if not isinstance(old, str) or not old:
        action["error"] = "repo.edit requires non-empty 'old' arg"
        return
    if not isinstance(new, str):
        action["error"] = "repo.edit requires string 'new' arg"
        return
    repo_root = self.repo_root
    target = (repo_root / rel_path).resolve()
    try:
        target.relative_to(repo_root)
    except ValueError:
        action["error"] = f"path escapes repo root: {rel_path!r}"
        return
    if not target.exists():
        action["error"] = f"not found: {rel_path!r}"
        return
    if target.is_dir():
        action["error"] = f"is a directory: {rel_path!r}"
        return
    try:
        current = target.read_text(encoding="utf-8")
    except OSError as e:
        action["error"] = f"read failed: {e!s:.200}"
        return
    count = current.count(old)
    if count == 0:
        action["error"] = "`old` not found in file"
        return
    if count > 1:
        action["error"] = (
            f"`old` matches {count} times — agent must regenerate with "
            "surrounding context for uniqueness"
        )
        return
    proposed = current.replace(old, new, 1)
    try:
        sw = SandboxedWrite(
            action_id=action["id"],
            vault_root=repo_root,
            rel_path=rel_path,
            content=proposed,
            sandbox_root=self._sandbox_root(),
        )
        sw.capture()
    except ValueError as e:
        action["error"] = str(e)[:200]
        return
    except Exception as e:
        action["error"] = f"sandbox capture failed: {e!s:.200}"
        return
    action["proposed_changes"] = [{
        "path": rel_path,
        "sandbox_id": action["id"],
        "diff_lines": sw.diff_lines(),
    }]


def _prepare_repo_write(self, action: dict) -> None:
    """For `[DO:repo.write({path, content})]` tokens: sandbox the proposed
    full-file write. New files render as all-adds in the diff."""
    args = action.get("args") or {}
    rel_path = (args.get("path") or "").strip()
    content = args.get("content")
    if not rel_path:
        action["error"] = "repo.write requires 'path' arg"
        return
    if not isinstance(content, str):
        action["error"] = "repo.write requires string 'content' arg"
        return
    repo_root = self.repo_root
    target = (repo_root / rel_path).resolve()
    try:
        target.relative_to(repo_root)
    except ValueError:
        action["error"] = f"path escapes repo root: {rel_path!r}"
        return
    if target.exists() and target.is_dir():
        action["error"] = f"is a directory: {rel_path!r}"
        return
    try:
        sw = SandboxedWrite(
            action_id=action["id"],
            vault_root=repo_root,
            rel_path=rel_path,
            content=content,
            sandbox_root=self._sandbox_root(),
        )
        sw.capture()
    except ValueError as e:
        action["error"] = str(e)[:200]
        return
    except Exception as e:
        action["error"] = f"sandbox capture failed: {e!s:.200}"
        return
    action["proposed_changes"] = [{
        "path": rel_path,
        "sandbox_id": action["id"],
        "diff_lines": sw.diff_lines(),
    }]


def _prepare_repo_exec(self, action: dict) -> None:
    """For `[DO:repo.exec({cmd, timeout?, cwd?, shell?})]` tokens: validate
    args + attach a `proposed_command` preview block. No sandbox dir —
    exec has no before/after state; the preview IS the command itself.

    Apply re-dispatches via call_app("repo", "exec", **args) — the work
    happens at apply time, not capture."""
    args = action.get("args") or {}
    cmd = args.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        action["error"] = "repo.exec requires non-empty 'cmd' arg"
        return
    timeout = args.get("timeout")
    if timeout is not None:
        try:
            timeout = int(timeout)
            if timeout <= 0 or timeout > 600:
                action["error"] = "'timeout' must be 1..600 seconds"
                return
        except (TypeError, ValueError):
            action["error"] = "'timeout' must be an integer (seconds)"
            return
    cwd = args.get("cwd")
    shell = args.get("shell") or ""
    action["proposed_command"] = {
        "cmd": cmd,
        "cwd": cwd or "(repo root)",
        "timeout": timeout or 30,
        "shell": shell or "(default)",
    }


def _save_pending(self, action: dict) -> None:
    self._pending_path(action["id"]).write_text(
        json.dumps(action, indent=2, ensure_ascii=False), encoding="utf-8",
    )


def _load_pending(self, action_id: str) -> dict | None:
    p = self._pending_path(action_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_pending(self, action_id: str) -> dict | None:
    """Read one pending action by id (call_app-able). Used by the replay
    runner to poll a gated step's status across a resume."""
    return self._load_pending(action_id)


def _lookup_inverse(self, app_id: str, method: str) -> str:
    """Return the declared inverse method for app_id.method, or '' if none.

    Delegates to the shared, registry-aware resolver so a verb-registry-migrated
    app (whose legacy [provides.assistant] block was deleted) keeps its undo.
    """
    from emptyos.sdk.actions_log import lookup_inverse
    return lookup_inverse(self.kernel, app_id, method)


def _method_signature(self, app_id: str, method: str) -> str:
    """Introspect the real method signature so the LLM doesn't invent params.

    Falls back to the verb registry's declared ``args`` schema when the app
    isn't loaded yet — a bare "()" teaches the model nothing and it emits
    empty args (seen in the wild: telegram-bridge journal card with {}).
    """
    import inspect
    try:
        inst = self.kernel.apps.instances.get(app_id)
        fn = getattr(inst, method, None) if inst else None
        if not callable(fn):
            return self._registry_signature(app_id, method)
        params: list[str] = []
        for name, p in inspect.signature(fn).parameters.items():
            if name in ("self", "request", "cls"):
                continue
            if p.default is inspect.Parameter.empty:
                params.append(name)
            else:
                params.append(f"{name}={p.default!r}")
        return "(" + ", ".join(params) + ")"
    except Exception:
        return "()"


def _registry_signature(self, app_id: str, method: str) -> str:
    """Signature from the verb registry's args schema, for unloaded apps.

    Matches a VerbEntry by app id + any of its surface dispatch methods
    (canonical, voice, assistant). ``{text = "string", due = "string?"}``
    renders as ``(text, due?)`` — enough for the LLM not to invent params.
    """
    try:
        registry = self.kernel.apps.get_verbs()
        for e in registry:
            if e.app_id != app_id:
                continue
            methods = {e.method, e.method_for("voice"), e.method_for("assistant")}
            if method not in methods:
                continue
            parts = []
            for name, typ in (e.args or {}).items():
                optional = isinstance(typ, str) and typ.endswith("?")
                parts.append(f"{name}?" if optional else name)
            return "(" + ", ".join(parts) + ")"
    except Exception:
        pass
    return "()"


async def _execute_server_actions(self, response: str, agent: dict, *,
                                  room_id: str = "") -> tuple[str, list[dict]]:
    """Parse [DO:app.method(json_args)] from response, execute, return cleaned text + results.

    `[BUTTON:label|DO:app.method({...})]` wrappers are click-to-execute and
    are NOT auto-executed here — they pass through to the client, which
    renders them as buttons and POSTs /api/do when the user clicks.
    Without masking, this auto-exec parser would fire the inner [DO:]
    immediately and the user would see a "done" before clicking anything.

    When `agent.gate_mode == "gate"`, tokens land in the rooms pending
    queue instead of auto-executing — same review-gate machinery the
    CLI participant path uses. `room_id` must be provided for storage.
    """
    if agent.get("gate_mode") == "gate" and room_id:
        source_actor = {"type": "agent", "id": agent.get("id", "")}
        return await self._gate_server_actions(
            response, room_id=room_id, source_actor=source_actor,
        )

    # Force-gate any response containing an ALWAYS_GATE verb (repo.edit,
    # repo.write, repo.exec). Destructive code-writes never auto-execute —
    # the whole turn goes to the review gate so reads in the same turn
    # wait alongside the edits the user is reviewing.
    if room_id and _has_always_gate_token(response):
        source_actor = {"type": "agent", "id": agent.get("id", "")}
        return await self._gate_server_actions(
            response, room_id=room_id, source_actor=source_actor,
        )

    server_actions = agent.get("server_actions", {})
    if not server_actions:
        return response, []

    button_wrapped = re.compile(
        r'\[BUTTON:[^|\]]+\|DO:[\w-]+\.\w+\(\{.*?\}\)\]', re.DOTALL
    )
    button_spans: list[str] = []

    def _stash(m: re.Match) -> str:
        button_spans.append(m.group(0))
        return f"\x00BUTTON_DO_{len(button_spans) - 1}\x00"

    masked = button_wrapped.sub(_stash, response)

    pattern = re.compile(r'\[DO:([\w-]+)\.(\w+)\((\{.*?\})\)\]', re.DOTALL)
    results = []
    cleaned = masked

    for match in pattern.finditer(masked):
        app_id, method, args_str = match.group(1), match.group(2), match.group(3)
        # Validate against allowlist
        allowed_methods = server_actions.get(app_id, [])
        if method not in allowed_methods:
            results.append({"app": app_id, "method": method, "error": "not allowed", "ok": False})
            continue
        try:
            args = parse_llm_json(args_str, fallback={})
            res = await self.call_app(app_id, method, **args)
            inverse = self._lookup_inverse(app_id, method)
            from emptyos.sdk.actions_log import record_action
            record_action(self._actions_log_path(), app=app_id, method=method,
                          args=args, result=res, inverse=inverse)
            # Parallel HMAC-chained audit. The undo log above carries raw
            # args + inverse for the Undo button; the audit chain is the
            # tamper-evident "what fired without a per-instance Apply"
            # record. Never let audit failure break the action path.
            try:
                from emptyos.sdk.autopilot import append_audit
                append_audit(
                    self.kernel.config.data_dir,
                    actor={"type": "agent", "id": agent.get("id", "")},
                    app=app_id, method=method, args=args,
                    grant_id=None,  # agent allowlist auto-applies aren't grant-driven
                    ok=True,
                )
            except Exception:
                pass
            results.append({
                "app": app_id, "method": method, "result": str(res)[:300],
                "ok": True, "reversible": bool(inverse),
                "links": self._action_result_links(app_id, res),
            })
        except Exception as e:
            results.append({"app": app_id, "method": method, "error": str(e)[:200], "ok": False})

    # Strip bare [DO:] tags from response text, then restore the
    # [BUTTON:|DO:] wrappers so the client renders them as buttons.
    cleaned = pattern.sub("", cleaned)
    for idx, original in enumerate(button_spans):
        cleaned = cleaned.replace(f"\x00BUTTON_DO_{idx}\x00", original)
    cleaned = cleaned.strip()
    return cleaned, results


@staticmethod
def _summarize_server_actions(results: list[dict]) -> str:
    """Fallback text when the LLM emits only [DO:] tags and nothing else.

    For reads (no inverse), include the result payload so the user sees data.
    For writes, a checkmark is enough — the server_results block below shows it
    and the Undo button covers reversibility.
    """
    if not results:
        return ""
    lines = []
    for r in results:
        label = f"{r.get('app', '?')}.{r.get('method', '?')}"
        if r.get("ok"):
            result = (r.get("result") or "").strip()
            if result and result not in ("None", "null", "[]", "{}"):
                lines.append(f"**{label}**\n{result}")
            else:
                lines.append(f"✓ {label}")
        else:
            lines.append(f"✗ {label} — {r.get('error', 'failed')}")
    return "\n\n".join(lines)


async def _gate_server_actions(
    self, response: str, *, room_id: str, source_actor: dict,
) -> tuple[str, list[dict]]:
    """Parse [DO:] tokens from `response`, save each as a pending action,
    return cleaned text + the saved entries.

    Autopilot grants (`.claude/rules/autopilot-grants.md`) can cause
    eligible verbs from this actor to skip the pending queue and apply
    immediately. The eligibility floor in `data/autopilot/policy.json`
    keeps free-form-content verbs (rooms.write_note, repo.*) and
    irreversible ones out of the grant path regardless of policy edits.
    """
    from emptyos.sdk.autopilot import append_audit, decide

    cleaned, pending = extract_do_tokens(
        response, source_actor=source_actor, context={"room_id": room_id},
    )
    data_root = self.kernel.config.data_dir
    scope_candidates = [f"session:{room_id}", f"room:{room_id}", "global"]
    # Registry-derived eligibility floor for this turn (None = legacy floor).
    # Computed once (it's stable across the turn) and getattr-guarded so a
    # minimal kernel without the method degrades to the legacy floor rather
    # than crashing the gate.
    _elig_fn = getattr(self.kernel, "autopilot_eligible_set", None)
    eligible_set = _elig_fn() if callable(_elig_fn) else None
    # Pivot 2026-06-07 (dark flag): when on, stable verbs auto-run by default
    # (no grant needed) unless held. Off = pre-pivot grant-or-gate behaviour.
    # config_flag degrades to the dark default on minimal kernels / test fakes.
    auto_stable = config_flag(self.kernel.config, "autopilot.auto_stable_default")
    # Per-actor budget ceiling (dark flag, default off). When on, decide()
    # overrides any `auto` outcome to `gate` (reason "over-budget") once the
    # actor's metered spend reaches its monthly cap — friction only, it can
    # never widen access. Spend is fed by the billing bridge
    # (`apps/public/standard/billing/app.py::_meter_spend`, flag
    # `autopilot.meter_spend`).
    enforce_budget = config_flag(self.kernel.config, "autopilot.enforce_budget_caps")
    for action in pending:
        # Sandbox-diff capture for vault writes: agent proposes content,
        # we stash it + a diff in data/apps/rooms/sandboxes/<id>/, the
        # pending card renders the diff, Apply replays the write only on
        # explicit user click. No vault file changes until then.
        if action["app"] == "rooms" and action["method"] == "write_note":
            self._prepare_write_note(action)
            self._save_pending(action)
            continue
        elif action["app"] == "repo" and action["method"] == "edit":
            self._prepare_repo_edit(action)
            self._save_pending(action)
            continue
        elif action["app"] == "repo" and action["method"] == "write":
            self._prepare_repo_write(action)
            self._save_pending(action)
            continue
        elif action["app"] == "repo" and action["method"] == "exec":
            self._prepare_repo_exec(action)
            self._save_pending(action)
            continue

        # Auto-vs-gate decision (`.claude/rules/autopilot-grants.md`). `decide()`
        # enforces the eligibility floor internally: a `gated`/`never` verb only
        # auto-runs via a matching grant; a `stable` verb auto-runs by default
        # (when the dark flag is on) unless held. Non-eligible verbs fall to
        # pending. `grant_id` is None for the stable-default path.
        verb = f"{action['app']}.{action['method']}"
        decision = decide(
            data_root,
            actor_type=(source_actor or {}).get("type") or "",
            actor_id=(source_actor or {}).get("id") or "",
            verb=verb,
            scope_candidates=scope_candidates,
            eligible=eligible_set,
            auto_stable=auto_stable,
            enforce_budget=enforce_budget,
        )
        if decision["action"] != "auto":
            # Preserve informative gate reasons ("held", "over-budget") on the
            # record for the audit trail — including the grant that *would*
            # have fired had the actor been under its budget cap. The
            # uninformative default ("no-grant") stays off the record.
            if decision.get("reason") and decision["reason"] != "no-grant":
                action["gate_reason"] = decision["reason"]
                if decision.get("grant_id"):
                    action["gate_grant_id"] = decision["grant_id"]
            self._save_pending(action)
            continue
        grant_id = decision.get("grant_id")

        # Auto-apply path. Capture into the same pending file so the global
        # dashboard / activity drawer can show the entry with its
        # resolved status; client-side UI is unchanged.
        try:
            result = await self.call_app(
                action["app"], action["method"], **(action.get("args") or {}),
            )
            action["status"] = "applied"
            action["result"] = str(result)[:500]
            action["grant_id"] = grant_id
            action["auto_reason"] = decision.get("reason")
            action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
            ok = True
            err: str | None = None
            # Record to the undo actions-log (with declared inverse) so
            # api_undo can reverse this auto-applied action — same shape the
            # agent path writes. Without this, a stable-default auto-apply
            # would be un-undoable, which breaks the audit+undo trust-net.
            inverse = self._lookup_inverse(action["app"], action["method"])
            action["reversible"] = bool(inverse)
            from emptyos.sdk.actions_log import record_action
            record_action(self._actions_log_path(), app=action["app"],
                          method=action["method"], args=action.get("args") or {},
                          result=result, inverse=inverse)
        except Exception as e:
            action["status"] = "failed"
            action["error"] = str(e)[:200]
            action["grant_id"] = grant_id
            action["auto_reason"] = decision.get("reason")
            action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
            ok = False
            err = str(e)[:200]
        self._save_pending(action)
        try:
            append_audit(
                data_root,
                actor=source_actor or {},
                app=action["app"], method=action["method"],
                args=action.get("args") or {},
                grant_id=grant_id, ok=ok, error=err,
            )
        except Exception:
            pass
        asyncio.create_task(self.emit("rooms:action_auto_applied", {
            "action_id": action["id"], "room_id": room_id,
            "app": action["app"], "method": action["method"],
            "grant_id": grant_id, "reason": decision.get("reason"), "ok": ok,
        }))
    return cleaned, pending


async def save_pending_action(
    self,
    *,
    app: str,
    method: str,
    args: dict | None = None,
    source_actor: dict | None = None,
    room_id: str = "",
) -> dict:
    """File a pending action directly, without parsing a ``[DO:]`` token.

    Used by apps that want to propose state changes through the review-gate
    paradigm (`.claude/rules/proposed-action.md`) without going via a chat
    surface — e.g. video-digest extracting KB-note candidates, or any future
    app proposing a write the user should approve.

    Returns the saved action dict (includes the generated ``id``). The action
    lands in ``data/apps/rooms/pending/<id>.json`` exactly as token-driven
    pending actions do, surfaces on the global pending dashboard, and is
    applied / rejected via the same endpoints.

    The same per-verb prep paths used by ``_gate_server_actions`` run here
    too, so vault-write proposals get the same diff-capture treatment.
    """
    action = {
        "id": "act-" + uuid.uuid4().hex[:10],
        "room_id": room_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "source_actor": source_actor or {"type": "app", "id": ""},
        "app": app,
        "method": method,
        "args": args or {},
        "status": "pending",
    }
    if action["app"] == "rooms" and action["method"] == "write_note":
        self._prepare_write_note(action)
    elif action["app"] == "repo" and action["method"] == "edit":
        self._prepare_repo_edit(action)
    elif action["app"] == "repo" and action["method"] == "write":
        self._prepare_repo_write(action)
    elif action["app"] == "repo" and action["method"] == "exec":
        self._prepare_repo_exec(action)
    self._save_pending(action)
    asyncio.create_task(
        self.emit(
            "rooms:action_proposed",
            {
                "action_id": action["id"],
                "app": app,
                "method": method,
                "source_actor": action["source_actor"],
                "room_id": room_id,
            },
        )
    )
    return action


def list_pending(self, room_id: str = "", status: str = "pending") -> list[dict]:
    """List pending actions, optionally filtered by room and status.
    Sorted by timestamp ascending."""
    out: list[dict] = []
    for f in self._pending_dir().glob("act-*.json"):
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if room_id and a.get("room_id") != room_id:
            continue
        if status and a.get("status") != status:
            continue
        out.append(a)
    out.sort(key=lambda x: x.get("ts", ""))
    return out


async def apply_pending(self, action_id: str) -> dict:
    """Execute a pending action via call_app, mark it applied.

    Sandboxed actions (those carrying `proposed_changes` from
    `[DO:rooms.write_note]`) bypass call_app — the resolved write is
    replayed via `SandboxedWrite.apply()` instead. Stale captures (the
    vault file changed since the diff was shown) fail rather than
    clobber whatever moved underneath.
    """
    action = self._load_pending(action_id)
    if not action:
        return {"error": "action not found"}
    if action.get("status") != "pending":
        return {"error": f"already {action.get('status')}"}
    if action.get("error"):
        # Gate-time prep already failed (bad args, no vault, etc.).
        action["status"] = "failed"
        action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
        self._save_pending(action)
        return {"error": action["error"], "action": action}
    proposed = action.get("proposed_changes") or []
    if proposed:
        try:
            applied_paths: list[str] = []
            for change in proposed:
                sw = load_sandbox(
                    change.get("sandbox_id") or action_id,
                    self._sandbox_root(),
                )
                if sw is None:
                    raise RuntimeError(
                        f"sandbox missing for {change.get('path')!r}; "
                        "agent must regenerate"
                    )
                sw.apply()
                applied_paths.append(change.get("path") or "")
                sw.discard()
        except StaleSandbox as e:
            action["status"] = "failed"
            action["error"] = str(e)[:200]
            action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
            self._save_pending(action)
            return {"error": action["error"], "action": action}
        except Exception as e:
            action["status"] = "failed"
            action["error"] = str(e)[:200]
            action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
            self._save_pending(action)
            return {"error": action["error"], "action": action}
        action["status"] = "applied"
        action["result"] = f"wrote {len(applied_paths)} file(s): " + \
            ", ".join(applied_paths)
        action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
        self._save_pending(action)
        await self.emit("rooms:note_written", {
            "action_id": action_id, "room_id": action.get("room_id"),
            "paths": applied_paths,
        })
        await self.emit("rooms:action_applied", {
            "action_id": action_id, "room_id": action.get("room_id"),
            "app": action["app"], "method": action["method"],
        })
        return action
    try:
        result = await self.call_app(
            action["app"], action["method"], **(action.get("args") or {}),
        )
    except Exception as e:
        action["status"] = "failed"
        action["error"] = str(e)[:200]
        action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
        self._save_pending(action)
        return {"error": str(e)[:200], "action": action}
    action["status"] = "applied"
    action["result"] = str(result)[:500]
    action["links"] = self._action_result_links(action["app"], result)
    action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
    self._save_pending(action)
    await self.emit("rooms:action_applied", {
        "action_id": action_id, "room_id": action.get("room_id"),
        "app": action["app"], "method": action["method"],
    })
    return action


async def reject_pending(self, action_id: str) -> dict:
    """Mark a pending action rejected without executing. Sandboxed
    actions also have their captured sandbox dir discarded so we don't
    accumulate orphans."""
    action = self._load_pending(action_id)
    if not action:
        return {"error": "action not found"}
    if action.get("status") != "pending":
        return {"error": f"already {action.get('status')}"}
    for change in action.get("proposed_changes") or []:
        sw = load_sandbox(
            change.get("sandbox_id") or action_id, self._sandbox_root(),
        )
        if sw is not None:
            sw.discard()
    action["status"] = "rejected"
    action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
    self._save_pending(action)
    await self.emit("rooms:action_rejected", {
        "action_id": action_id, "room_id": action.get("room_id"),
    })
    return action


def edit_pending(self, action_id: str, args: dict) -> dict:
    """Replace a pending action's args before Apply (modify-params-then-apply).

    Only ``args`` is editable — never ``app``/``method``; changing the verb
    would be a different action sneaking past the gate. Diff-shaped actions
    (sandbox captures / ALWAYS_GATE_VERBS) are refused: their sandbox was
    captured from the original args, so the rendered diff — the whole value
    of that gate — would no longer match. The original args are kept on the
    record (``args_original``) so the audit trail shows the human edit.
    """
    action = self._load_pending(action_id)
    if not action:
        return {"error": "action not found"}
    if action.get("status") != "pending":
        return {"error": f"already {action.get('status')}"}
    if (
        action.get("proposed_changes")
        or (action.get("app"), action.get("method")) in ALWAYS_GATE_VERBS
    ):
        return {"error": "diff-shaped actions are not editable; reject and regenerate"}
    if not isinstance(args, dict):
        return {"error": "args must be an object"}
    if "args_original" not in action:
        action["args_original"] = action.get("args") or {}
    action["args"] = args
    action["edited"] = True
    action["edited_ts"] = datetime.now(timezone.utc).isoformat()
    self._save_pending(action)
    return action


@web_route("POST", "/api/undo")
async def api_undo(self, request):
    """Undo the last reversible [DO:] action by calling its declared inverse.

    Reversal logic lives in the shared undo log (emptyos.sdk.actions_log); this
    route wraps it with the rooms:undo emit + the rooms response shape.
    """
    from emptyos.sdk.actions_log import undo_last
    out = await undo_last(self._actions_log_path(), self.call_app)
    if out.get("ok"):
        undid = out.get("undid", {})
        await self.emit("rooms:undo", {
            "app": undid.get("app"), "method": undid.get("method"),
            "inverse": undid.get("inverse"),
        })
    return out


@web_route("GET", "/api/rooms/{room_id}/pending")
async def api_room_pending(self, request):
    """Pending [DO:] actions for a room. Defaults to status=pending; pass
    ?status=all to include applied/rejected/failed for an audit view."""
    room_id = request.path_params["room_id"]
    status = request.query_params.get("status", "pending")
    if status == "all":
        status = ""
    return self.list_pending(room_id, status=status)


@web_route("GET", "/api/pending")
async def api_global_pending(self, request):
    """All pending actions across every room. Used by the sidebar's
    global pending dashboard. ?status=all includes resolved entries."""
    status = request.query_params.get("status", "pending")
    if status == "all":
        status = ""
    return self.list_pending(room_id="", status=status)


@web_route("POST", "/api/pending/{action_id}/apply")
async def api_apply_pending(self, request):
    return await self.apply_pending(request.path_params["action_id"])


@web_route("POST", "/api/pending/{action_id}/reject")
async def api_reject_pending(self, request):
    return await self.reject_pending(request.path_params["action_id"])


@web_route("POST", "/api/pending/{action_id}/edit")
async def api_edit_pending(self, request):
    """Edit a pending action's args before Apply. Body: ``{"args": {...}}``."""
    try:
        body = await request.json()
    except Exception:
        return {"error": "invalid JSON body"}
    return self.edit_pending(
        request.path_params["action_id"], (body or {}).get("args"),
    )
