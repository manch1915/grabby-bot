"""Redis persistence. Everything the bot must not lose on restart lives here."""

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from grabby.domain.errors import ErrorKind
from grabby.domain.jobs import Batch, Job, UserSettings
from grabby.domain.platforms import Platform

JOB_TTL = 7 * 24 * 3600


def _s(value: str | bytes) -> str:
    # Clients use decode_responses=True; this keeps the type checker honest.
    return value.decode() if isinstance(value, bytes) else value


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class JobStore:
    def __init__(self, redis: Redis) -> None:
        self.r = redis

    async def save_job(self, job: Job) -> None:
        await self.r.set(f"job:{job.id}", _dump(job.to_dict()), ex=JOB_TTL)

    async def get_job(self, job_id: str) -> Job | None:
        raw = await self.r.get(f"job:{job_id}")
        return Job.from_dict(json.loads(raw)) if raw else None

    async def save_batch(self, batch: Batch) -> None:
        await self.r.set(f"batch:{batch.id}", _dump(batch.to_dict()), ex=JOB_TTL)

    async def get_batch(self, batch_id: str) -> Batch | None:
        raw = await self.r.get(f"batch:{batch_id}")
        return Batch.from_dict(json.loads(raw)) if raw else None

    async def add_jobs(self, batch: Batch, jobs: list[Job]) -> None:
        pipe = self.r.pipeline()
        pipe.set(f"batch:{batch.id}", _dump(batch.to_dict()), ex=JOB_TTL)
        for job in jobs:
            pipe.set(f"job:{job.id}", _dump(job.to_dict()), ex=JOB_TTL)
        pipe.rpush(f"batch:{batch.id}:jobs", *[job.id for job in jobs])
        pipe.expire(f"batch:{batch.id}:jobs", JOB_TTL)
        pipe.sadd("batches:active", batch.id)
        await pipe.execute()

    async def batch_jobs(self, batch_id: str) -> list[Job]:
        ids = await self.r.lrange(f"batch:{batch_id}:jobs", 0, -1)
        if not ids:
            return []
        raws = await self.r.mget([f"job:{_s(i)}" for i in ids])
        return [Job.from_dict(json.loads(raw)) for raw in raws if raw]

    async def active_batches(self) -> list[Batch]:
        ids = await self.r.smembers("batches:active")
        batches = []
        for batch_id in ids:
            batch = await self.get_batch(_s(batch_id))
            if batch is None:
                await self.r.srem("batches:active", batch_id)
            else:
                batches.append(batch)
        return sorted(batches, key=lambda b: b.created)

    async def finish_batch(self, batch: Batch) -> None:
        batch.closed = True
        pipe = self.r.pipeline()
        pipe.set(f"batch:{batch.id}", _dump(batch.to_dict()), ex=JOB_TTL)
        pipe.srem("batches:active", batch.id)
        await pipe.execute()

    async def open_batch_id(self, user_id: int) -> str | None:
        value = await self.r.get(f"user:{user_id}:batch")
        return str(value) if value else None

    async def set_open_batch(self, user_id: int, batch_id: str | None) -> None:
        if batch_id is None:
            await self.r.delete(f"user:{user_id}:batch")
        else:
            await self.r.set(f"user:{user_id}:batch", batch_id, ex=JOB_TTL)


@dataclass(slots=True)
class CachedFile:
    kind: str
    file_id: str


@dataclass(slots=True)
class CachedPost:
    files: list[CachedFile]
    info: dict[str, Any]


class CacheStore:
    def __init__(self, redis: Redis, ttl_days: int) -> None:
        self.r = redis
        self.ttl = ttl_days * 24 * 3600

    async def get(self, key: str) -> CachedPost | None:
        raw = await self.r.get(f"cache:{key}")
        if not raw:
            return None
        data = json.loads(raw)
        return CachedPost(files=[CachedFile(**f) for f in data["files"]], info=data["info"])

    async def put(self, key: str, files: list[CachedFile], info: dict[str, Any]) -> None:
        payload = {"files": [{"kind": f.kind, "file_id": f.file_id} for f in files], "info": info}
        await self.r.set(f"cache:{key}", _dump(payload), ex=self.ttl)

    async def drop(self, key: str) -> None:
        await self.r.delete(f"cache:{key}")


