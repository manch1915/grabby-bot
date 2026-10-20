import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fakeredis.aioredis import FakeRedis

from grabby.config import Settings
from grabby.domain.errors import ErrorKind
from grabby.domain.jobs import Batch, Job
from grabby.domain.links import Link
from grabby.domain.media import MediaBundle, MediaItem, MediaKind, PostInfo
from grabby.fetch.protocol import FetchRequest, FetchResult
from grabby.infra.store import CachedFile, CacheStore, JobStore, StatsStore
from grabby.services.pipeline import Outcome, Pipeline
from grabby.services.scheduler import FairScheduler
from grabby.services.storage import WorkDir


class FakeFetcher:
    """Answers per URL; a delay lets tests finish jobs out of order."""

    def __init__(self) -> None:
        self.delays: dict[str, float] = {}
        self.errors: dict[str, ErrorKind] = {}
        self.calls: list[str] = []
        self.block = asyncio.Event()
        self.block.set()
        self._cancelled: set[str] = set()

    async def fetch(
        self, key: str, request: FetchRequest, *, extractor_path: Path | None = None
    ) -> FetchResult:
        self.calls.append(request.url)
        await asyncio.sleep(self.delays.get(request.url, 0))
        await self.block.wait()
        if key in self._cancelled:
            return FetchResult(error_kind=ErrorKind.CANCELLED)
        if (kind := self.errors.get(request.url)) is not None:
            return FetchResult(error_kind=kind, error="boom")
        path = Path(request.workdir) / "clip.mp4"
        path.write_bytes(b"x" * 10)
        item = MediaItem(str(path), MediaKind.VIDEO, size=10, width=720, height=1280, duration=3)
        return FetchResult(bundle=MediaBundle(info=PostInfo(url=request.url, author="@a"), items=[item]))

    def cancel(self, key: str) -> bool:
        self._cancelled.add(key)
        self.block.set()
        return True


class FakeResolver:
    async def resolve_all(self, links: list[Link]) -> list[Link]:
        return links


@dataclass
class FakeMessenger:
    delivered: list[tuple[int, str, bool, bool]] = field(default_factory=list)  # index, url, ok, cached
    finished: list[tuple[Batch, list[Job]]] = field(default_factory=list)
    statuses: int = 0
    done: asyncio.Event = field(default_factory=asyncio.Event)

    async def status_new(self, batch: Batch) -> int | None:
        return 100

    async def status_update(self, batch: Batch) -> None:
        self.statuses += 1

    async def deliver(self, batch: Batch, outcome: Outcome) -> list[CachedFile]:
        job = outcome.job
        self.delivered.append((job.index, job.url, outcome.ok, outcome.cached is not None))
        return [CachedFile("video", f"file-{job.index}")] if outcome.bundle else []

    async def finish(self, batch: Batch, failures: list[Job]) -> None:
        self.finished.append((batch, failures))
        self.done.set()


@dataclass
class Env:
    pipeline: Pipeline
    fetcher: FakeFetcher
    messenger: FakeMessenger
    redis: FakeRedis
    settings: Settings
    workdir: WorkDir

    def rebuild(self) -> "Env":
        """A fresh pipeline over the same Redis, as after a restart."""
        return make_env(self.settings, self.redis, self.workdir.root)


def make_env(settings: Settings, redis: FakeRedis, root: Path) -> Env:
    fetcher, messenger = FakeFetcher(), FakeMessenger()
    workdir = WorkDir(root, limit_mb=100, min_free_mb=0)
    workdir.reset()
    pipeline = Pipeline(
        settings=settings,
        scheduler=FairScheduler(max_parallel=4, per_user=4, platform_limit=lambda p: 4),
        fetcher=fetcher,  # type: ignore[arg-type]
        jobs=JobStore(redis),
        cache=CacheStore(redis, 1),
        stats=StatsStore(redis),
        workdir=workdir,
        resolver=FakeResolver(),  # type: ignore[arg-type]
        messenger=messenger,
    )
    return Env(pipeline, fetcher, messenger, redis, settings, workdir)


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    settings = Settings(
        bot_token="1:x", work_dir=tmp_path / "work", data_dir=tmp_path / "data", status_edit_interval_s=0
    )  # type: ignore[arg-type]
    redis = FakeRedis(decode_responses=True)
    environment = make_env(settings, redis, settings.work_dir)
    yield environment
    await environment.pipeline.stop()
