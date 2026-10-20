"""Site accounts (cookie files) handed out carefully.

Instagram flags a session that fetches in bursts: two fresh accounts went to checkpoint
after about a hundred requests in a few minutes. So accounts are a fallback, each one
waits between uses, has a daily budget, and sits out a quarantine when the site blocks it.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from redis.asyncio import Redis

from grabby.domain.platforms import Platform


@dataclass(frozen=True, slots=True)
class Lease:
    platform: Platform
    source: Path

    @property
    def key(self) -> str:
        return f"{self.platform.value}:{self.source.name}"


@dataclass(frozen=True, slots=True)
class AccountState:
    name: str
    used_today: int
    quarantined_for_s: int


class CookiePool:
    def __init__(
        self,
        redis: Redis,
        files: Callable[[Platform], list[Path]],
        *,
        min_gap_s: float = 8.0,
        daily_cap: int = 300,
        quarantine_s: int = 6 * 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.r = redis
        self._files = files
        self.min_gap = min_gap_s
        self.daily_cap = daily_cap
        self.quarantine = quarantine_s
        self._clock = clock
        self._leased: set[str] = set()
        self._lock = asyncio.Lock()

    @staticmethod
    def _day() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    async def _usable(self, platform: Platform) -> list[Lease]:
        day = self._day()
        usable = []
        for source in self._files(platform):
            lease = Lease(platform, source)
            if lease.key in self._leased or await self.r.exists(f"cookies:quarantine:{lease.key}"):
                continue
            used = int(await self.r.hget(f"cookies:daily:{day}", lease.key) or 0)
            if used < self.daily_cap:
                usable.append(lease)
        return usable

    async def acquire(self, platform: Platform) -> Lease | None:
        """The least recently used healthy account, after its cool-down gap. None if there is none."""
        async with self._lock:
            usable = await self._usable(platform)
            if not usable:
                return None
            last = {
                lease.key: float(await self.r.hget("cookies:last_used", lease.key) or 0) for lease in usable
            }
            lease = min(usable, key=lambda item: last[item.key])
            self._leased.add(lease.key)
        wait = last[lease.key] + self.min_gap - self._clock()
        if wait > 0:
            await asyncio.sleep(wait)
        return lease

    async def release(self, lease: Lease, *, blocked: str | None = None) -> bool:
        """Record a use. Returns True when this call put the account into quarantine."""
        self._leased.discard(lease.key)
        day = self._day()
        pipe = self.r.pipeline()
        pipe.hset("cookies:last_used", lease.key, str(self._clock()))
        pipe.hincrby(f"cookies:daily:{day}", lease.key, 1)
        pipe.expire(f"cookies:daily:{day}", 2 * 24 * 3600)
        await pipe.execute()
        if blocked is None:
            return False
        created = await self.r.set(f"cookies:quarantine:{lease.key}", blocked, ex=self.quarantine, nx=True)
        return bool(created)

    async def states(self, platform: Platform) -> list[AccountState]:
        day = self._day()
        result = []
        for source in self._files(platform):
            key = f"{platform.value}:{source.name}"
            ttl = int(await self.r.ttl(f"cookies:quarantine:{key}"))
            used = int(await self.r.hget(f"cookies:daily:{day}", key) or 0)
            result.append(AccountState(source.name, used, max(ttl, 0)))
        return result
