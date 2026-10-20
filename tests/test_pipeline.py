import asyncio

from conftest import Env
from grabby.domain.errors import ErrorKind
from grabby.domain.jobs import JobStatus
from grabby.domain.platforms import Platform
from grabby.services.pipeline import JobOptions

TT = "https://www.tiktok.com/@a/video/{}"


async def submit(env: Env, *urls: str, user: int = 1, options: JobOptions | None = None) -> int:
    result = await env.pipeline.submit_text(
        user_id=user, chat_id=user, lang="ru", texts=list(urls), options=options
    )
    return result.added


async def wait_done(env: Env) -> None:
    await asyncio.wait_for(env.messenger.done.wait(), 5)


async def test_results_arrive_in_link_order(env: Env) -> None:
    await env.pipeline.start()
    urls = [TT.format(i) for i in range(6)]
    env.fetcher.delays = {urls[0]: 0.2, urls[1]: 0.1}  # the first links finish last
    assert await submit(env, " ".join(urls)) == 6
    await wait_done(env)
    assert [d[0] for d in env.messenger.delivered] == list(range(6))
    batch, failures = env.messenger.finished[0]
    assert batch.done == 6 and not failures


async def test_duplicates_collapse(env: Env) -> None:
    await env.pipeline.start()
    url = TT.format(1)
    result = await env.pipeline.submit_text(
        user_id=1, chat_id=1, lang="ru", texts=[url, url + "?is_from_webapp=1", TT.format(2)]
    )
    assert (result.added, result.duplicates) == (2, 1)
    await wait_done(env)


async def test_failures_and_profiles_are_reported(env: Env) -> None:
    await env.pipeline.start()
    bad = TT.format(2)
    env.fetcher.errors = {bad: ErrorKind.NOT_FOUND}
    await submit(env, TT.format(1), bad, "https://www.instagram.com/natgeo/")
    await wait_done(env)
    _, failures = env.messenger.finished[0]
    assert {(f.url, f.error_kind) for f in failures} == {
        (bad, ErrorKind.NOT_FOUND),
        ("https://www.instagram.com/natgeo/", ErrorKind.NOT_A_POST),
    }
    assert [d[2] for d in env.messenger.delivered] == [True, False, False]


async def test_network_errors_are_retried(env: Env) -> None:
    env.pipeline.settings.retry_base_delay_s = 0.05
    await env.pipeline.start()
    url = TT.format(9)
    env.fetcher.errors = {url: ErrorKind.NETWORK}
    await submit(env, url)
    await asyncio.sleep(0.02)
    env.fetcher.errors.clear()  # the site comes back before the retry
    await wait_done(env)
    assert env.fetcher.calls.count(url) == 2
    assert env.messenger.delivered[0][2]


async def test_second_request_comes_from_cache(env: Env) -> None:
    await env.pipeline.start()
    url = TT.format(7)
    await submit(env, url)
    await wait_done(env)
    env.messenger.done.clear()
    await submit(env, url)
    await wait_done(env)
    assert env.fetcher.calls == [url]
    assert env.messenger.delivered[-1][3]  # cached


async def test_document_mode_is_cached_separately(env: Env) -> None:
    await env.pipeline.start()
    url = TT.format(8)
    await submit(env, url)
    await wait_done(env)
    env.messenger.done.clear()
    await submit(env, url, options=JobOptions(as_document=True))
    await wait_done(env)
    assert env.fetcher.calls == [url, url]


async def test_links_sent_while_running_join_the_batch(env: Env) -> None:
    await env.pipeline.start()
    env.fetcher.block.clear()
    await submit(env, TT.format(1))
    await submit(env, TT.format(2), TT.format(3))
    env.fetcher.block.set()
    await wait_done(env)
    assert len(env.messenger.finished) == 1
    assert env.messenger.finished[0][0].total == 3


async def test_cancel_stops_the_queue(env: Env) -> None:
    await env.pipeline.start()
    env.fetcher.block.clear()
    await submit(env, *(TT.format(i) for i in range(10)))
    await asyncio.sleep(0.05)
    cancelled = await env.pipeline.cancel(1)
    assert cancelled == 10  # 4 running are killed, 6 dropped from the queue
    await wait_done(env)
    batch, failures = env.messenger.finished[0]
    assert batch.cancelled == 10
    assert all(f.status is JobStatus.CANCELLED for f in failures)


