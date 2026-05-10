"""Scheduler — cron-based job execution for @scheduled app methods.

Uses APScheduler to run decorated methods on cron schedules.
Jobs are registered when apps are loaded and removed when apps stop.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


class Scheduler:
    """Manages scheduled jobs for apps."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self._scheduler = None
        self._running = False

    async def start(self):
        """Start the scheduler."""
        if not self.kernel.config.get("scheduler.enabled", True):
            return

        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError:
            print("[Scheduler] APScheduler not installed, skipping")
            return

        tz = self.kernel.config.get("scheduler.timezone", "UTC")
        self._scheduler = AsyncIOScheduler(timezone=tz)
        self._scheduler.start()
        self._running = True
        print(f"[Scheduler] Started (timezone: {tz})")

    async def stop(self):
        """Stop the scheduler and all jobs."""
        if self._scheduler and self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False

    def register_app_jobs(self, app_id: str, instance):
        """Scan an app instance for @scheduled methods and register them."""
        if not self._scheduler or not self._running:
            return

        from apscheduler.triggers.cron import CronTrigger

        methods = (
            instance._get_decorated("_eos_scheduled") if hasattr(instance, "_get_decorated") else []
        )

        for meta, method in methods:
            cron_expr = meta["cron"]
            job_id = f"{app_id}:{meta['id']}"

            try:
                trigger = CronTrigger.from_crontab(cron_expr)
            except ValueError as e:
                print(f"[Scheduler] Invalid cron '{cron_expr}' for {job_id}: {e}")
                continue

            async def job_wrapper(m=method, jid=job_id, aid=app_id):
                try:
                    result = m()
                    if asyncio.iscoroutine(result):
                        await result
                    await self.kernel.events.emit(
                        "scheduler:job:executed",
                        {"job_id": jid, "app": aid},
                        source="scheduler",
                    )
                except Exception as e:
                    print(f"[Scheduler] Job {jid} failed: {e}")
                    await self.kernel.events.emit(
                        "scheduler:job:failed",
                        {"job_id": jid, "app": aid, "error": str(e)},
                        source="scheduler",
                    )

            self._scheduler.add_job(
                job_wrapper,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
            )
            print(f"[Scheduler] Registered job {job_id} ({cron_expr})")

    def unregister_app_jobs(self, app_id: str):
        """Remove all jobs for an app."""
        if not self._scheduler:
            return
        for job in self._scheduler.get_jobs():
            if job.id.startswith(f"{app_id}:"):
                job.remove()

    @property
    def jobs(self) -> list[dict]:
        """List all registered jobs."""
        if not self._scheduler:
            return []
        return [
            {
                "id": job.id,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
            for job in self._scheduler.get_jobs()
        ]
