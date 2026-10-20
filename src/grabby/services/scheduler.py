"""Fair job scheduling: users take turns, with caps per user, per site and overall."""

import asyncio
import contextlib
from collections import OrderedDict, deque
from collections.abc import Callable

from grabby.domain.jobs import Job
from grabby.domain.platforms import Platform

# How deep to look into one user's queue for a job whose site has a free slot.
_LOOKAHEAD = 4


class FairScheduler:
    def __init__(
        self,
        *,
        max_parallel: int,
        per_user: int,
        platform_limit: Callable[[Platform], int],
        can_start: Callable[[], bool] = lambda: True,
        recheck_interval: float = 5.0,
    ) -> None:
        self._max = max_parallel
        self._per_user = per_user
        self._platform_limit = platform_limit
        self._can_start = can_start
        self._recheck = recheck_interval
        self._queues: OrderedDict[int, deque[Job]] = OrderedDict()
        self._running: dict[str, Job] = {}
        self._by_user: dict[int, int] = {}
        self._by_platform: dict[Platform, int] = {}
        self._cond = asyncio.Condition()

    @property
    def running(self) -> list[Job]:
        return list(self._running.values())

    def queued(self, user_id: int | None = None) -> int:
        if user_id is not None:
            return len(self._queues.get(user_id, ()))
        return sum(len(q) for q in self._queues.values())

    async def push(self, jobs: list[Job], *, front: bool = False) -> None:
        async with self._cond:
            for job in reversed(jobs) if front else jobs:
                queue = self._queues.setdefault(job.user_id, deque())
                if front:
                    queue.appendleft(job)
                else:
                    queue.append(job)
            self._cond.notify_all()

    async def cancel_user(self, user_id: int) -> list[Job]:
        async with self._cond:
            return list(self._queues.pop(user_id, ()))

    async def next(self) -> Job:
        async with self._cond:
            while True:
                job = self._pick()
                if job is not None:
                    self._running[job.id] = job
                    self._by_user[job.user_id] = self._by_user.get(job.user_id, 0) + 1
                    self._by_platform[job.platform] = self._by_platform.get(job.platform, 0) + 1
                    return job
                # Disk space frees up without anyone notifying us, so poll as well.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._cond.wait(), timeout=self._recheck)

    async def release(self, job: Job) -> None:
        async with self._cond:
            if self._running.pop(job.id, None) is None:
                return
            self._by_user[job.user_id] -= 1
            self._by_platform[job.platform] -= 1
            self._cond.notify_all()

    def _pick(self) -> Job | None:
        if len(self._running) >= self._max or not self._queues or not self._can_start():
            return None
        for user_id in list(self._queues):
            queue = self._queues[user_id]
            if self._by_user.get(user_id, 0) >= self._per_user:
                continue
            for i, job in enumerate(list(queue)[:_LOOKAHEAD]):
                if self._by_platform.get(job.platform, 0) < self._platform_limit(job.platform):
                    del queue[i]
                    # Round robin: the user who just got a slot goes to the back.
                    self._queues.move_to_end(user_id)
                    if not queue:
                        del self._queues[user_id]
                    return job
        return None
