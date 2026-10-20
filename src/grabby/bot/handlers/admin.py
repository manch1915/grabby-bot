import asyncio
import logging
from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from grabby.bot.texts import t
from grabby.config import Settings
from grabby.domain.jobs import UserSettings
from grabby.infra.store import KeyValue, StatsStore, UserStore
from grabby.services.fetcher import Fetcher
from grabby.services.pipeline import Pipeline
from grabby.services.storage import MB, WorkDir
from grabby.services.updater import ExtractorManager, UpdateReport

router = Router(name="admin")
_tasks: set[asyncio.Task[None]] = set()
log = logging.getLogger(__name__)


@dataclass(slots=True)
class AdminContext:
    settings: Settings
    stats: StatsStore
    users: UserStore
    pipeline: Pipeline
    workdir: WorkDir
    kv: KeyValue
    extractors: ExtractorManager
    fetcher: Fetcher


def _gb(value: int) -> str:
    return f"{value / 1024 / MB:.1f} GB"


@router.message(Command("admin", "stats", "access", "allow", "deny", "ban", "unban", "update", "rollback"))
async def admin_gate(
    message: Message, command: CommandObject, is_admin: bool, prefs: UserSettings, admin: AdminContext
) -> None:
    if not is_admin:
        await message.answer(t(prefs.lang, "admin_only"))
        return
    handler = _COMMANDS.get(command.command, _help)
    await handler(message, command, prefs, admin)


async def _help(message: Message, command: CommandObject, prefs: UserSettings, ctx: AdminContext) -> None:
    await message.answer(t(prefs.lang, "admin_help"))


async def _stats(message: Message, command: CommandObject, prefs: UserSettings, ctx: AdminContext) -> None:
    stats, users, pipeline, workdir, kv = ctx.stats, ctx.users, ctx.pipeline, ctx.workdir, ctx.kv
    snap = await stats.snapshot()
    platforms = sorted(set(snap.ok_by_platform) | set(snap.failed_by_platform))
    rows = [
        f"  {p}: {snap.ok_by_platform.get(p, 0)} ok / {snap.failed_by_platform.get(p, 0)} fail"
        for p in platforms
    ]
    errors = ", ".join(f"{k} {v}" for k, v in sorted(snap.errors.items(), key=lambda kv: -kv[1])[:8])
    versions = await kv.get("extractor_versions") or "image"
    text = "\n".join(
        [
            "<b>Grabby</b>",
            f"Users: {await users.users_count()}",
            f"Today: {snap.today_ok} ok, {snap.today_failed} failed",
            f"Cache hits: {snap.cache_hits}, sent {_gb(snap.bytes_sent)}",
            f"Queue: {pipeline.queue_size()} waiting, {len(pipeline.scheduler.running)} running",
            f"Work dir: {_gb(workdir.usage())} of {_gb(workdir.limit)}, disk free {_gb(workdir.free())}",
            f"Access: {await users.access_mode(ctx.settings.access_mode)}",
            f"Extractors: {versions}",
            "By site:",
            *(rows or ["  none yet"]),
            f"Errors: {errors or 'none'}",
        ]
    )
    await message.answer(text)


async def _access(message: Message, command: CommandObject, prefs: UserSettings, ctx: AdminContext) -> None:
    users = ctx.users
    mode = (command.args or "").strip()
    if mode not in ("open", "list"):
        current = await users.access_mode(ctx.settings.access_mode)
        await message.answer(f"Access: {current}. Use /access open or /access list")
        return
    await users.set_access_mode(mode)
    await message.answer(t(prefs.lang, "admin_done"))


async def _user_flag(
    message: Message, command: CommandObject, prefs: UserSettings, ctx: AdminContext
) -> None:
    users = ctx.users
    try:
        user_id = int((command.args or "").strip())
    except ValueError:
        await message.answer(t(prefs.lang, "admin_bad_id"))
        return
    match command.command:
        case "allow":
            await users.allow(user_id)
        case "deny":
            await users.allow(user_id, False)
        case "ban":
            await users.ban(user_id)
        case "unban":
            await users.ban(user_id, False)
    await message.answer(t(prefs.lang, "admin_done"))


async def _update(message: Message, command: CommandObject, prefs: UserSettings, ctx: AdminContext) -> None:
    extractors, fetcher, kv = ctx.extractors, ctx.fetcher, ctx.kv
    await message.answer(t(prefs.lang, "update_started"))

    async def run() -> None:
        report = await extractors.check(fetcher, ctx.settings.update_probe_urls)
        await save_versions(kv, report)
        await message.answer(report_text(prefs.lang, report))

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _rollback(message: Message, command: CommandObject, prefs: UserSettings, ctx: AdminContext) -> None:
    done = ctx.extractors.rollback()
    await message.answer(t(prefs.lang, "rollback_done" if done else "rollback_none"))


async def save_versions(kv: KeyValue, report: UpdateReport) -> None:
    if report.versions:
        await kv.set("extractor_versions", ", ".join(f"{k} {v}" for k, v in sorted(report.versions.items())))


def report_text(lang: str, report: UpdateReport) -> str:
    versions = ", ".join(f"{k} {v}" for k, v in sorted(report.versions.items())) or "?"
    key = "update_changed" if report.changed else "update_same"
    return t(lang, key, versions=versions, ok=report.probes_ok, total=report.probes_total, note=report.note)


_COMMANDS = {
    "stats": _stats,
    "access": _access,
    "allow": _user_flag,
    "deny": _user_flag,
    "ban": _user_flag,
    "unban": _user_flag,
    "update": _update,
    "rollback": _rollback,
}
