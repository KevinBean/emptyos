"""Rooms — team mode: lead + worker participants, a shared task list, and an
opt-in auto-dispatch loop.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the per-room `team` block (roles + tasks + run state), the
lead/worker system-prompt block, the team task/role verbs (callable via UI,
``call_app``, and the ``[DO:rooms.team_*]`` gate), and the bounded
auto-dispatch orchestrator (``_team_dispatch_loop``).

A room is a "team" only once a participant has role ``lead`` — otherwise these
helpers are inert and the room behaves exactly as a normal chat room
(coexistence guarantee). The auto-dispatch loop is modelled on
``dogfood-agent/drain.py``: explicitly armed (``team_start_run``), budgeted
(``turns_left``), stoppable (``team_stop_run``), background-task driven.
Worker state-changing ``[DO:]`` to OTHER apps still flows through the room's
existing review gate — the loop runs *turns*, it only auto-applies the
team-namespace verbs (the arm is the grant for the team to manage its own
list).

Cross-module callers reach methods here via ``self.X`` after re-binding;
do not import from ``.app`` (it imports us, which would cycle). Reaches into:
``_run_participant_turn`` (chat.py), ``_normalize_participants`` (participants.py),
``_load_agent``/``_save_agent`` (agents.py).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from emptyos.sdk import now_iso as _now
from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ──────────────────────────────────────────
#   _team_prompt_block        = _team._team_prompt_block
#   _team_enabled             = _team._team_enabled
#   _team_lead_participant    = _team._team_lead_participant
#   _participant_by_id        = _team._participant_by_id
#   _team_find_task           = _team._team_find_task
#   _team_next_actionable     = _team._team_next_actionable
#   _team_lead_nudge          = _team._team_lead_nudge
#   _team_dec_turn            = _team._team_dec_turn
#   _team_finish_run          = _team._team_finish_run
#   _fire                     = _team._fire
#   team_list                 = _team.team_list
#   team_add_task             = _team.team_add_task
#   team_assign               = _team.team_assign
#   team_set_status           = _team.team_set_status
#   set_participant_role      = _team.set_participant_role
#   team_start_run            = _team.team_start_run
#   team_stop_run             = _team.team_stop_run
#   _team_turn_or_timeout     = _team._team_turn_or_timeout
#   _team_dispatch_loop       = _team._team_dispatch_loop
#   api_team_list             = _team.api_team_list
#   api_team_add_task         = _team.api_team_add_task
#   api_team_assign           = _team.api_team_assign
#   api_team_set_status       = _team.api_team_set_status
#   api_team_set_role         = _team.api_team_set_role
#   api_team_run_start        = _team.api_team_run_start
#   api_team_run_stop         = _team.api_team_run_stop
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────────


TEAM_STATUSES = ("todo", "doing", "done", "blocked")
TEAM_ROLES = ("lead", "worker", "peer")
TEAM_MAX_TASKS = 50
TEAM_DEFAULT_MAX_TURNS = 12
TEAM_MAX_TURNS_CAP = 40
# Per-turn wall-clock cap. The loop only checks stop/budget BETWEEN turns, so
# a turn that hangs (slow model, stuck CLI, human-think with no human) would
# otherwise freeze the whole run with no way to preempt it. Bound every turn.
TEAM_TURN_TIMEOUT_S = 180.0
# Team-namespace verbs the auto-dispatch loop applies directly (the armed run
# is the grant). Any other [DO:] verb still flows through the normal gate.
TEAM_VERBS = {"team_add_task", "team_assign", "team_set_status"}


# ── State helpers ────────────────────────────────────────────────────────


def _team_block(room: dict) -> dict:
    return room.get("team") or {}


def _team_enabled(self, room: dict) -> bool:
    """A room is a team once any participant has role 'lead'."""
    return any(
        (p.get("role") == "lead") for p in self._normalize_participants(room)
    )


def _team_lead_participant(self, room: dict) -> dict | None:
    for p in self._normalize_participants(room):
        if p.get("role") == "lead":
            return p
    return None


def _participant_by_id(self, room: dict, pid: str) -> dict | None:
    for p in self._normalize_participants(room):
        if (p.get("id") or "") == pid:
            return p
    return None


def _team_find_task(self, room: dict, task_id: str) -> dict | None:
    for t in _team_block(room).get("tasks") or []:
        if t.get("id") == task_id:
            return t
    return None


def _team_next_actionable(self, room: dict) -> dict | None:
    """Next task to dispatch: status 'todo' with a real assignee. Sequential
    (one at a time) — matches today's single-responder loop and keeps the
    shared history coherent."""
    for t in _team_block(room).get("tasks") or []:
        if t.get("status") == "todo" and (t.get("assignee") or "").strip():
            return t
    return None


# ── Read API ───────────────────────────────────────────────────────────────


def team_list(self, room_id: str) -> dict:
    """Tasks + roster-with-roles + run state for the Team panel."""
    room = self._load_agent(room_id)
    if not room:
        return {"error": "room not found"}
    parts = self._normalize_participants(room)
    tb = _team_block(room)
    roster = [
        {"id": p.get("id"), "type": p.get("type"), "role": p.get("role", "peer")}
        for p in parts
    ]
    return {
        "room_id": room_id,
        "enabled": self._team_enabled(room),
        "lead": (self._team_lead_participant(room) or {}).get("id", ""),
        "roster": roster,
        "tasks": list(tb.get("tasks") or []),
        "run": tb.get("run") or {},
    }


# ── Mutating verbs (callable via UI / call_app / [DO:rooms.team_*]) ──────────


def team_add_task(self, room_id: str, content: str, assignee: str = "") -> dict:
    content = (content or "").strip()
    if not content:
        return {"error": "content required"}
    room = self._load_agent(room_id)
    if not room:
        return {"error": "room not found"}
    if assignee and not self._participant_by_id(room, assignee):
        return {"error": f"no participant '{assignee}'"}
    tb = dict(_team_block(room))
    tasks = list(tb.get("tasks") or [])
    if len(tasks) >= TEAM_MAX_TASKS:
        return {"error": f"task list full ({TEAM_MAX_TASKS})"}
    task = {
        "id": f"t-{uuid.uuid4().hex[:10]}",
        "content": content,
        "assignee": assignee or "",
        "status": "todo",
        "result": "",
        "created": _now(),
        "updated": _now(),
    }
    tasks.append(task)
    tb["tasks"] = tasks
    room["team"] = tb
    self._save_agent(room)
    self._fire("rooms:team_task_added", {"room_id": room_id, "task_id": task["id"], "assignee": assignee})
    return {"ok": True, "task": task}


def team_assign(self, room_id: str, task_id: str, assignee: str) -> dict:
    room = self._load_agent(room_id)
    if not room:
        return {"error": "room not found"}
    if assignee and not self._participant_by_id(room, assignee):
        return {"error": f"no participant '{assignee}'"}
    tb = dict(_team_block(room))
    tasks = list(tb.get("tasks") or [])
    found = None
    for t in tasks:
        if t.get("id") == task_id:
            t["assignee"] = assignee or ""
            t["updated"] = _now()
            found = t
            break
    if not found:
        return {"error": f"no task '{task_id}'"}
    tb["tasks"] = tasks
    room["team"] = tb
    self._save_agent(room)
    self._fire("rooms:team_task_assigned", {"room_id": room_id, "task_id": task_id, "assignee": assignee})
    return {"ok": True, "task": found}


def team_set_status(self, room_id: str, task_id: str, status: str, result: str = "") -> dict:
    status = (status or "").strip().lower()
    if status not in TEAM_STATUSES:
        return {"error": f"status must be one of {TEAM_STATUSES}"}
    room = self._load_agent(room_id)
    if not room:
        return {"error": "room not found"}
    tb = dict(_team_block(room))
    tasks = list(tb.get("tasks") or [])
    found = None
    for t in tasks:
        if t.get("id") == task_id:
            t["status"] = status
            if result:
                t["result"] = str(result)[:2000]
            t["updated"] = _now()
            found = t
            break
    if not found:
        return {"error": f"no task '{task_id}'"}
    tb["tasks"] = tasks
    room["team"] = tb
    self._save_agent(room)
    self._fire("rooms:team_task_status", {"room_id": room_id, "task_id": task_id, "status": status})
    return {"ok": True, "task": found}


def set_participant_role(self, room_id: str, participant_id: str, role: str) -> dict:
    role = (role or "").strip().lower()
    if role not in TEAM_ROLES:
        return {"error": f"role must be one of {TEAM_ROLES}"}
    room = self._load_agent(room_id)
    if not room:
        return {"error": "room not found"}
    parts = list(room.get("participants") or self._normalize_participants(room))
    found = False
    for p in parts:
        if isinstance(p, dict) and (p.get("id") or "") == participant_id:
            p["role"] = role
            found = True
        elif isinstance(p, dict) and "role" not in p:
            p["role"] = "peer"
    if not found:
        return {"error": f"no participant '{participant_id}'"}
    room["participants"] = parts
    # Maintain the team block's lead pointer + enabled flag.
    tb = dict(_team_block(room))
    lead = next((p.get("id") for p in parts if p.get("role") == "lead"), "")
    tb["lead"] = lead or ""
    tb["enabled"] = bool(lead)
    tb.setdefault("tasks", [])
    room["team"] = tb
    self._save_agent(room)
    return {"ok": True, "participant_id": participant_id, "role": role, "team_enabled": bool(lead)}


# ── Auto-dispatch run control ────────────────────────────────────────────


def team_start_run(self, room_id: str, max_turns: int = TEAM_DEFAULT_MAX_TURNS, started_by: str = "user") -> dict:
    """Arm the auto-dispatch loop for this room (the autonomy grant)."""
    room = self._load_agent(room_id)
    if not room:
        return {"error": "room not found"}
    if not self._team_enabled(room):
        return {"error": "room has no lead — assign a lead role first"}
    try:
        budget = max(1, min(int(max_turns), TEAM_MAX_TURNS_CAP))
    except (TypeError, ValueError):
        budget = TEAM_DEFAULT_MAX_TURNS
    tb = dict(_team_block(room))
    if (tb.get("run") or {}).get("active"):
        return {"error": "a team run is already active"}
    tb["run"] = {
        "active": True, "turns_left": budget, "max_turns": budget,
        "started": _now(), "started_by": started_by, "stop_requested": False,
    }
    tb.setdefault("tasks", [])
    room["team"] = tb
    # Resume any work stranded into participants' mailboxes on a prior run (dark flag).
    if _team_mailbox_enabled(self):
        _team_drain_mailbox(self, room, room_id)
    self._save_agent(room)
    self._fire("rooms:team_run_started", {"room_id": room_id, "max_turns": budget})
    # Fire-and-forget orchestrator (matches drain / app-builder pattern).
    # Guarded: outside a running loop (sync unit tests) we just arm the run
    # without spawning — the loop is exercised separately.
    import asyncio
    try:
        asyncio.get_running_loop()
        self.spawn_background(self._team_dispatch_loop(room_id))
    except RuntimeError:
        pass
    return {"ok": True, "run": tb["run"]}


def team_stop_run(self, room_id: str) -> dict:
    room = self._load_agent(room_id)
    if not room:
        return {"error": "room not found"}
    tb = dict(_team_block(room))
    run = dict(tb.get("run") or {})
    if not run.get("active"):
        return {"ok": True, "already_stopped": True}
    run["stop_requested"] = True
    tb["run"] = run
    room["team"] = tb
    self._save_agent(room)
    return {"ok": True, "stopping": True}


def _team_dec_turn(self, room_id: str) -> int:
    """Decrement the run budget; return turns_left (or 0)."""
    room = self._load_agent(room_id)
    if not room:
        return 0
    tb = dict(_team_block(room))
    run = dict(tb.get("run") or {})
    run["turns_left"] = max(0, int(run.get("turns_left", 0)) - 1)
    tb["run"] = run
    room["team"] = tb
    self._save_agent(room)
    return run["turns_left"]


def _team_finish_run(self, room_id: str, reason: str) -> None:
    room = self._load_agent(room_id)
    if not room:
        return
    tb = dict(_team_block(room))
    run = dict(tb.get("run") or {})
    run["active"] = False
    run["finished"] = _now()
    run["reason"] = reason
    tb["run"] = run
    room["team"] = tb
    # Deposit any unfinished assigned work into the assignee's durable mailbox
    # so it survives task-list edits and resumes on the next run (dark flag).
    if _team_mailbox_enabled(self):
        _team_strand_to_mailbox(self, room, room_id)
    self._save_agent(room)


# ── Inter-agent mailbox wiring (durable strand → resume; dark flag) ──────────
# The dispatch loop runs recipients in-process and strands assigned `todo`
# tasks when a run's budget/stop hits. These deposit stranded work into the
# assignee's agent_mailbox on run end and re-hydrate it on the next run start,
# so a handoff survives across runs (and task-list edits). First consumer of
# emptyos/sdk/agent_mailbox.py; the SDK helper graduates on the 2nd consumer
# (staff Connect/Root/Growth agents). Gated by [apps.rooms] feature.team-mailbox.


def _team_mailbox_enabled(self) -> bool:
    # Degrade to the dark default on a minimal kernel / test fake without config.
    try:
        return bool(self.app_config("feature.team-mailbox.enabled", False))
    except Exception:
        return False


def _team_strand_to_mailbox(self, room: dict, room_id: str) -> int:
    """Enqueue each unfinished (todo + assigned) task into its assignee's
    mailbox. Returns the count deposited."""
    n = 0
    for t in _team_block(room).get("tasks") or []:
        if t.get("status") == "todo" and (t.get("assignee") or "").strip():
            self.send_to_agent(
                t["assignee"],
                kind="team-task",
                subject=(t.get("content") or "")[:200],
                payload={
                    "room_id": room_id,
                    "task_id": t.get("id"),
                    "content": t.get("content") or "",
                    "assignee": t["assignee"],
                },
            )
            n += 1
    return n


def _team_drain_mailbox(self, room: dict, room_id: str) -> int:
    """Re-hydrate stranded team tasks from this room's participants' mailboxes
    (dedup by task_id), acking each handled message. Mutates room in place;
    returns the count re-added."""
    tb = dict(_team_block(room))
    tasks = list(tb.get("tasks") or [])
    have = {t.get("id") for t in tasks}
    added = 0
    for p in self._normalize_participants(room):
        pid = (p.get("id") or "").strip()
        if not pid:
            continue
        for msg in self.agent_inbox(pid):
            pay = msg.get("payload") or {}
            if pay.get("room_id") != room_id:
                continue  # for a different room — leave it for that room's drain
            tid = pay.get("task_id")
            if not tid or tid in have:
                # No id, or already in the list → handled; ack it.
                self.agent_inbox_ack(msg.get("id"), pid)
                continue
            if len(tasks) >= TEAM_MAX_TASKS:
                # List full — leave the message PENDING (no silent drop); it
                # retries on the next run once the list has room.
                continue
            tasks.append({
                "id": tid,
                "content": pay.get("content") or "",
                "assignee": pay.get("assignee") or pid,
                "status": "todo",
                "result": "",
            })
            have.add(tid)
            added += 1
            self.agent_inbox_ack(msg.get("id"), pid)
    if added:
        tb["tasks"] = tasks
        room["team"] = tb
    return added


def _team_lead_nudge(self, task: dict, result: str) -> str:
    return (
        f"[team] Worker '{task.get('assignee')}' finished task {task.get('id')} "
        f"(\"{task.get('content')}\"). Result: {result[:600]}\n"
        "Review the shared task list. Assign remaining work to a worker with "
        "[DO:rooms.team_assign({\"task_id\":\"...\",\"assignee\":\"...\"})], add a new "
        "task with [DO:rooms.team_add_task({\"content\":\"...\",\"assignee\":\"...\"})], "
        "or — if everything is done — say the work is complete and assign nothing."
    )


async def _team_turn_or_timeout(self, room: dict, participant: dict,
                                text: str) -> tuple[dict, bool]:
    """Run ONE team turn under `TEAM_TURN_TIMEOUT_S`; return (result, timed_out).

    Every team turn goes through here so a new turn type cannot silently ship
    without the guard — which is exactly how the lead turn was awarded a bare
    `await` and froze runs indefinitely. The recovery policy stays with the
    caller: the two turn types deliberately differ in what they do on timeout.
    """
    try:
        res = await asyncio.wait_for(
            self._run_participant_turn(
                room, participant, text, source="team", apply_team_tokens=True,
            ),
            timeout=TEAM_TURN_TIMEOUT_S,
        )
        return (res or {}), False
    except (TimeoutError, asyncio.TimeoutError):
        return {}, True


async def _team_dispatch_loop(self, room_id: str) -> None:
    """Bounded, stoppable orchestrator. Dispatches the next actionable task's
    worker, then a lead turn to react, until no actionable tasks remain, the
    budget is exhausted, or a stop is requested."""
    reason = "done"
    try:
        while True:
            room = self._load_agent(room_id)
            if not room:
                reason = "gone"
                break
            run = _team_block(room).get("run") or {}
            if not run.get("active"):
                reason = "inactive"
                break
            if run.get("stop_requested"):
                reason = "stopped"
                break
            if int(run.get("turns_left", 0)) <= 0:
                reason = "budget"
                break

            task = self._team_next_actionable(room)
            if not task:
                reason = "done"
                break

            worker = self._participant_by_id(room, task.get("assignee") or "")
            if not worker:
                self.team_set_status(room_id, task["id"], "blocked",
                                     result=f"no participant '{task.get('assignee')}'")
                continue

            # ── Worker turn ──
            self.team_set_status(room_id, task["id"], "doing")
            self._team_dec_turn(room_id)
            worker_text = (
                f"[team] You are assigned task {task['id']}: {task['content']}\n"
                "Complete it now and report your result concisely. When done, emit "
                f"[DO:rooms.team_set_status({{\"task_id\":\"{task['id']}\","
                "\"status\":\"done\",\"result\":\"<one-line result>\"}})]."
            )
            res, timed_out = await self._team_turn_or_timeout(room, worker, worker_text)
            if timed_out:
                # A hung worker turn (slow model / human-think) must not freeze
                # the run. Block the task so it isn't retried, and halt — the
                # next turn would likely hang the same way.
                self.team_set_status(room_id, task["id"], "blocked",
                                     result="worker turn timed out")
                reason = "turn_timeout"
                break
            # Fallback: if the worker didn't flip status, the loop closes it out.
            room2 = self._load_agent(room_id)
            t2 = self._team_find_task(room2, task["id"]) if room2 else None
            if t2 and t2.get("status") == "doing":
                self.team_set_status(room_id, task["id"], "done",
                                     result=(res.get("reply") or "")[:500])

            # Re-check the gate before spending a lead turn.
            room = self._load_agent(room_id)
            run = _team_block(room).get("run") or {} if room else {}
            if not run.get("active") or run.get("stop_requested"):
                reason = "stopped" if run.get("stop_requested") else "inactive"
                break
            if int(run.get("turns_left", 0)) <= 0:
                reason = "budget"
                break

            # ── Lead turn (reacts, reassigns, or declares complete) ──
            lead = self._team_lead_participant(room)
            if lead:
                self._team_dec_turn(room_id)
                room = self._load_agent(room_id)
                done_task = self._team_find_task(room, task["id"]) or task
                nudge = self._team_lead_nudge(done_task, done_task.get("result") or "")
                _, timed_out = await self._team_turn_or_timeout(room, lead, nudge)
                if timed_out:
                    # A hung lead turn must not freeze the run either. Unlike
                    # the worker branch above we do NOT touch the task: the
                    # worker's turn has already concluded and settled its
                    # status/result (via its own [DO:] token or the fallback
                    # above), so overwriting it here would discard real work.
                    # Only the lead's reaction is lost. Distinct reason so the
                    # surfaced "(last run: ...)" says which side hung.
                    reason = "lead_turn_timeout"
                    break
        self._team_finish_run(room_id, reason)
    except Exception as e:  # never let the loop crash silently
        try:
            self.kernel.syslog.error("rooms-team", f"dispatch loop {room_id}: {e}")
        except Exception:
            pass
        self._team_finish_run(room_id, "error")
        reason = "error"
    try:
        await self.emit("rooms:team_run_finished", {"room_id": room_id, "reason": reason})
    except Exception:
        pass


# ── System-prompt block (role-gated; consumed by _build_system / _build_cli_system) ──


def _team_prompt_block(self, room: dict, participant: dict) -> str:
    """Lead/worker coordination framing + the current task list, inline.

    Empty when the room is not a team or the participant is a peer — so
    non-team rooms get zero extra prompt and behave exactly as before.
    """
    if not isinstance(participant, dict):
        return ""
    role = participant.get("role", "peer")
    if role not in ("lead", "worker") or not self._team_enabled(room):
        return ""
    tasks = _team_block(room).get("tasks") or []

    def _fmt(ts):
        if not ts:
            return "  (none)"
        return "\n".join(
            f"  - {t['id']} [{t.get('status', 'todo')}]"
            f"{(' @' + t['assignee']) if t.get('assignee') else ''}: {t.get('content', '')}"
            for t in ts
        )

    if role == "lead":
        return (
            "You are the TEAM LEAD of this room. Coordinate the work via the shared "
            "task list below. Break the goal into tasks, assign each to a worker by "
            "their participant id, and track progress. Manage the list ONLY through "
            "these tokens (they take effect immediately during a team run):\n"
            "  [DO:rooms.team_add_task({\"content\":\"...\",\"assignee\":\"<worker id>\"})]\n"
            "  [DO:rooms.team_assign({\"task_id\":\"...\",\"assignee\":\"<worker id>\"})]\n"
            "  [DO:rooms.team_set_status({\"task_id\":\"...\",\"status\":\"done|blocked\"})]\n"
            "When every task is done, say the work is complete and do not assign more.\n\n"
            "Shared task list:\n" + _fmt(tasks)
        )
    # worker
    mine = [t for t in tasks if t.get("assignee") == participant.get("id")
            and t.get("status") in ("todo", "doing")]
    return (
        "You are a WORKER on this team. Do the task assigned to you, then report "
        "your result concisely. Mark it complete with "
        "[DO:rooms.team_set_status({\"task_id\":\"...\",\"status\":\"done\","
        "\"result\":\"<one line>\"})]. For any state change OUTSIDE the team list "
        "(editing a note, sending a message), emit the normal [DO:app.method(...)] "
        "token — those go through the review gate as usual.\n\n"
        "Your open tasks:\n" + _fmt(mine)
    )


def _fire(self, event: str, data: dict) -> None:
    """Fire-and-forget emit (these run inside sync verb methods called from
    HTTP handlers; never block the request on the bus). Check for a running
    loop BEFORE building the coroutine so a sync caller (unit test) doesn't
    leave an un-awaited coroutine warning behind."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (e.g. unit test calling the verb directly) — skip
    loop.create_task(self.emit(event, data))


