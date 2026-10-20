from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from grabby.bot.texts import pick_lang, t
from grabby.domain.jobs import UserSettings
from grabby.infra.store import UserStore

type Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class AccessMiddleware(BaseMiddleware):
    """Bans, invite-only mode, and per-user settings for every handler."""

    def __init__(self, users: UserStore, admin_ids: list[int], default_mode: str) -> None:
        self.users = users
        self.admin_ids = set(admin_ids)
        self.default_mode = default_mode

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.is_bot:
            return None
        is_admin = user.id in self.admin_ids
        if not is_admin and await self.users.is_banned(user.id):
            return None

        prefs = await self.users.settings(user.id)
        if prefs is None:
            prefs = UserSettings(lang=pick_lang(user.language_code))
            await self.users.save_settings(user.id, prefs)
            await self.users.touch(user.id, user.full_name)

        invite_only = await self.users.access_mode(self.default_mode) == "list"
        if not is_admin and invite_only and not await self.users.is_allowed(user.id):
            text = t(prefs.lang, "closed")
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            return None

        data["prefs"] = prefs
        data["is_admin"] = is_admin
        return await handler(event, data)
