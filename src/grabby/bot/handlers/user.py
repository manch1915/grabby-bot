from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from grabby.bot import keyboards
from grabby.bot.intake import Intake, extract_urls
from grabby.bot.keyboards import BatchAction, JobAction, SettingToggle
from grabby.bot.texts import LANGS, size_label, t
from grabby.domain.jobs import SendMode, UserSettings
from grabby.domain.media import Quality
from grabby.infra.store import UserStore
from grabby.services.pipeline import JobOptions, Pipeline

router = Router(name="user")

MAX_TXT_BYTES = 1024 * 1024


@router.message(CommandStart())
@router.message(Command("help"))
async def start(message: Message, prefs: UserSettings, upload_limit: int) -> None:
    await message.answer(t(prefs.lang, "start", limit=size_label(prefs.lang, upload_limit)))


@router.message(Command("settings"))
async def settings(message: Message, prefs: UserSettings) -> None:
    await message.answer(t(prefs.lang, "settings_title"), reply_markup=keyboards.settings(prefs))


@router.callback_query(SettingToggle.filter())
async def toggle_setting(
    query: CallbackQuery, callback_data: SettingToggle, prefs: UserSettings, users: UserStore
) -> None:
    match callback_data.name:
        case "quality":
            prefs.quality = Quality.ECONOMY if prefs.quality is Quality.MAX else Quality.MAX
        case "mode":
            prefs.send_mode = SendMode.DOCUMENT if prefs.send_mode is SendMode.MEDIA else SendMode.MEDIA
        case "caption":
            prefs.caption = not prefs.caption
        case "footer":
            prefs.footer = not prefs.footer
        case "lang":
            prefs.lang = LANGS[(LANGS.index(prefs.lang) + 1) % len(LANGS)]
    await users.save_settings(query.from_user.id, prefs)
    if isinstance(query.message, Message):
        await query.message.edit_text(t(prefs.lang, "settings_title"), reply_markup=keyboards.settings(prefs))
    await query.answer(t(prefs.lang, "saved"))


@router.message(Command("cancel"))
async def cancel(message: Message, prefs: UserSettings, pipeline: Pipeline) -> None:
    assert message.from_user is not None
    count = await pipeline.cancel(message.from_user.id)
    await message.answer(t(prefs.lang, "cancelled", n=count) if count else t(prefs.lang, "nothing_to_cancel"))


@router.callback_query(BatchAction.filter(F.action == "cancel"))
async def cancel_button(query: CallbackQuery, prefs: UserSettings, pipeline: Pipeline) -> None:
    count = await pipeline.cancel(query.from_user.id)
    await query.answer(t(prefs.lang, "cancelled", n=count) if count else t(prefs.lang, "nothing_to_cancel"))


@router.callback_query(BatchAction.filter(F.action == "retry"))
async def retry(
    query: CallbackQuery, callback_data: BatchAction, prefs: UserSettings, pipeline: Pipeline
) -> None:
    submitted = await pipeline.retry_failed(callback_data.batch_id, query.from_user.id)
    if submitted.added and isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)
    await query.answer(
        t(prefs.lang, "retry_started", n=submitted.added)
        if submitted.added
        else t(prefs.lang, "retry_nothing")
    )


@router.callback_query(JobAction.filter())
async def job_action(
    query: CallbackQuery, callback_data: JobAction, prefs: UserSettings, pipeline: Pipeline
) -> None:
    options = JobOptions(quality=prefs.quality)
    match callback_data.action:
        case "file":
            options.as_document = True
        case "eco":
            options.quality = Quality.ECONOMY
        case "split":
            options.split_parts = True
    submitted = await pipeline.resubmit(callback_data.job_id, query.from_user.id, options)
    if callback_data.action != "file" and isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)
    await query.answer(t(prefs.lang, "status_one") if submitted.added else t(prefs.lang, "retry_nothing"))


@router.message(F.document.file_name.lower().endswith(".txt") | (F.document.mime_type == "text/plain"))
async def link_list(message: Message, bot: Bot, prefs: UserSettings, intake: Intake) -> None:
    assert message.document is not None and message.from_user is not None
    if (message.document.file_size or 0) > MAX_TXT_BYTES:
        await message.answer(t(prefs.lang, "txt_too_big"))
        return
    buffer = await bot.download(message.document)
    assert buffer is not None
    text = buffer.read().decode("utf-8", "replace")
    urls = [*extract_urls(message), text]
    _add(intake, message, prefs, urls)


@router.message()
async def links(message: Message, prefs: UserSettings, intake: Intake) -> None:
    _add(intake, message, prefs, extract_urls(message))


def _add(intake: Intake, message: Message, prefs: UserSettings, urls: list[str]) -> None:
    assert message.from_user is not None
    intake.add(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        lang=prefs.lang,
        options=JobOptions(quality=prefs.quality, as_document=prefs.send_mode is SendMode.DOCUMENT),
        message_id=message.message_id,
        urls=urls,
    )
