from grabby.domain.errors import ErrorKind

LANGS = ("ru", "en")

_TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "start": (
            "Привет! Я Grabby. Пришлите ссылку на видео или пост, и я верну его в лучшем качестве, "
            "без водяных знаков.\n\n"
            "<b>Откуда</b>: TikTok (видео и слайдшоу), Instagram (Reels, посты, карусели), YouTube и Shorts, "
            "X, Facebook, Pinterest, Reddit, VK, Threads, Twitch, Vimeo и сотни других сайтов.\n\n"
            "<b>Сколько угодно</b>: несколько ссылок в одном сообщении, пачка пересланных постов или "
            ".txt-файл со списком. Пришлю всё по порядку, файлы до {limit}.\n\n"
            "/settings  качество, формат, подписи, язык\n"
            "/cancel  остановить очередь"
        ),
        "description": (
            "Скачиваю видео и фото из TikTok без водяного знака, Instagram, YouTube, X, Pinterest, "
            "Reddit, VK, Threads и других сайтов. Можно присылать сразу много ссылок, файлы до {limit}."
        ),
        "short_description": "Видео из TikTok, Instagram, YouTube и других сайтов. Пачками, до {limit}.",
        "no_links": "Не нашёл ссылок. Пришлите ссылку на видео или пост.",
        "txt_too_big": "Файл со ссылками слишком большой, нужен .txt до 1 МБ.",
        "closed": "Бот сейчас работает только для своих. Если вам нужен доступ, напишите владельцу.",
        "status_one": "Скачиваю…",
        "status_many": "Обрабатываю ссылки: {processed} из {total}",
        "status_failed": "Не получилось: {failed}",
        "status_queue": "В очереди: {queued}",
        "summary_ok": "Готово, всё скачал: {total}.",
        "summary_partial": "Готово: {ok} из {total}.",
        "summary_cancelled": "Остановлено. Успел прислать {ok} из {total}.",
        "summary_failed_head": "Не получилось:",
        "summary_more": "…и ещё {n}",
        "error_one": "Не получилось скачать: {reason}.\n{url}",
        "cancelled": "Остановил. Отменено задач: {n}.",
        "nothing_to_cancel": "Очередь и так пуста.",
        "retry_started": "Пробую ещё раз: {n}.",
        "retry_nothing": "Повторять нечего.",
        "btn_as_file": "Прислать файлом",
        "btn_retry": "Повторить неудачные",
        "btn_cancel": "Остановить",
        "btn_economy": "Скачать в 720p",
        "btn_split": "Прислать частями",
        "too_large": "Файл больше {limit}, Telegram такой не примет. Скачать в 720p или прислать частями?",
        "settings_title": "<b>Настройки</b>\nНажмите на пункт, чтобы переключить.",
        "set_quality_max": "Качество: максимум",
        "set_quality_economy": "Качество: экономия (до 720p)",
        "set_mode_media": "Отправка: видео и фото",
        "set_mode_document": "Отправка: файлом, без сжатия",
        "set_caption_on": "Подпись с автором и текстом: да",
        "set_caption_off": "Подпись с автором и текстом: нет",
        "set_footer_on": "Строка с ботом внизу: да",
        "set_footer_off": "Строка с ботом внизу: нет",
        "set_lang": "Язык: Русский",
        "saved": "Сохранено",
        "admin_only": "Команда только для администратора.",
        "admin_help": (
            "/stats  статистика\n/access open|list  режим доступа\n/allow ID, /deny ID  список доступа\n"
            "/ban ID, /unban ID  бан\n/update  проверить yt-dlp и gallery-dl\n/rollback  откатить экстракторы"
        ),
        "admin_done": "Готово.",
        "admin_bad_id": "Нужен числовой ID пользователя.",
        "update_started": "Проверяю обновления, это займёт пару минут.",
        "update_changed": "Обновил экстракторы: {versions}. Проверочные ссылки: {ok} из {total}.",
        "update_same": "Новых версий нет или они хуже текущих: {versions}. Проверка: {ok} из {total}. {note}",
        "rollback_done": "Откатил на предыдущую версию экстракторов.",
        "rollback_none": "Откатывать некуда, работает версия из образа.",
    },
    "en": {
        "start": (
            "Hi! I'm Grabby. Send me a link to a video or a post and I'll send it back in the best "
            "quality, without watermarks.\n\n"
            "<b>Where from</b>: TikTok (videos and slideshows), Instagram (Reels, posts, carousels), "
            "YouTube and Shorts, X, Facebook, Pinterest, Reddit, VK, Threads, Twitch, Vimeo and hundreds "
            "of other sites.\n\n"
            "<b>As many as you like</b>: several links in one message, a stack of forwarded posts or a "
            ".txt file with a list. Everything comes back in order, files up to {limit}.\n\n"
            "/settings  quality, format, captions, language\n"
            "/cancel  stop the queue"
        ),
        "description": (
            "I download videos and photos from TikTok (no watermark), Instagram, YouTube, X, Pinterest, "
            "Reddit, VK, Threads and other sites. Send many links at once, files up to {limit}."
        ),
        "short_description": "Videos from TikTok, Instagram, YouTube and more. In batches, up to {limit}.",
        "no_links": "I couldn't find any links. Send me a link to a video or a post.",
        "txt_too_big": "That file is too big, I need a .txt under 1 MB.",
        "closed": "This bot is invite-only right now. Ask the owner if you need access.",
        "status_one": "Downloading…",
        "status_many": "Working on links: {processed} of {total}",
        "status_failed": "Failed: {failed}",
        "status_queue": "Queued: {queued}",
        "summary_ok": "Done, all {total} downloaded.",
        "summary_partial": "Done: {ok} of {total}.",
        "summary_cancelled": "Stopped. Sent {ok} of {total}.",
        "summary_failed_head": "Couldn't download:",
        "summary_more": "…and {n} more",
        "error_one": "Couldn't download this one: {reason}.\n{url}",
        "cancelled": "Stopped. Cancelled jobs: {n}.",
        "nothing_to_cancel": "The queue is already empty.",
        "retry_started": "Trying again: {n}.",
        "retry_nothing": "Nothing to retry.",
        "btn_as_file": "Send as file",
        "btn_retry": "Retry failed",
        "btn_cancel": "Stop",
        "btn_economy": "Get 720p",
        "btn_split": "Send in parts",
        "too_large": (
            "The file is over {limit} and Telegram won't take it. Get 720p instead or send it in parts?"
        ),
        "settings_title": "<b>Settings</b>\nTap an option to switch it.",
        "set_quality_max": "Quality: maximum",
        "set_quality_economy": "Quality: data saver (up to 720p)",
        "set_mode_media": "Send as: video and photo",
        "set_mode_document": "Send as: file, uncompressed",
        "set_caption_on": "Caption with author and text: on",
        "set_caption_off": "Caption with author and text: off",
        "set_footer_on": "Bot line at the bottom: on",
        "set_footer_off": "Bot line at the bottom: off",
        "set_lang": "Language: English",
        "saved": "Saved",
        "admin_only": "Admins only.",
        "admin_help": (
            "/stats  statistics\n/access open|list  access mode\n/allow ID, /deny ID  access list\n"
            "/ban ID, /unban ID  ban\n/update  check yt-dlp and gallery-dl\n/rollback  roll extractors back"
        ),
        "admin_done": "Done.",
        "admin_bad_id": "I need a numeric user ID.",
        "update_started": "Checking for updates, this takes a couple of minutes.",
        "update_changed": "Extractors updated: {versions}. Probe links: {ok} of {total}.",
        "update_same": "No better version: {versions}. Probes: {ok} of {total}. {note}",
        "rollback_done": "Rolled the extractors back to the previous version.",
        "rollback_none": "Nothing to roll back to, the image's version is in use.",
    },
}

