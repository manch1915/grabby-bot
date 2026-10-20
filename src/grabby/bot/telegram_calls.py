import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError

log = logging.getLogger(__name__)

_MAX_FLOOD_WAITS = 6
_MAX_NETWORK_RETRIES = 3


async def call[T](make: Callable[[], Awaitable[T]]) -> T:
    """Run a Bot API call, waiting out flood control and retrying transient failures.

    `make` builds a fresh coroutine each time: a 429 on sendMediaGroup must not lose files.
    """
    flood_waits = network_retries = 0
    while True:
        try:
            return await make()
        except TelegramRetryAfter as exc:
            flood_waits += 1
            if flood_waits > _MAX_FLOOD_WAITS:
                raise
            log.info("flood control, sleeping %ss", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 1)
        except (TelegramNetworkError, TelegramServerError) as exc:
            network_retries += 1
            if network_retries > _MAX_NETWORK_RETRIES:
                raise
            delay = 2**network_retries
            log.info("telegram error %s, retrying in %ss", exc, delay)
            await asyncio.sleep(delay)
