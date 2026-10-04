"""run_status — normalize each harness app's native run status to one of four
cross-app phases, so a single board (or any aggregate surface) can render runs
from app-builder, fix-agent, and dogfood-agent side-by-side.

Each app keeps its own rich native status vocabulary for its own UI — this
helper is only for the cross-app glance ("which runs need me right now?").
Three apps consume it the moment it lands (the `list_all()` row builders in
app-builder / fix-agent / dogfood-agent), which is why it's an SDK helper
rather than living in one app (CLAUDE.md rule 9).

The normalized `list_all()` row shape every harness returns (read by
run-center + the `harness-runs` board):
    id, harness, kind, title, target, status, phase,
    branch, diff_stat, started, finished,
    scope  — the bounded context the run is rooted in: a worktree path
             (fix-agent / app-builder), the vault/daemon under test
             (dogfood-agent), or a session id (agent). Distinct from
             `target` (the entity worked on). "" when not applicable.
             Borrowed from OpenGauss's project-scoped child agents — a
             spawned worker should always declare *where* it operates.

The four phases, narrowest to widest attention-demand:
    running       — in flight, nothing for you to do yet
    needs-review  — paused at a human gate (a `ready` run awaiting merge)
    done          — terminal, resolved, benign
    failed        — terminal, something went wrong (or was undone)

Native vocabularies covered (2026-05-30):
    app-builder / fix-agent : queued running ready merged no-changes error
                              interrupted verifying verified verify-failed
                              verify-timeout reverted discarded
    dogfood-agent           : running ok error interrupted
    agent_fleet             : idle working blocked ended unknown
                              ("unknown" is intentionally unmapped — see the
                               note in _PHASE_BY_STATUS; it resolves via the
                               `finished`-aware default, not a static entry.)
"""

from __future__ import annotations

#: Canonical phase order — use for kanban column order + select options.
RUN_PHASES: tuple[str, ...] = ("running", "needs-review", "done", "failed")

#: Color hints aligned with the boards `color_map` palette.
RUN_PHASE_COLORS: dict[str, str] = {
    "running": "amber",
    "needs-review": "purple",
    "done": "green",
    "failed": "red",
}

_PHASE_BY_STATUS: dict[str, str] = {
    # in flight
    "queued": "running",
    "running": "running",
    "verifying": "running",
    "idle": "running",
    "working": "running",
    # awaiting a human gate (the one state that wants your attention)
    "ready": "needs-review",
    "blocked": "needs-review",
    # terminal — resolved / benign
    "merged": "done",
    "verified": "done",
    "done": "done",
    "ok": "done",
    "no-changes": "done",
    "discarded": "done",
    "ended": "done",
    # NB: agent_fleet's "unknown" is deliberately NOT mapped here. It must fall
    # through to normalize_run_status's `finished`-aware default, because it
    # means two different things depending on `finished`:
    #   finished == ""  -> a LIVE session we haven't classified yet (the fleet
    #                      meets every already-running session mid-stream the
    #                      moment the feature flag is enabled) -> "running".
    #   finished  set   -> the TTL reconciler's transient unknown->ended step
    #                      -> "done".
    # A static "unknown": "done" entry answers both with "done", which hides a
    # live session from the very panel the fleet exists to populate. That is the
    # exact case the default below was written to protect.
    # terminal — went wrong, or the change didn't survive
    "error": "failed",
    "timeout": "failed",
    "interrupted": "failed",
    "verify-failed": "failed",
    "verify-timeout": "failed",
    "reverted": "failed",
}


def normalize_run_status(status: str | None, *, finished: object = None) -> str:
    """Map a native run status to one of :data:`RUN_PHASES`.

    Unknown statuses degrade gracefully rather than vanishing from the board:
    a run with a ``finished`` timestamp is assumed ``done``; one without is
    assumed still ``running``. This keeps a new native status (added by a
    future app) visible — worst case in the wrong-ish column — instead of
    silently dropping it.
    """
    key = (status or "").strip().lower()
    if key in _PHASE_BY_STATUS:
        return _PHASE_BY_STATUS[key]
    return "done" if finished else "running"