# ── HTTP wrappers (Team panel) ───────────────────────────────────────────


@web_route("GET", "/api/rooms/{room_id}/team")
async def api_team_list(self, request):
    return self.team_list(request.path_params["room_id"])


@web_route("POST", "/api/rooms/{room_id}/team/tasks")
async def api_team_add_task(self, request):
    body = await request.json()
    return self.team_add_task(
        request.path_params["room_id"],
        (body or {}).get("content", ""),
        (body or {}).get("assignee", ""),
    )


@web_route("POST", "/api/rooms/{room_id}/team/tasks/{task_id}/assign")
async def api_team_assign(self, request):
    body = await request.json()
    return self.team_assign(
        request.path_params["room_id"],
        request.path_params["task_id"],
        (body or {}).get("assignee", ""),
    )


@web_route("POST", "/api/rooms/{room_id}/team/tasks/{task_id}/status")
async def api_team_set_status(self, request):
    body = await request.json()
    return self.team_set_status(
        request.path_params["room_id"],
        request.path_params["task_id"],
        (body or {}).get("status", ""),
        (body or {}).get("result", ""),
    )


@web_route("POST", "/api/rooms/{room_id}/team/role")
async def api_team_set_role(self, request):
    body = await request.json()
    return self.set_participant_role(
        request.path_params["room_id"],
        (body or {}).get("participant_id", ""),
        (body or {}).get("role", ""),
    )


@web_route("POST", "/api/rooms/{room_id}/team/run/start")
async def api_team_run_start(self, request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return self.team_start_run(
        request.path_params["room_id"],
        (body or {}).get("max_turns", TEAM_DEFAULT_MAX_TURNS),
    )


@web_route("POST", "/api/rooms/{room_id}/team/run/stop")
async def api_team_run_stop(self, request):
    return self.team_stop_run(request.path_params["room_id"])
