import asyncio

from grabby.domain.jobs import Job
from grabby.domain.platforms import Platform
from grabby.services.scheduler import FairScheduler


def job(user: int, index: int, platform: Platform = Platform.TIKTOK) -> Job:
    return Job(
        batch_id=f"b{user}",
        user_id=user,
        chat_id=user,
        index=index,
        url=f"u{user}-{index}",
        platform=platform,
    )


def scheduler(**kwargs: object) -> FairScheduler:
    params: dict[str, object] = {"max_parallel": 10, "per_user": 10, "platform_limit": lambda p: 10}
    params.update(kwargs)
    return FairScheduler(**params)  # type: ignore[arg-type]


async def test_users_take_turns() -> None:
    s = scheduler(max_parallel=100)
    await s.push([job(1, i) for i in range(5)])
    await s.push([job(2, i) for i in range(2)])
    order = [(j.user_id, j.index) for j in [await s.next() for _ in range(7)]]
    # The second user doesn't wait for all five of the first user's links.
    assert order[:4] == [(1, 0), (2, 0), (1, 1), (2, 1)]
    assert order[4:] == [(1, 2), (1, 3), (1, 4)]


async def test_per_user_cap() -> None:
    s = scheduler(per_user=2)
    await s.push([job(1, i) for i in range(3)])
    first, second = await s.next(), await s.next()
    assert (first.index, second.index) == (0, 1)
    waiter = asyncio.create_task(s.next())
    await asyncio.sleep(0.01)
    assert not waiter.done()
    await s.release(first)
    third = await asyncio.wait_for(waiter, 1)
    assert third.index == 2


async def test_platform_cap_lets_other_sites_through() -> None:
    s = scheduler(platform_limit=lambda p: 1 if p is Platform.TIKTOK else 5)
    await s.push([job(1, 0), job(1, 1), job(1, 2, Platform.INSTAGRAM)])
    a, b = await s.next(), await s.next()
    assert (a.platform, b.platform) == (Platform.TIKTOK, Platform.INSTAGRAM)
    assert s.queued() == 1


async def test_global_cap_and_disk_gate() -> None:
    room = {"free": False}
    s = scheduler(max_parallel=1, can_start=lambda: room["free"], recheck_interval=0.01)
    await s.push([job(1, 0), job(2, 0)])
    waiter = asyncio.create_task(s.next())
    await asyncio.sleep(0.05)
    assert not waiter.done()
    room["free"] = True
    first = await asyncio.wait_for(waiter, 1)
    blocked = asyncio.create_task(s.next())
    await asyncio.sleep(0.05)
    assert not blocked.done()
    await s.release(first)
    assert (await asyncio.wait_for(blocked, 1)).user_id == 2


async def test_cancel_user_drops_only_their_jobs() -> None:
    s = scheduler()
    await s.push([job(1, i) for i in range(3)] + [job(2, 0)])
    dropped = await s.cancel_user(1)
    assert len(dropped) == 3
    assert (await s.next()).user_id == 2


async def test_push_front_for_restored_jobs() -> None:
    s = scheduler()
    await s.push([job(1, 5)])
    await s.push([job(1, 1), job(1, 2)], front=True)
    assert [(await s.next()).index for _ in range(3)] == [1, 2, 5]


async def test_site_interval_spaces_out_job_starts() -> None:
    now = {"t": 100.0}
    s = scheduler(
        platform_interval=lambda p: 2.0 if p is Platform.INSTAGRAM else 0.0,
        clock=lambda: now["t"],
        recheck_interval=0.01,
    )
    await s.push([job(1, 0, Platform.INSTAGRAM), job(1, 1, Platform.INSTAGRAM), job(1, 2, Platform.X)])
    first = await s.next()
    second = await s.next()
    assert (first.platform, second.platform) == (Platform.INSTAGRAM, Platform.X)
    waiter = asyncio.create_task(s.next())
    await asyncio.sleep(0.05)
    assert not waiter.done()
    now["t"] += 2.1
    assert (await asyncio.wait_for(waiter, 1)).index == 1
