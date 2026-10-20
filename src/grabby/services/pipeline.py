"""Batches end to end: links in, jobs scheduled, downloads run, results delivered in order."""

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

from grabby.config import Settings
from grabby.domain.errors import ErrorKind
from grabby.domain.jobs import Batch, Job, JobStatus
from grabby.domain.links import Link, check_supported, find_urls, normalize
from grabby.domain.media import MediaBundle, Quality
from grabby.domain.platforms import Platform
from grabby.fetch.protocol import FetchRequest
from grabby.infra.store import CachedFile, CachedPost, CacheStore, JobStore, StatsStore
from grabby.services.fetcher import Fetcher
from grabby.services.netguard import is_public_host
from grabby.services.resolver import LinkResolver
from grabby.services.scheduler import FairScheduler
from grabby.services.storage import WorkDir

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Outcome:
    job: Job
    bundle: MediaBundle | None = None
    cached: CachedPost | None = None

    @property
    def ok(self) -> bool:
        return self.bundle is not None or self.cached is not None


class Messenger(Protocol):
    """What the pipeline needs from the chat side; the Telegram adapter implements it."""

    async def status_new(self, batch: Batch) -> int | None: ...

    async def status_update(self, batch: Batch) -> None: ...

    async def deliver(self, batch: Batch, outcome: Outcome) -> list[CachedFile]: ...

    async def finish(self, batch: Batch, failures: list[Job]) -> None: ...


@dataclass(slots=True)
class Submitted:
    batch: Batch | None
    added: int = 0
    duplicates: int = 0


@dataclass(slots=True)
class JobOptions:
    quality: Quality = Quality.MAX
    as_document: bool = False
    split_parts: bool = False


@dataclass(eq=False)
class _Runtime:
    batch: Batch
    outcomes: dict[int, Outcome] = field(default_factory=dict)
    jobs: dict[str, Job] = field(default_factory=dict)
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None
    last_edit: float = 0.0
    edit_pending: bool = False


class Pipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        scheduler: FairScheduler,
        fetcher: Fetcher,
        jobs: JobStore,
        cache: CacheStore,
        stats: StatsStore,
        workdir: WorkDir,
        resolver: LinkResolver,
        messenger: Messenger,
    ) -> None:
        self.settings = settings
        self.scheduler = scheduler
        self.fetcher = fetcher
        self.jobs = jobs
        self.cache = cache
        self.stats = stats
        self.workdir = workdir
        self.resolver = resolver
        self.messenger = messenger
        self._runtimes: dict[str, _Runtime] = {}
        self._submit_locks: dict[int, asyncio.Lock] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._background: set[asyncio.Task[None]] = set()
        self._proxies = itertools.cycle(settings.proxy_urls) if settings.proxy_urls else None

    # lifecycle

    async def start(self) -> None:
        await self._restore()
        self._workers = [
            asyncio.create_task(self._worker(), name=f"worker-{i}") for i in range(self.settings.max_parallel)
        ]

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        for job in self.scheduler.running:
            self.fetcher.cancel(job.id)
        await asyncio.gather(*self._workers, return_exceptions=True)
        for runtime in self._runtimes.values():
            if runtime.task:
                runtime.task.cancel()

    def active_job_ids(self) -> set[str]:
        return {job_id for rt in self._runtimes.values() for job_id in rt.jobs}

    # intake

    async def submit_text(
        self, *, user_id: int, chat_id: int, lang: str, texts: list[str], reply_to: int | None = None,
        options: JobOptions | None = None,
    ) -> Submitted:  # fmt: skip
        raw: list[str] = []
        for text in texts:
            # A bare entity value like "example.org/clip" has no scheme for find_urls to see.
            raw += find_urls(text) or ([text] if text and not any(c.isspace() for c in text) else [])
        links = [link for url in raw if (link := normalize(url)) is not None]
        return await self.submit_links(
            user_id=user_id, chat_id=chat_id, lang=lang, links=links, reply_to=reply_to, options=options
        )

    async def submit_links(
        self, *, user_id: int, chat_id: int, lang: str, links: list[Link], reply_to: int | None = None,
        options: JobOptions | None = None,
    ) -> Submitted:  # fmt: skip
        if not links:
            return Submitted(batch=None)
        options = options or JobOptions()
        resolved = await self.resolver.resolve_all(links)
        internal = {
            link.url
            for link in resolved
            if link.platform is Platform.OTHER and not await is_public_host(link.host)
        }
        unique: list[Link] = []
        seen: set[str] = set()
        duplicates = 0
        for link in resolved:
            if link.url in seen:
                duplicates += 1
                continue
            seen.add(link.url)
            unique.append(link)

        lock = self._submit_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            runtime = await self._open_runtime(user_id, chat_id, lang, reply_to)
            async with runtime.cond:
                batch = runtime.batch
                new_jobs = []
                for link in unique:
                    job = Job(
                        batch_id=batch.id, user_id=user_id, chat_id=chat_id, index=batch.total,
                        url=link.url, platform=link.platform, quality=options.quality,
                        as_document=options.as_document, split_parts=options.split_parts,
                    )  # fmt: skip
                    batch.total += 1
                    if link.url in internal:
                        job.status = JobStatus.FAILED
                        job.error_kind, job.error = ErrorKind.UNSUPPORTED, "not a public host"
                        batch.failed += 1
                        runtime.outcomes[job.index] = Outcome(job)
                    elif (reason := check_supported(link)) is not None:
                        job.status = JobStatus.FAILED
                        job.error_kind, job.error = ErrorKind.NOT_A_POST, reason.value
                        batch.failed += 1
                        runtime.outcomes[job.index] = Outcome(job)
                    runtime.jobs[job.id] = job
                    new_jobs.append(job)
                await self.jobs.add_jobs(batch, new_jobs)
                runtime.cond.notify_all()
            if batch.status_message_id is None:
                batch.status_message_id = await self.messenger.status_new(batch)
                await self.jobs.save_batch(batch)
            else:
                self._touch_status(runtime)
            await self.scheduler.push([j for j in new_jobs if j.status is JobStatus.QUEUED])
        return Submitted(batch=batch, added=len(new_jobs), duplicates=duplicates)

    async def _open_runtime(self, user_id: int, chat_id: int, lang: str, reply_to: int | None) -> _Runtime:
        batch_id = await self.jobs.open_batch_id(user_id)
        runtime = self._runtimes.get(batch_id) if batch_id else None
        if runtime is not None and not runtime.batch.closed and runtime.batch.chat_id == chat_id:
            return runtime
        batch = Batch(user_id=user_id, chat_id=chat_id, lang=lang, reply_to=reply_to)
        runtime = _Runtime(batch=batch)
        self._runtimes[batch.id] = runtime
        await self.jobs.set_open_batch(user_id, batch.id)
        runtime.task = asyncio.create_task(self._deliver_loop(runtime), name=f"deliver-{batch.id}")
        return runtime

    # user actions

    async def cancel(self, user_id: int) -> int:
        dropped = await self.scheduler.cancel_user(user_id)
        for job in dropped:
            await self._complete(job, Outcome(job), status=JobStatus.CANCELLED)
        killed = 0
        for job in self.scheduler.running:
            if job.user_id == user_id and self.fetcher.cancel(job.id):
                killed += 1
        return len(dropped) + killed

    async def retry_failed(self, batch_id: str, user_id: int) -> Submitted:
        batch = await self.jobs.get_batch(batch_id)
        if batch is None or batch.user_id != user_id:
            return Submitted(batch=None)
        failed = [
            j
            for j in await self.jobs.batch_jobs(batch_id)
            if j.status in (JobStatus.FAILED, JobStatus.CANCELLED)
            and j.error_kind is not ErrorKind.NOT_A_POST
        ]
        links = [Link(url=j.url, platform=j.platform) for j in failed]
        return await self.submit_links(user_id=user_id, chat_id=batch.chat_id, lang=batch.lang, links=links)

    async def resubmit(self, job_id: str, user_id: int, options: JobOptions) -> Submitted:
        job = await self.jobs.get_job(job_id)
        if job is None or job.user_id != user_id:
            return Submitted(batch=None)
        batch = await self.jobs.get_batch(job.batch_id)
        lang = batch.lang if batch else "ru"
        return await self.submit_links(
            user_id=user_id, chat_id=job.chat_id, lang=lang,
            links=[Link(url=job.url, platform=job.platform)], options=options,
        )  # fmt: skip

    def queue_size(self, user_id: int | None = None) -> int:
        return self.scheduler.queued(user_id)

    # workers

    async def _worker(self) -> None:
        while True:
            job = await self.scheduler.next()
            try:
                await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job %s crashed", job.id)
                job.error_kind, job.error = ErrorKind.UNKNOWN, "internal error"
                await self._complete(job, Outcome(job), status=JobStatus.FAILED)
            finally:
                await self.scheduler.release(job)

    async def _run(self, job: Job) -> None:
        if job.id not in self.active_job_ids():
            return  # batch was finished or dropped meanwhile
        job.status = JobStatus.RUNNING
        await self.jobs.save_job(job)

        cached = await self.cache.get(job.cache_key)
        if cached is not None:
            await self._complete(job, Outcome(job, cached=cached), status=JobStatus.DONE)
            return

        while True:
            job.attempts += 1
            result = await self.fetcher.fetch(job.id, self._request(job))
            if result.ok:
                await self._complete(job, Outcome(job, bundle=result.bundle), status=JobStatus.DONE)
                return
            kind = result.error_kind or ErrorKind.UNKNOWN
            self.workdir.release(job.id)
            if kind.retryable and job.attempts <= self.settings.max_retries:
                delay = self.settings.retry_base_delay_s * 2 ** (job.attempts - 1)
                log.info("retrying %s in %ss after %s", job.url, delay, kind)
                await asyncio.sleep(delay)
                continue
            job.error_kind, job.error = kind, result.error[:500]
            status = JobStatus.CANCELLED if kind is ErrorKind.CANCELLED else JobStatus.FAILED
            await self._complete(job, Outcome(job), status=status)
            return

    def _request(self, job: Job) -> FetchRequest:
        proxy = None
        if self._proxies is not None and job.platform in self.settings.proxy_platforms:
            proxy = next(self._proxies)
        cookies = self.settings.cookies_for(job.platform)
        return FetchRequest(
            url=job.url,
            platform=job.platform.value,
            workdir=str(self.workdir.job_dir(job.id)),
            quality=job.quality,
            as_document=job.as_document,
            max_items=self.settings.max_items_per_link,
            cookies_file=str(cookies) if cookies else None,
            proxy=proxy,
            split_parts=job.split_parts,
        )

    async def _complete(self, job: Job, outcome: Outcome, *, status: JobStatus) -> None:
        runtime = self._runtimes.get(job.batch_id)
        job.status = status
        await self.jobs.save_job(job)
        if status is not JobStatus.CANCELLED:
            await self.stats.record(job.platform, status is JobStatus.DONE, error=job.error_kind)
        if runtime is None:
            self.workdir.release(job.id)
            return
        async with runtime.cond:
            batch = runtime.batch
            if status is JobStatus.DONE:
                batch.done += 1
            elif status is JobStatus.FAILED:
                batch.failed += 1
            else:
                batch.cancelled += 1
            runtime.outcomes[job.index] = outcome
            runtime.cond.notify_all()
        await self.jobs.save_batch(runtime.batch)
        self._touch_status(runtime)

    # delivery

    async def _deliver_loop(self, runtime: _Runtime) -> None:
        batch = runtime.batch
        try:
            while True:
                async with runtime.cond:
                    await runtime.cond.wait_for(
                        lambda: batch.delivered in runtime.outcomes or (batch.total and batch.complete)
                    )
                    if batch.complete:
                        await self._close(runtime)
                        return
                    outcome = runtime.outcomes.pop(batch.delivered)
                await self._deliver_one(batch, outcome)
                async with runtime.cond:
                    batch.delivered += 1
                    runtime.jobs.pop(outcome.job.id, None)
                await self.jobs.save_batch(batch)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("delivery loop for batch %s died", batch.id)

    async def _deliver_one(self, batch: Batch, outcome: Outcome) -> None:
        job = outcome.job
        try:
            files = await self.messenger.deliver(batch, outcome)
            if outcome.bundle is not None and files:
                info = {
                    "url": outcome.bundle.info.url,
                    "author": outcome.bundle.info.author,
                    "title": outcome.bundle.info.title,
                    "text": outcome.bundle.info.text,
                }
                await self.cache.put(job.cache_key, files, info)
                await self.stats.record_sent(outcome.bundle.total_size, cached=False)
            elif outcome.cached is not None:
                await self.stats.record_sent(0, cached=True)
        except Exception:
            log.exception("could not deliver %s", job.url)
            if outcome.cached is not None:
                await self.cache.drop(job.cache_key)
        finally:
            self.workdir.release(job.id)

    async def _close(self, runtime: _Runtime) -> None:
        batch = runtime.batch
        await self.jobs.finish_batch(batch)
        if await self.jobs.open_batch_id(batch.user_id) == batch.id:
            await self.jobs.set_open_batch(batch.user_id, None)
        self._runtimes.pop(batch.id, None)
        failures = [
            j
            for j in await self.jobs.batch_jobs(batch.id)
            if j.status in (JobStatus.FAILED, JobStatus.CANCELLED)
        ]
        try:
            await self.messenger.finish(batch, failures)
        except Exception:
            log.exception("could not send the summary for batch %s", batch.id)

    def _touch_status(self, runtime: _Runtime) -> None:
        if runtime.edit_pending:
            return
        runtime.edit_pending = True
        task = asyncio.create_task(self._edit_status(runtime))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _edit_status(self, runtime: _Runtime) -> None:
        # At most one edit per interval; the last change always lands.
        wait = runtime.last_edit + self.settings.status_edit_interval_s - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        runtime.edit_pending = False
        if runtime.batch.closed:
            return
        runtime.last_edit = time.monotonic()
        try:
            await self.messenger.status_update(runtime.batch)
        except Exception:
            log.exception("status update failed for batch %s", runtime.batch.id)

    # restart recovery

    async def _restore(self) -> None:
        for batch in await self.jobs.active_batches():
            runtime = _Runtime(batch=batch)
            requeue: list[Job] = []
            for job in await self.jobs.batch_jobs(batch.id):
                if job.index < batch.delivered:
                    continue
                runtime.jobs[job.id] = job
                if job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                    runtime.outcomes[job.index] = Outcome(job)
                    continue
                if job.status is JobStatus.DONE:
                    batch.done -= 1  # files are gone after a restart, download again
                job.status = JobStatus.QUEUED
                requeue.append(job)
            self._runtimes[batch.id] = runtime
            runtime.task = asyncio.create_task(self._deliver_loop(runtime), name=f"deliver-{batch.id}")
            await self.jobs.save_batch(batch)
            await self.scheduler.push(requeue)
            log.info("restored batch %s: %d jobs back in the queue", batch.id, len(requeue))
