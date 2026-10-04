"""Decorators for app methods — @cli_command, @web_route, @on_event, @scheduled."""

from __future__ import annotations

from collections.abc import Callable


def cli_command(name: str, help: str = ""):
    """Register a method as a CLI subcommand."""

    def decorator(func: Callable) -> Callable:
        func._eos_cli = {"name": name, "help": help}
        return func

    return decorator


def web_route(method: str, path: str, *, operator: bool = False):
    """Register a method as a web route.

    ``operator=True`` marks a route that only the machine's operator may call —
    writing config/network, installing or toggling code, opening host files. In
    ``user`` posture (public demo, hosted learner) the daemon refuses it with a
    403; in ``operator`` posture it behaves as before. Enforced once at
    ``_add_route`` (``emptyos/web/server.py``); see ``docs/AUTH.md`` § Operator
    vs user and ``.claude/rules/operator-posture.md``. Core ``@server.*`` routes
    use ``emptyos.posture.OPERATOR_ROUTES`` instead — this flag is for app
    ``@web_route`` handlers, which that table does not see.
    """

    def decorator(func: Callable) -> Callable:
        func._eos_web = {"method": method, "path": path, "operator": operator}
        return func

    return decorator


def on_event(event_type: str, *, background: bool = False):
    """Register a method as an event handler.

    ``background=True`` detaches the handler from bus dispatch: it is spawned
    as a task and the bus moves on immediately instead of awaiting it.

    Use it ONLY for pure side effects — a journal ripple, a notification, a
    log row, an ambient re-computation — where nothing downstream reads a
    result and ordering against other handlers does not matter.

    Why it exists: ``EventBus.emit`` awaits every handler serially in one task,
    so a slow handler pins the whole bus for its duration. Measured on the live
    daemon, ``reactor.on_learn_lesson_completed`` held it for **16.7 s** on a
    single lesson completion, and two others ran 1–3 s. That is the daemon-wedge
    class in ``.claude/rules/debugging.md``, reached by ordinary use rather than
    by any exotic bug.

    Do NOT reach for this to paper over a handler that is slow because it is
    *wrong* (an unbounded vault scan, a sync call blocking the loop). Detaching
    keeps the bus free but the work still runs — fix the work. Errors are still
    logged in the bus's own ``Handler error for <event>`` format, so a detached
    handler stays visible to trace-miner rather than failing silently.
    """

    def decorator(func: Callable) -> Callable:
        func._eos_event = {"type": event_type, "background": bool(background)}
        return func

    return decorator


def ws_route(path: str):
    """Register a method as a WebSocket endpoint."""

    def decorator(func: Callable) -> Callable:
        func._eos_ws = {"path": path}
        return func

    return decorator


def scheduled(cron: str, id: str = ""):
    """Register a method as a scheduled job."""

    def decorator(func: Callable) -> Callable:
        func._eos_scheduled = {"cron": cron, "id": id or func.__name__}
        return func

    return decorator
