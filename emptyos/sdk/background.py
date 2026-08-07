"""Tracked fire-and-forget tasks — one implementation for apps and plugins.

asyncio holds only a WEAK reference to a running task:

    "Save a reference to the result of this function, to avoid a task
     disappearing mid-execution."  — asyncio.create_task docs

So a detached task whose only reference was the local returned by
`create_task` can be collected before it finishes. It fails silently, more
often under memory pressure — which for a boot-time warm-up is exactly when
things are already going badly.

Extracted 2026-08-05 at the second consumer (CLAUDE.md rule 9). `BaseApp` had
the tracking inline for background event handlers and setup warm-ups; the
scanner that found the app-side sites also found seven in `plugins/`, three of
them `auto_start()` warm-ups — the same shape with no `BaseApp` in reach.
Rather than a second copy, both now wrap this.

Pure asyncio. No kernel, no BaseApp, no I/O — so it unit-tests without a daemon.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine


def spawn_tracked(
    coro: Coroutine[Any, Any, Any],
    *,
    tasks: set,
    on_error: Callable[[BaseException], None] | None = None,
) -> asyncio.Task:
    """Run `coro` detached, holding a strong reference in `tasks`.

    * The task is added to `tasks` and removed when it finishes, so the caller
      can cancel outstanding work at teardown.
    * `on_error(exc)` is called for a non-cancellation failure. Without it the
      exception would sit in the task until garbage collection, surfacing (if
      at all) as asyncio's "Task exception was never retrieved".
    * A task cancelled *before the loop ran its body* leaves `coro` created and
      never awaited, which Python reports as a RuntimeWarning at GC time — from
      a shutdown path where nobody is watching. It is closed explicitly so a
      racing teardown stays silent instead of adding noise to the log it races.
    """
    started = False

    async def _run():
        nonlocal started
        started = True
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — the point is that nothing escapes
            if on_error is not None:
                try:
                    on_error(e)
                except Exception:
                    pass

    def _done(t: asyncio.Task) -> None:
        tasks.discard(t)
        if not started:
            try:
                coro.close()
            except Exception:
                pass

    task = asyncio.create_task(_run())
    tasks.add(task)
    task.add_done_callback(_done)
    return task


def cancel_tracked(tasks: set) -> None:
    """Cancel everything still outstanding and clear the set. Teardown helper."""
    for task in list(tasks):
        task.cancel()
    tasks.clear()
