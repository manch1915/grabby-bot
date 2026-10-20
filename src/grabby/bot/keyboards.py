from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from grabby.bot.texts import t
from grabby.domain.jobs import SendMode, UserSettings
from grabby.domain.media import Quality


class JobAction(CallbackData, prefix="j"):
    action: str  # file | eco | split
    job_id: str


class BatchAction(CallbackData, prefix="b"):
    action: str  # retry | cancel
    batch_id: str


class SettingToggle(CallbackData, prefix="s"):
    name: str


def as_file(lang: str, job_id: str) -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(
        text=t(lang, "btn_as_file"), callback_data=JobAction(action="file", job_id=job_id).pack()
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def too_large(lang: str, job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(lang, "btn_economy"), callback_data=JobAction(action="eco", job_id=job_id).pack()
                ),
                InlineKeyboardButton(
                    text=t(lang, "btn_split"), callback_data=JobAction(action="split", job_id=job_id).pack()
                ),
            ]
        ]
    )


def cancel(lang: str, batch_id: str) -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(
        text=t(lang, "btn_cancel"), callback_data=BatchAction(action="cancel", batch_id=batch_id).pack()
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def retry(lang: str, batch_id: str) -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(
        text=t(lang, "btn_retry"), callback_data=BatchAction(action="retry", batch_id=batch_id).pack()
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def settings(prefs: UserSettings) -> InlineKeyboardMarkup:
    lang = prefs.lang
    rows = [
        ("quality", "set_quality_max" if prefs.quality is Quality.MAX else "set_quality_economy"),
        ("mode", "set_mode_media" if prefs.send_mode is SendMode.MEDIA else "set_mode_document"),
        ("caption", "set_caption_on" if prefs.caption else "set_caption_off"),
        ("footer", "set_footer_on" if prefs.footer else "set_footer_off"),
        ("lang", "set_lang"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, key), callback_data=SettingToggle(name=name).pack())]
            for name, key in rows
        ]
    )