class UserStore:
    def __init__(self, redis: Redis) -> None:
        self.r = redis

    async def settings(self, user_id: int) -> UserSettings | None:
        raw = await self.r.get(f"user:{user_id}:settings")
        return UserSettings.from_dict(json.loads(raw)) if raw else None

    async def save_settings(self, user_id: int, settings: UserSettings) -> None:
        await self.r.set(f"user:{user_id}:settings", _dump(settings.to_dict()))

    async def touch(self, user_id: int, name: str) -> None:
        await self.r.hset("users", str(user_id), _dump({"name": name, "seen": int(time.time())}))

    async def users_count(self) -> int:
        return int(await self.r.hlen("users"))

    async def access_mode(self, default: str) -> str:
        value = await self.r.get("access:mode")
        return str(value) if value else default

    async def set_access_mode(self, mode: str) -> None:
        await self.r.set("access:mode", mode)

    async def is_allowed(self, user_id: int) -> bool:
        return bool(await self.r.sismember("access:allow", str(user_id)))

    async def allow(self, user_id: int, value: bool = True) -> None:
        if value:
            await self.r.sadd("access:allow", str(user_id))
        else:
            await self.r.srem("access:allow", str(user_id))

    async def is_banned(self, user_id: int) -> bool:
        return bool(await self.r.sismember("access:banned", str(user_id)))

    async def ban(self, user_id: int, value: bool = True) -> None:
        if value:
            await self.r.sadd("access:banned", str(user_id))
        else:
            await self.r.srem("access:banned", str(user_id))


@dataclass(slots=True)
class StatsSnapshot:
    ok_by_platform: dict[str, int]
    failed_by_platform: dict[str, int]
    errors: dict[str, int]
    today_ok: int
    today_failed: int
    cache_hits: int
    bytes_sent: int


class StatsStore:
    def __init__(self, redis: Redis) -> None:
        self.r = redis

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    async def record(self, platform: Platform, ok: bool, *, error: ErrorKind | None = None) -> None:
        day = self._today()
        pipe = self.r.pipeline()
        pipe.hincrby("stats:ok" if ok else "stats:failed", platform.value, 1)
        pipe.hincrby(f"stats:day:{day}", "ok" if ok else "failed", 1)
        pipe.expire(f"stats:day:{day}", 40 * 24 * 3600)
        if error is not None:
            pipe.hincrby("stats:errors", error.value, 1)
        await pipe.execute()

    async def record_sent(self, size: int, *, cached: bool) -> None:
        pipe = self.r.pipeline()
        pipe.incrby("stats:bytes", size)
        if cached:
            pipe.incr("stats:cache_hits")
        await pipe.execute()

    async def snapshot(self) -> StatsSnapshot:
        ok, failed, errors, day, hits, sent = await self._fetch()
        return StatsSnapshot(
            ok_by_platform=_ints(ok),
            failed_by_platform=_ints(failed),
            errors=_ints(errors),
            today_ok=int(day.get("ok", 0)),
            today_failed=int(day.get("failed", 0)),
            cache_hits=int(hits or 0),
            bytes_sent=int(sent or 0),
        )

    async def _fetch(self) -> tuple[Any, ...]:
        pipe = self.r.pipeline()
        pipe.hgetall("stats:ok")
        pipe.hgetall("stats:failed")
        pipe.hgetall("stats:errors")
        pipe.hgetall(f"stats:day:{self._today()}")
        pipe.get("stats:cache_hits")
        pipe.get("stats:bytes")
        return tuple(await pipe.execute())


def _ints(data: dict[str, str]) -> dict[str, int]:
    return {k: int(v) for k, v in data.items()}


class KeyValue:
    """Small free-form state: extractor versions, last update check and the like."""

    def __init__(self, redis: Redis) -> None:
        self.r = redis

    async def get(self, key: str) -> str | None:
        value = await self.r.get(f"kv:{key}")
        return str(value) if value is not None else None

    async def set(self, key: str, value: str) -> None:
        await self.r.set(f"kv:{key}", value)

    async def set_many(self, items: Iterable[tuple[str, str]]) -> None:
        await self.r.mset({f"kv:{k}": v for k, v in items})
