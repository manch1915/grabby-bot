import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import (
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    MessageEntity,
)
from aiohttp import web

from grabby.bot import telegram_calls
from grabby.bot.intake import Intake, extract_urls
from grabby.fetch.threads import find_post, media_urls
from grabby.services.pipeline import JobOptions
from grabby.services.resolver import LinkResolver
from grabby.services.storage import WorkDir


def message(text: str | None = None, **kwargs: object) -> Message:
    return Message(
        message_id=int(kwargs.pop("message_id", 1)),  # type: ignore[call-overload]
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        text=text,
        **kwargs,  # type: ignore[arg-type]
    )


def test_extract_urls_from_text_links_buttons_and_previews() -> None:
    text = "смотри тут и вот vm.tiktok.com/ZMx/"
    msg = message(
        text,
        entities=[
            MessageEntity(type="text_link", offset=6, length=3, url="https://www.instagram.com/p/abc/")
        ],
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Open", url="https://youtu.be/jNQXAC9IVRw")]]
        ),
        link_preview_options=LinkPreviewOptions(url="https://x.com/u/status/1"),
    )
    assert extract_urls(msg) == [
        "https://vm.tiktok.com/ZMx/",
        "https://www.instagram.com/p/abc/",
        "https://youtu.be/jNQXAC9IVRw",
        "https://x.com/u/status/1",
    ]


def test_extract_urls_from_caption() -> None:
    msg = message(caption="clip https://www.tiktok.com/@a/video/1")
    assert extract_urls(msg) == ["https://www.tiktok.com/@a/video/1"]


async def test_intake_merges_a_burst_in_message_order() -> None:
    calls: list[tuple[list[str], int]] = []
    no_links: list[int] = []

    async def submit(
        user: int, chat: int, lang: str, urls: list[str], reply_to: int, options: JobOptions
    ) -> int:
        calls.append((urls, reply_to))
        return len(urls)

    async def none(chat: int, lang: str, reply_to: int) -> None:
        no_links.append(reply_to)

    intake = Intake(submit, none, debounce=0.05)
    for message_id in (12, 10, 11):  # forwarded posts arrive out of order
        intake.add(
            user_id=1,
            chat_id=1,
            lang="ru",
            options=JobOptions(),
            message_id=message_id,
            urls=[f"u{message_id}"],
        )
    await asyncio.sleep(0.15)
    assert calls == [(["u10", "u11", "u12"], 10)]

    intake.add(user_id=1, chat_id=1, lang="ru", options=JobOptions(), message_id=13, urls=[])
    await asyncio.sleep(0.15)
    assert no_links == [13]


async def test_flood_control_waits_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(telegram_calls.asyncio, "sleep", fake_sleep)
    method = SendMessage(chat_id=1, text="hi")
    attempts = iter(
        [
            TelegramRetryAfter(method=method, message="Too Many Requests", retry_after=7),
            TelegramNetworkError(method=method, message="reset"),
            "ok",
        ]
    )

    async def make() -> str:
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    assert await telegram_calls.call(make) == "ok"
    assert sleeps == [8, 2]


THREADS_PAGE = """<html><script type="application/json" data-sjs>{blob}</script></html>"""


def test_threads_parser_finds_the_post_and_its_media() -> None:
    post = {
        "code": "DesH8y",
        "user": {"username": "octavia"},
        "caption": {"text": "hello"},
        "image_versions2": {"candidates": []},
        "carousel_media": [
            {"image_versions2": {"candidates": [{"url": "small", "width": 100, "height": 100},
                                                 {"url": "big", "width": 1440, "height": 1800}]}},
            {"video_versions": [{"url": "clip"}], "image_versions2": {"candidates": [{"url": "cover"}]}},
        ],
    }  # fmt: skip
    other = {"code": "OTHER", "image_versions2": {"candidates": [{"url": "nope"}]}}
    html = THREADS_PAGE.format(
        blob=json.dumps({"require": [[{"thread_items": [{"post": other}, {"post": post}]}]]})
    )
    found = find_post(html, "DesH8y")
    assert found is not None
    assert media_urls(found) == [("big", "jpg"), ("clip", "mp4")]
    assert find_post(html, "missing") is None


async def test_resolver_reads_location_and_meta_refresh() -> None:
    async def hop1(request: web.Request) -> web.Response:
        raise web.HTTPFound("https://www.instagram.com/reel/abc/")

    async def hop2(request: web.Request) -> web.Response:
        return web.Response(
            text='<meta http-equiv="refresh" content="0;URL=https://www.tiktok.com/@a/video/5">',
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/hop1", hop1)
    app.router.add_get("/hop2", hop2)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        async with aiohttp.ClientSession() as session:
            resolver = LinkResolver(session)
            redirected = await resolver._follow(f"http://127.0.0.1:{port}/hop1", headers={})
            refreshed = await resolver._follow(f"http://127.0.0.1:{port}/hop2", headers={})
    finally:
        await runner.cleanup()
    assert redirected == "https://www.instagram.com/reel/abc/"
    assert refreshed == "https://www.tiktok.com/@a/video/5"


def test_workdir_budget(tmp_path: Path) -> None:
    work = WorkDir(tmp_path, limit_mb=1, min_free_mb=0)
    work.reset()
    job = work.job_dir("abc")
    (job / "a.bin").write_bytes(b"x" * 600_000)
    assert work.has_room()
    (job / "b.bin").write_bytes(b"x" * 600_000)
    work._cached_at = 0
    assert not work.has_room()
    work.release("abc")
    assert work.has_room()
