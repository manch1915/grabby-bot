"""Telegram side of the pipeline: status messages, media delivery, summaries."""

import asyncio
import contextlib
import logging
from html import escape
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    ReplyParameters,
)

from grabby.bot import keyboards
from grabby.bot.telegram_calls import call
from grabby.bot.texts import error_text, size_label, t
from grabby.domain.captions import build_caption
from grabby.domain.errors import ErrorKind
from grabby.domain.jobs import Batch, Job, JobStatus, UserSettings
from grabby.domain.media import ALBUM_SIZE, MediaItem, MediaKind, PostInfo
from grabby.infra.store import CachedFile, CachedPost, UserStore
from grabby.services.pipeline import Outcome

log = logging.getLogger(__name__)

_SUMMARY_LINES = 25

type InputMedia = InputMediaPhoto | InputMediaVideo | InputMediaDocument


class TelegramMessenger:
    def __init__(
        self,
        bot: Bot,
        users: UserStore,
        *,
        local_files: bool,
        footer: str,
        max_bytes: int,
        upload_slots: int = 3,
    ) -> None:
        self.bot = bot
        self.users = users
        self.local_files = local_files
        self.max_bytes = max_bytes
        self.footer = footer
        self._uploads = asyncio.Semaphore(upload_slots)

    # status

    def _status_text(self, batch: Batch, queued: int = 0) -> str:
        if batch.total == 1 and batch.processed == 0:
            return t(batch.lang, "status_one")
        lines = [t(batch.lang, "status_many", processed=batch.processed, total=batch.total)]
        if batch.failed:
            lines.append(t(batch.lang, "status_failed", failed=batch.failed))
        if batch.duplicates:
            lines.append(t(batch.lang, "duplicates", n=batch.duplicates))
        if queued:
            lines.append(t(batch.lang, "status_queue", queued=queued))
        return "\n".join(lines)

    async def status_new(self, batch: Batch) -> int | None:
        reply = (
            ReplyParameters(message_id=batch.reply_to, allow_sending_without_reply=True)
            if batch.reply_to
            else None
        )
        message = await call(
            lambda: self.bot.send_message(
                batch.chat_id,
                self._status_text(batch),
                reply_parameters=reply,
                reply_markup=keyboards.cancel(batch.lang, batch.id),
                disable_notification=True,
            )
        )
        return message.message_id

    async def status_update(self, batch: Batch) -> None:
        if batch.status_message_id is None:
            return
        await self._edit(batch, self._status_text(batch), keyboards.cancel(batch.lang, batch.id))

    async def _edit(self, batch: Batch, text: str, markup: InlineKeyboardMarkup | None) -> None:
        if batch.status_message_id is None:
            return
        message_id = batch.status_message_id
        try:
            await call(
                lambda: self.bot.edit_message_text(
                    text=text,
                    chat_id=batch.chat_id,
                    message_id=message_id,
                    reply_markup=markup,
                )
            )
        except TelegramBadRequest as exc:
            if "not modified" not in str(exc):
                log.info("status edit failed: %s", exc)

    async def _delete_status(self, batch: Batch) -> None:
        if batch.status_message_id is None:
            return
        message_id = batch.status_message_id
        with contextlib.suppress(TelegramBadRequest):
            await call(lambda: self.bot.delete_message(batch.chat_id, message_id))

    # delivery

    async def deliver(self, batch: Batch, outcome: Outcome) -> list[CachedFile]:
        job = outcome.job
        if job.status is JobStatus.CANCELLED:
            return []
        if not outcome.ok:
            if batch.total == 1:
                await self._single_error(batch, job)
            return []
        prefs = await self.users.settings(job.user_id) or UserSettings(lang=batch.lang)
        if outcome.cached is not None:
            info = PostInfo.from_dict(outcome.cached.info)
            await self._send_cached(job, outcome.cached, self._caption(info, prefs), prefs)
            return outcome.cached.files
        assert outcome.bundle is not None
        caption = self._caption(outcome.bundle.info, prefs)
        async with self._uploads:
            return await self._send_items(job, outcome.bundle.items, caption, prefs)

    def _caption(self, info: PostInfo, prefs: UserSettings) -> str | None:
        footer = self.footer if prefs.footer and self.footer else None
        if not prefs.caption:
            return footer
        return build_caption(info, footer=footer)

    def _file(self, path: str) -> str | FSInputFile:
        # In --local mode the Bot API server reads the file straight from the shared volume.
        if self.local_files:
            return Path(path).resolve().as_uri()
        return FSInputFile(path)

    async def _send_items(
        self, job: Job, items: list[MediaItem], caption: str | None, prefs: UserSettings
    ) -> list[CachedFile]:
        sent: list[CachedFile] = []
        visual = [i for i in items if i.kind in (MediaKind.PHOTO, MediaKind.VIDEO)]
        documents = [i for i in items if i.kind is MediaKind.DOCUMENT]
        others = [i for i in items if i.kind in (MediaKind.AUDIO, MediaKind.ANIMATION)]
        chat = job.chat_id

        if len(visual) == 1 and not documents:
            sent.append(await self._send_single(job, visual[0], caption, prefs.lang))
            caption = None
        else:
            for start in range(0, len(visual), ALBUM_SIZE):
                group = visual[start : start + ALBUM_SIZE]
                sent.extend(await self._send_group(chat, group, caption))
                caption = None
        for start in range(0, len(documents), ALBUM_SIZE):
            group = documents[start : start + ALBUM_SIZE]
            if len(group) == 1:
                sent.append(await self._send_single(job, group[0], caption, prefs.lang))
            else:
                sent.extend(await self._send_group(chat, group, caption))
            caption = None
        for item in others:
            sent.append(await self._send_single(job, item, caption, prefs.lang))
            caption = None
        return sent

    async def _send_single(self, job: Job, item: MediaItem, caption: str | None, lang: str) -> CachedFile:
        chat = job.chat_id
        file = self._file(item.path)
        thumb = FSInputFile(item.thumbnail) if item.thumbnail else None
        message: Message
        match item.kind:
            case MediaKind.VIDEO:
                message = await call(
                    lambda: self.bot.send_video(
                        chat, file, caption=caption, width=item.width or None, height=item.height or None,
                        duration=item.duration or None, thumbnail=thumb, supports_streaming=True,
                        reply_markup=keyboards.as_file(lang, job.id),
                    )
                )  # fmt: skip
                assert message.video is not None
                return CachedFile("video", message.video.file_id)
            case MediaKind.PHOTO:
                message = await call(lambda: self.bot.send_photo(chat, file, caption=caption))
                assert message.photo
                return CachedFile("photo", message.photo[-1].file_id)
            case MediaKind.AUDIO:
                message = await call(
                    lambda: self.bot.send_audio(
                        chat, file, caption=caption, duration=item.duration or None,
                        title=item.title, performer=item.performer,
                    )
                )  # fmt: skip
                assert message.audio is not None
                return CachedFile("audio", message.audio.file_id)
            case MediaKind.ANIMATION:
                message = await call(lambda: self.bot.send_animation(chat, file, caption=caption))
                assert message.animation is not None
                return CachedFile("animation", message.animation.file_id)
            case _:
                message = await call(
                    lambda: self.bot.send_document(chat, file, caption=caption, thumbnail=thumb)
                )
                assert message.document is not None
                return CachedFile("document", message.document.file_id)

    async def _send_group(self, chat: int, items: list[MediaItem], caption: str | None) -> list[CachedFile]:
        def build() -> list[InputMedia]:
            media: list[InputMedia] = []
            for i, item in enumerate(items):
                cap = caption if i == 0 else None
                file = self._file(item.path)
                if item.kind is MediaKind.VIDEO:
                    media.append(
                        InputMediaVideo(
                            media=file, caption=cap, width=item.width or None, height=item.height or None,
                            duration=item.duration or None, supports_streaming=True,
                            thumbnail=FSInputFile(item.thumbnail) if item.thumbnail else None,
                        )
                    )  # fmt: skip
                elif item.kind is MediaKind.PHOTO:
                    media.append(InputMediaPhoto(media=file, caption=cap))
                else:
                    media.append(InputMediaDocument(media=file, caption=cap))
            return media

        messages = await call(lambda: self.bot.send_media_group(chat, build()))  # type: ignore[arg-type]
        return [_cached_from(m) for m in messages]

    async def _send_cached(
        self, job: Job, post: CachedPost, caption: str | None, prefs: UserSettings
    ) -> None:
        chat = job.chat_id
        visual = [f for f in post.files if f.kind in ("photo", "video")]
        documents = [f for f in post.files if f.kind == "document"]
        others = [f for f in post.files if f.kind in ("audio", "animation")]
        if len(visual) == 1 and not documents and visual[0].kind == "video":
            file_id = visual[0].file_id
            await call(
                lambda: self.bot.send_video(
                    chat, file_id, caption=caption, supports_streaming=True,
                    reply_markup=keyboards.as_file(prefs.lang, job.id),
                )
            )  # fmt: skip
            caption = None
            visual = []
        for group in _chunks(visual) + _chunks(documents):
            if len(group) == 1:
                await self._send_cached_one(chat, group[0], caption)
            else:
                media: list[InputMedia] = [
                    _input_from_cached(f, caption if i == 0 else None) for i, f in enumerate(group)
                ]
                await call(lambda media=media: self.bot.send_media_group(chat, media))  # type: ignore[misc]
            caption = None
        for item in others:
            await self._send_cached_one(chat, item, caption)
            caption = None

    async def _send_cached_one(self, chat: int, item: CachedFile, caption: str | None) -> None:
        file_id = item.file_id
        match item.kind:
            case "photo":
                await call(lambda: self.bot.send_photo(chat, file_id, caption=caption))
            case "video":
                await call(
                    lambda: self.bot.send_video(chat, file_id, caption=caption, supports_streaming=True)
                )
            case "audio":
                await call(lambda: self.bot.send_audio(chat, file_id, caption=caption))
            case "animation":
                await call(lambda: self.bot.send_animation(chat, file_id, caption=caption))
            case _:
                await call(lambda: self.bot.send_document(chat, file_id, caption=caption))

    async def _single_error(self, batch: Batch, job: Job) -> None:
        limit = size_label(batch.lang, self.max_bytes)
        reason = error_text(batch.lang, job.error_kind, limit=limit)
        text = t(batch.lang, "error_one", reason=reason, url=escape(job.url))
        markup = keyboards.too_large(batch.lang, job.id) if job.error_kind is ErrorKind.TOO_LARGE else None
        if job.error_kind is ErrorKind.TOO_LARGE:
            text = t(batch.lang, "too_large", limit=limit) + "\n" + escape(job.url)
        await self._edit(batch, text, markup)
        batch.status_message_id = None  # keep the error, finish() must not delete it

    # summary

    async def finish(self, batch: Batch, failures: list[Job]) -> None:
        if batch.total == 1 and not failures:
            await self._delete_status(batch)
            return
        if batch.total == 1:
            return  # the error already replaced the status message
        cancelled = any(j.status is JobStatus.CANCELLED for j in failures)
        ok = batch.total - len(failures)
        if cancelled:
            head = t(batch.lang, "summary_cancelled", ok=ok, total=batch.total)
        elif failures:
            head = t(batch.lang, "summary_partial", ok=ok, total=batch.total)
        else:
            head = t(batch.lang, "summary_ok", total=batch.total)
        lines = [head]
        if batch.duplicates:
            lines.append(t(batch.lang, "duplicates", n=batch.duplicates) + ".")
        failed = [j for j in failures if j.status is JobStatus.FAILED]
        if failed:
            lines += ["", t(batch.lang, "summary_failed_head")]
            for job in failed[:_SUMMARY_LINES]:
                reason = error_text(batch.lang, job.error_kind, limit=size_label(batch.lang, self.max_bytes))
                lines.append(f"{job.index + 1}. {escape(_short(job.url))}  {reason}")
            if len(failed) > _SUMMARY_LINES:
                lines.append(t(batch.lang, "summary_more", n=len(failed) - _SUMMARY_LINES))
        retryable = [j for j in failures if j.error_kind is not ErrorKind.NOT_A_POST]
        markup = keyboards.retry(batch.lang, batch.id) if retryable else None
        # The status message is far above after a long batch; the summary goes to the bottom.
        await self._delete_status(batch)
        text = "\n".join(lines)
        await call(lambda: self.bot.send_message(batch.chat_id, text, reply_markup=markup))


def _short(url: str) -> str:
    bare = url.split("://", 1)[-1].removeprefix("www.")
    return bare if len(bare) <= 60 else bare[:59] + "…"


def _chunks(files: list[CachedFile]) -> list[list[CachedFile]]:
    return [files[i : i + ALBUM_SIZE] for i in range(0, len(files), ALBUM_SIZE)]


def _input_from_cached(item: CachedFile, caption: str | None) -> InputMedia:
    if item.kind == "video":
        return InputMediaVideo(media=item.file_id, caption=caption, supports_streaming=True)
    if item.kind == "photo":
        return InputMediaPhoto(media=item.file_id, caption=caption)
    return InputMediaDocument(media=item.file_id, caption=caption)


def _cached_from(message: Message) -> CachedFile:
    if message.video:
        return CachedFile("video", message.video.file_id)
    if message.photo:
        return CachedFile("photo", message.photo[-1].file_id)
    if message.document:
        return CachedFile("document", message.document.file_id)
    if message.audio:
        return CachedFile("audio", message.audio.file_id)
    raise ValueError("unexpected media in album")
