"""Collects links from bursts of messages before handing them to the pipeline.

Forwarding 100 posts produces 100 updates that aiogram handles concurrently, so they
can reach us out of order. We buffer each user's messages for a moment, sort them by
message id (it grows monotonically within a chat) and submit the burst in one go.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from aiogram.types import Message

from grabby.domain.links import find_urls
from grabby.services.pipeline import JobOptions

log = logging.getLogger(__name__)


def extract_urls(message: Message) -> list[str]:
    urls: list[str] = []
    for text, entities in ((message.text, message.entities), (message.caption, message.caption_entities)):
        if not text:
            continue
        found = find_urls(text)
        urls += found
        for entity in entities or []:
            if entity.type == "text_link" and entity.url:
                urls.append(entity.url)
            elif entity.type == "url":
                value = entity.extract_from(text)
                if not any(value in url for url in found):
                    urls.append(value)
    markup = message.reply_markup
    if markup is not None:
        urls += [b.url for row in markup.inline_keyboard for b in row if b.url]
    preview = message.link_preview_options
    if preview is not None and preview.url and preview.url not in urls:
        urls.append(preview.url)
    return urls


@dataclass(slots=True)
class _Pending:
    chat_id: int
    lang: str
    options: JobOptions
    items: list[tuple[int, list[str]]] = field(default_factory=list)  # message_id, urls
    timer: asyncio.TimerHandle | None = None


type SubmitFn = Callable[[int, int, str, list[str], int, JobOptions], Awaitable[int]]
type NoLinksFn = Callable[[int, str, int], Awaitable[None]]


class Intake:
    def __init__(self, submit: SubmitFn, no_links: NoLinksFn, *, debounce: float = 0.8) -> None:
        self._submit = submit
        self._no_links = no_links
        self._debounce = debounce
        self._pending: dict[int, _Pending] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def add(
        self, *, user_id: int, chat_id: int, lang: str, options: JobOptions, message_id: int, urls: list[str]
    ) -> None:
        pending = self._pending.get(user_id)
        if pending is None or pending.chat_id != chat_id:
            pending = self._pending[user_id] = _Pending(chat_id=chat_id, lang=lang, options=options)
        pending.items.append((message_id, urls))
        if pending.timer is not None:
            pending.timer.cancel()
        loop = asyncio.get_running_loop()
        pending.timer = loop.call_later(self._debounce, self._schedule_flush, user_id)

    def _schedule_flush(self, user_id: int) -> None:
        task = asyncio.create_task(self._flush(user_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _flush(self, user_id: int) -> None:
        pending = self._pending.pop(user_id, None)
        if pending is None:
            return
        pending.items.sort()
        urls = [url for _, item_urls in pending.items for url in item_urls]
        first_message = pending.items[0][0]
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            try:
                added = (
                    await self._submit(
                        user_id, pending.chat_id, pending.lang, urls, first_message, pending.options
                    )
                    if urls
                    else 0
                )
                if not added:
                    await self._no_links(pending.chat_id, pending.lang, first_message)
            except Exception:
                log.exception("intake for user %s failed", user_id)
