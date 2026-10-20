from pathlib import Path

from fakeredis.aioredis import FakeRedis

from grabby.domain.platforms import Platform
from grabby.services.cookie_pool import CookiePool


def pool(tmp_path: Path, **kwargs: object) -> CookiePool:
    for name in ("instagram.txt", "instagram-2.txt"):
        (tmp_path / name).write_text("#")
    files = lambda p: sorted(tmp_path.glob(f"{p.value}*.txt"))  # noqa: E731
    return CookiePool(FakeRedis(decode_responses=True), files, min_gap_s=0, **kwargs)  # type: ignore[arg-type]


async def test_least_recently_used_account_goes_first(tmp_path: Path) -> None:
    accounts = pool(tmp_path)
    first = await accounts.acquire(Platform.INSTAGRAM)
    assert first is not None
    await accounts.release(first)
    second = await accounts.acquire(Platform.INSTAGRAM)
    assert second is not None and second.source != first.source


async def test_an_account_in_use_is_not_handed_out_twice(tmp_path: Path) -> None:
    accounts = pool(tmp_path)
    a, b = await accounts.acquire(Platform.INSTAGRAM), await accounts.acquire(Platform.INSTAGRAM)
    assert a and b and a.source != b.source
    assert await accounts.acquire(Platform.INSTAGRAM) is None


async def test_quarantine_and_daily_cap(tmp_path: Path) -> None:
    accounts = pool(tmp_path, daily_cap=1)
    a = await accounts.acquire(Platform.INSTAGRAM)
    assert a is not None
    assert await accounts.release(a, blocked="checkpoint_required") is True
    b = await accounts.acquire(Platform.INSTAGRAM)
    assert b is not None and b.source != a.source
    assert await accounts.release(b) is False
    assert await accounts.acquire(Platform.INSTAGRAM) is None  # one blocked, one used up for today
    assert await accounts.acquire(Platform.TIKTOK) is None
    states = {s.name: s for s in await accounts.states(Platform.INSTAGRAM)}
    assert states[a.source.name].quarantined_for_s > 0 and states[b.source.name].used_today == 1
