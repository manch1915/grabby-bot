import asyncio
import logging

from grabby.app import run
from grabby.config import Settings


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