_ERRORS: dict[str, dict[ErrorKind, str]] = {
    "ru": {
        ErrorKind.NOT_FOUND: "пост удалён или недоступен",
        ErrorKind.PRIVATE: "аккаунт закрыт",
        ErrorKind.LOGIN_REQUIRED: "сайт пускает только после входа в аккаунт",
        ErrorKind.AGE_RESTRICTED: "стоит ограничение по возрасту",
        ErrorKind.GEO_BLOCKED: "недоступно в стране сервера",
        ErrorKind.UNSUPPORTED: "этот сайт не поддерживается",
        ErrorKind.NOT_A_POST: "это профиль или плейлист, нужна ссылка на конкретный пост",
        ErrorKind.NO_MEDIA: "в посте нет видео или фото",
        ErrorKind.TOO_LARGE: "файл больше {limit}",
        ErrorKind.RATE_LIMITED: "сайт временно ограничил загрузки, попробуйте позже",
        ErrorKind.NETWORK: "сайт не ответил",
        ErrorKind.TIMEOUT: "загрузка шла слишком долго",
        ErrorKind.LIVE: "это прямой эфир",
        ErrorKind.CANCELLED: "отменено",
        ErrorKind.UNKNOWN: "что-то пошло не так",
    },
    "en": {
        ErrorKind.NOT_FOUND: "the post was deleted or isn't available",
        ErrorKind.PRIVATE: "the account is private",
        ErrorKind.LOGIN_REQUIRED: "the site only shows it after logging in",
        ErrorKind.AGE_RESTRICTED: "it's age-restricted",
        ErrorKind.GEO_BLOCKED: "it's blocked in the server's country",
        ErrorKind.UNSUPPORTED: "this site isn't supported",
        ErrorKind.NOT_A_POST: "that's a profile or a playlist, I need a link to a single post",
        ErrorKind.NO_MEDIA: "there's no video or photo in the post",
        ErrorKind.TOO_LARGE: "the file is over {limit}",
        ErrorKind.RATE_LIMITED: "the site is limiting downloads right now, try later",
        ErrorKind.NETWORK: "the site didn't respond",
        ErrorKind.TIMEOUT: "the download took too long",
        ErrorKind.LIVE: "it's a live stream",
        ErrorKind.CANCELLED: "cancelled",
        ErrorKind.UNKNOWN: "something went wrong",
    },
}


def pick_lang(code: str | None) -> str:
    if code and code.split("-")[0].lower() in ("ru", "uk", "be", "kk", "hy", "uz", "ky"):
        return "ru"
    return "en"


def t(lang: str, key: str, **kwargs: object) -> str:
    table = _TEXTS.get(lang, _TEXTS["en"])
    text = table.get(key) or _TEXTS["en"][key]
    return text.format(**kwargs) if kwargs else text


def error_text(lang: str, kind: ErrorKind | None, *, limit: str = "") -> str:
    table = _ERRORS.get(lang, _ERRORS["en"])
    return table[kind or ErrorKind.UNKNOWN].format(limit=limit)


def size_label(lang: str, size: int) -> str:
    mb = size / 1024 / 1024
    if mb >= 1000:
        return f"{mb / 1000:.0f} ГБ" if lang == "ru" else f"{mb / 1000:.0f} GB"
    return f"{mb:.0f} МБ" if lang == "ru" else f"{mb:.0f} MB"
