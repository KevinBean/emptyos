"""CronRuleScheduler — register a set of records as cron jobs, keep them synced.

Both ``apps/personal/staff`` and the mail email-routines feature grew the same
shape: an app owns a list of dict records, each with an id + a cron + a "should
this be scheduled right now" predicate + a per-record job coroutine, and needs
to register them all, sync one after an edit, drop one on delete, and clear them
on teardown — under a stable ``<prefix><id>`` job-id namespace, tracking which
ids are live. Extracted from that duplication per CLAUDE.md rule 9 (the second
caller). The divergent half — how each app *stores* + seeds its records (staff
syncs fields on a changed default; mail just adds-missing) — is deliberately NOT
extracted; only the cron register/sync/unregister skeleton is.

The app supplies four callables; this owns the job-id prefixing, the
remove-then-add sync, the live-id set, and the scheduler-unavailable warning.
It wraps ``BaseApp.add_cron_job`` / ``remove_cron_job`` (the real primitives),
so it needs nothing from the app but those two methods + ``log_warn``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class CronRuleScheduler:
    """Owns the ``<prefix><id>`` cron jobs for one app's list of rule records."""

    def __init__(
        self,
        app: Any,
        *,
        prefix: str,
        id_of: Callable[[dict], str],
        cron_of: Callable[[dict], str],
        should_schedule: Callable[[dict], bool],
        make_job: Callable[[dict], Callable[[], Awaitable[None]]],
    ) -> None:
        self._app = app
        self._prefix = prefix
        self._id_of = id_of
        self._cron_of = cron_of
        self._should = should_schedule
        self._make_job = make_job
        self._job_ids: set[str] = set()

    @property
    def job_ids(self) -> set[str]:
        """Copy of the currently-live job ids (for tests / introspection)."""
        return set(self._job_ids)

    def job_id(self, record: dict) -> str:
        return f"{self._prefix}{self._id_of(record)}"

    def register_all(self, records: list[dict]) -> None:
        """(Re)register every record. Idempotent — a record that shouldn't be
        scheduled ends up with no job; already-registered ones are refreshed."""
        for r in records:
            self.sync(r)

    def sync(self, record: dict) -> None:
        """Remove-then-add one record's job to reflect its current state."""
        jid = self.job_id(record)
        self._app.remove_cron_job(jid)
        self._job_ids.discard(jid)
        if not self._should(record):
            return
        if self._app.add_cron_job(jid, self._make_job(record), cron=self._cron_of(record)):
            self._job_ids.add(jid)
        else:
            self._app.log_warn(
                f"{jid} — scheduler unavailable or invalid cron {self._cron_of(record)!r}"
            )

    def unregister(self, record: dict) -> None:
        """Drop one record's job (on delete)."""
        jid = self.job_id(record)
        self._app.remove_cron_job(jid)
        self._job_ids.discard(jid)

    def unregister_all(self) -> None:
        """Drop every live job (on teardown)."""
        for jid in list(self._job_ids):
            self._app.remove_cron_job(jid)
        self._job_ids.clear()
