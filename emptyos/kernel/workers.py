"""Compute Workers — background job queue for GPU/heavy tasks.

Submit async callables, track status, coordinate GPU access.
Registered as kernel service "workers".
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


class JobState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    name: str
    state: JobState = JobState.PENDING
    result: Any = None
    error: str = ""
    submitted_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    source: str = ""  # app that submitted it
    metadata: dict = field(default_factory=dict)


class WorkerPool:
    """Asyncio-based background job queue.

    - Submit coroutine functions → get job ID
    - Poll status or await result
    - Configurable max_workers (default 1 for GPU coordination)
    - Emits events: job:started, job:completed, job:failed
    """

    def __init__(self, kernel: Kernel, max_workers: int = 1):
        self.kernel = kernel
        self.max_workers = max_workers
        self._jobs: dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._funcs: dict[str, tuple[Callable, tuple, dict]] = {}

    async def start(self):
        """Start worker tasks."""
        for i in range(self.max_workers):
            task = asyncio.create_task(self._worker_loop(i))
            self._workers.append(task)

    async def stop(self):
        """Stop all workers."""
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    async def submit(
        self,
        func: Callable[..., Coroutine],
        *args,
        name: str = "",
        source: str = "",
        metadata: dict | None = None,
        **kwargs,
    ) -> str:
        """Submit a coroutine function for background execution. Returns job ID."""
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            name=name or func.__name__,
            submitted_at=time.time(),
            source=source,
            metadata=metadata or {},
        )
        self._jobs[job_id] = job
        self._funcs[job_id] = (func, args, kwargs)
        await self._queue.put(job_id)
        return job_id

    def status(self, job_id: str) -> dict | None:
        """Get job status."""
        job = self._jobs.get(job_id)
        if not job:
            return None
        return {
            "id": job.id,
            "name": job.name,
            "state": job.state.value,
            "error": job.error,
            "source": job.source,
            "submitted_at": job.submitted_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "metadata": job.metadata,
        }

    def result(self, job_id: str) -> Any:
        """Get job result (None if not completed)."""
        job = self._jobs.get(job_id)
        return job.result if job and job.state == JobState.COMPLETED else None

    def cancel(self, job_id: str) -> bool:
        """Cancel a pending job."""
        job = self._jobs.get(job_id)
        if job and job.state == JobState.PENDING:
            job.state = JobState.CANCELLED
            return True
        return False

    def list_jobs(self, limit: int = 50) -> list[dict]:
        """List recent jobs."""
        jobs = sorted(self._jobs.values(), key=lambda j: j.submitted_at, reverse=True)
        return [self.status(j.id) for j in jobs[:limit]]

    @property
    def pending_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.state == JobState.PENDING)

    @property
    def running_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.state == JobState.RUNNING)

    async def _worker_loop(self, worker_id: int):
        """Worker loop — pick jobs from queue and execute."""
        while True:
            try:
                job_id = await self._queue.get()
                job = self._jobs.get(job_id)
                if not job or job.state == JobState.CANCELLED:
                    continue

                func, args, kwargs = self._funcs.pop(job_id, (None, None, None))
                if not func:
                    continue

                job.state = JobState.RUNNING
                job.started_at = time.time()
                await self.kernel.events.emit(
                    "job:started", {"id": job_id, "name": job.name}, source="workers"
                )

                try:
                    job.result = await func(*args, **kwargs)
                    job.state = JobState.COMPLETED
                    job.completed_at = time.time()
                    await self.kernel.events.emit(
                        "job:completed",
                        {
                            "id": job_id,
                            "name": job.name,
                            "duration": round(job.completed_at - job.started_at, 2),
                        },
                        source="workers",
                    )
                except Exception as e:
                    job.state = JobState.FAILED
                    job.error = str(e)
                    job.completed_at = time.time()
                    await self.kernel.events.emit(
                        "job:failed",
                        {"id": job_id, "name": job.name, "error": str(e)},
                        source="workers",
                    )

                # Cleanup old jobs (keep last 200)
                if len(self._jobs) > 200:
                    old = sorted(self._jobs.values(), key=lambda j: j.submitted_at)
                    for j in old[: len(self._jobs) - 200]:
                        if j.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
                            self._jobs.pop(j.id, None)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Workers] Worker {worker_id} error: {e}")