async def test_retry_failed_runs_only_failed_links(env: Env) -> None:
    await env.pipeline.start()
    bad = TT.format(2)
    env.fetcher.errors = {bad: ErrorKind.RATE_LIMITED}
    env.pipeline.settings.max_retries = 0
    await submit(env, TT.format(1), bad)
    await wait_done(env)
    batch, _ = env.messenger.finished[0]
    env.fetcher.errors.clear()
    env.messenger.done.clear()
    retried = await env.pipeline.retry_failed(batch.id, user_id=1)
    assert retried.added == 1
    await wait_done(env)
    assert env.fetcher.calls[-1] == bad
    assert await env.pipeline.retry_failed(batch.id, user_id=2) == type(retried)(batch=None)


async def test_unfinished_batch_survives_restart(env: Env) -> None:
    await env.pipeline.start()
    env.fetcher.block.clear()
    urls = [TT.format(i) for i in range(5)]
    await submit(env, *urls)
    await asyncio.sleep(0.05)
    await env.pipeline.stop()

    restarted = env.rebuild()
    await restarted.pipeline.start()
    await asyncio.wait_for(restarted.messenger.done.wait(), 5)
    assert [d[1] for d in restarted.messenger.delivered] == urls
    assert restarted.messenger.finished[0][0].done == 5
    await restarted.pipeline.stop()


async def test_users_do_not_share_batches(env: Env) -> None:
    await env.pipeline.start()
    first = await env.pipeline.submit_text(user_id=1, chat_id=1, lang="ru", texts=[TT.format(1)])
    second = await env.pipeline.submit_text(user_id=2, chat_id=2, lang="en", texts=[TT.format(1)])
    assert first.batch is not None and second.batch is not None
    assert first.batch.id != second.batch.id


async def test_links_to_internal_addresses_are_refused(env: Env) -> None:
    await env.pipeline.start()
    await submit(env, "http://127.0.0.1:8081/bot123/getMe", "https://192.168.1.1/admin.mp4")
    await wait_done(env)
    _, failures = env.messenger.finished[0]
    assert len(failures) == 2
    assert {f.error_kind for f in failures} == {ErrorKind.UNSUPPORTED}
    assert env.fetcher.calls == []


async def test_repeats_across_messages_are_counted_once(env: Env) -> None:
    await env.pipeline.start()
    env.fetcher.block.clear()
    await submit(env, TT.format(1), TT.format(2))
    second = await env.pipeline.submit_text(
        user_id=1, chat_id=1, lang="ru", texts=[TT.format(2), TT.format(3)]
    )
    as_file = await submit(env, TT.format(1), options=JobOptions(as_document=True))
    env.fetcher.block.set()
    await wait_done(env)
    batch, _ = env.messenger.finished[0]
    assert (second.added, second.duplicates, as_file) == (1, 1, 1)
    assert (batch.total, batch.duplicates) == (4, 1)


def add_accounts(env: Env, *names: str) -> None:
    folder = env.settings.cookies_dir
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / f"tiktok-{name}.txt").write_text(name)


async def test_accounts_are_only_used_when_the_site_asks_for_a_login(env: Env) -> None:
    add_accounts(env, "a")
    open_link, closed_link = TT.format(1), TT.format(2)
    env.fetcher.needs_login = {closed_link}
    await env.pipeline.start()
    await submit(env, open_link, closed_link)
    await wait_done(env)
    assert [d[2] for d in env.messenger.delivered] == [True, True]
    assert env.fetcher.accounts_used == [None, None, "a"]


async def test_blocked_account_rests_and_the_next_one_takes_over(env: Env) -> None:
    add_accounts(env, "a", "b")
    url = TT.format(5)
    env.fetcher.needs_login = {url}
    env.fetcher.blocked_accounts = {"a"}
    await env.pipeline.start()
    await submit(env, url)
    await wait_done(env)
    assert env.messenger.delivered[0][2]
    assert env.fetcher.accounts_used == [None, "a", "b"]
    states = {s.name: s for s in await env.pipeline.cookies.states(Platform.TIKTOK)}
    assert states["tiktok-a.txt"].quarantined_for_s > 0
    assert states["tiktok-b.txt"].quarantined_for_s == 0
    assert "tiktok-a.txt" in env.messenger.alerts[0]


async def test_without_accounts_a_login_wall_is_reported(env: Env) -> None:
    url = TT.format(6)
    env.fetcher.needs_login = {url}
    await env.pipeline.start()
    await submit(env, url)
    await wait_done(env)
    assert env.fetcher.accounts_used == [None]
    assert not env.messenger.delivered[0][2]
