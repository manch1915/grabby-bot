import asyncio
import contextlib
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, ReplyParameters
from redis.asyncio import Redis

from grabby.bot.handlers import admin, user
from grabby.bot.intake import Intake
from grabby.bot.messenger import TelegramMessenger
from grabby.bot.middlewares import AccessMiddleware
from grabby.bot.texts import t
from grabby.config import Settings
from grabby.infra.store import CacheStore, JobStore, KeyValue, StatsStore, UserStore
from grabby.services.fetcher import Fetcher
from grabby.services.pipeline import JobOptions, Pipeline
from grabby.services.resolver import LinkResolver
from grabby.services.scheduler import FairScheduler
from grabby.services.storage import WorkDir
from grabby.services.updater import ExtractorManager

log = logging.getLogger("grabby")

_COMMANDS = {
    "ru": [
        BotCommand(command="start", description="Как пользоваться"),
        BotCommand(command="settings", description="Качество, формат, язык"),
        BotCommand(command="cancel", description="Остановить очередь"),
    ],
    "en": [
        BotCommand(command="start", description="How to use"),
        BotCommand(command="settings", description="Quality, format, language"),
        BotCommand(command="cancel", description="Stop the queue"),
    ],
}


async def _switch_to_local_server(settings: Settings, kv: KeyValue) -> None:
    """A bot has to log out of api.telegram.org once before a local server can serve it."""
    if await kv.get("logged_out_of_cloud") == "1":
        return
    async with Bot(settings.bot_token.get_secret_value()) as cloud:
        with contextlib.suppress(Exception):
            await cloud.log_out()
    await kv.set("logged_out_of_cloud", "1")


async def _every(interval_s: float, job: object, *, first_delay: float) -> None:
    await asyncio.sleep(first_delay)
    while True:
        try:
            await job()  # type: ignore[operator]
        except Exception:
            log.exception("periodic task failed")
        await asyncio.sleep(interval_s)


async def run(settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    kv = KeyValue(redis)
    session = None
    local_files = False
    if settings.bot_api_url:
        await _switch_to_local_server(settings, kv)
        server = TelegramAPIServer.from_base(settings.bot_api_url, is_local=settings.bot_api_local)
        session = AiohttpSession(api=server, timeout=1800)
        local_files = settings.bot_api_local
    bot = Bot(
        settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    me = await bot.get_me()
    log.info("running as @%s, local Bot API: %s", me.username, bool(settings.bot_api_url))

    workdir = WorkDir(
        settings.work_dir, limit_mb=settings.work_dir_limit_mb, min_free_mb=settings.min_free_disk_mb
    )
    workdir.reset()
    extractors = ExtractorManager(
        settings.extractors_dir, channel=settings.update_channel, work_dir=settings.work_dir
    )
    fetcher = Fetcher(timeout=settings.download_timeout_s, extractor_path=extractors.current_path)
    scheduler = FairScheduler(
        max_parallel=settings.max_parallel,
        per_user=settings.max_parallel_per_user,
        platform_limit=settings.platform_limit,
        can_start=workdir.has_room,
    )
    users, jobs, stats = UserStore(redis), JobStore(redis), StatsStore(redis)
    footer = settings.footer_text or f'<a href="https://t.me/{me.username}">@{me.username}</a>'
    messenger = TelegramMessenger(bot, users, local_files=local_files, footer=footer)

    http = aiohttp.ClientSession()
    pipeline = Pipeline(
        settings=settings,
        scheduler=scheduler,
        fetcher=fetcher,
        jobs=jobs,
        cache=CacheStore(redis, settings.cache_ttl_days),
        stats=stats,
        workdir=workdir,
        resolver=LinkResolver(http),
        messenger=messenger,
    )

    async def submit(
        user_id: int, chat_id: int, lang: str, texts: list[str], reply_to: int, options: JobOptions
    ) -> int:
        result = await pipeline.submit_text(
            user_id=user_id, chat_id=chat_id, lang=lang, texts=texts, reply_to=reply_to, options=options
        )
        return result.added

    async def no_links(chat_id: int, lang: str, reply_to: int) -> None:
        if chat_id < 0:
            return  # in groups, stay quiet about ordinary chatter
        await bot.send_message(
            chat_id, t(lang, "no_links"), reply_parameters=ReplyParameters(message_id=reply_to)
        )

    access = AccessMiddleware(users, settings.admin_ids, settings.access_mode)
    dp = Dispatcher()
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_routers(admin.router, user.router)
    dp["pipeline"] = pipeline
    dp["users"] = users
    dp["intake"] = Intake(submit, no_links)
    dp["admin"] = admin.AdminContext(
        settings=settings, stats=stats, users=users, pipeline=pipeline, workdir=workdir, kv=kv,
        extractors=extractors, fetcher=fetcher,
    )  # fmt: skip

    for lang, commands in _COMMANDS.items():
        await bot.set_my_commands(commands, language_code=lang)
    await bot.set_my_commands(_COMMANDS["en"])

    async def sweep() -> None:
        workdir.sweep(older_than_s=3600, keep=pipeline.active_job_ids())

    async def update_extractors() -> None:
        report = await extractors.check(fetcher, settings.update_probe_urls)
        await admin.save_versions(kv, report)
        if report.changed or report.note:
            for admin_id in settings.admin_ids:
                with contextlib.suppress(Exception):
                    await bot.send_message(admin_id, admin.report_text("ru", report))

    background = [asyncio.create_task(_every(600, sweep, first_delay=600))]
    if settings.auto_update:
        background.append(
            asyncio.create_task(_every(settings.update_interval_h * 3600, update_extractors, first_delay=300))
        )

    await pipeline.start()
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in background:
            task.cancel()
        await pipeline.stop()
        await http.close()
        await bot.session.close()
        await redis.aclose()
