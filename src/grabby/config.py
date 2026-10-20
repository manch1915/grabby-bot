from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from grabby.domain.platforms import Platform


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    # Local Bot API server (telegram-bot-api --local). Empty means api.telegram.org with its 50 MB cap.
    bot_api_url: str = ""
    bot_api_local: bool = True
    redis_url: str = "redis://localhost:6379/0"

    work_dir: Path = Path("/work")
    work_dir_limit_mb: int = 4096
    min_free_disk_mb: int = 1024
    data_dir: Path = Path("/data")

    max_parallel: int = 3
    max_parallel_per_user: int = 2
    platform_limits: Annotated[dict[Platform, int], NoDecode] = Field(
        default_factory=lambda: {Platform.TIKTOK: 2, Platform.INSTAGRAM: 2, Platform.YOUTUBE: 2}
    )
    default_platform_limit: int = 3
    # Seconds between job starts per site. Instagram flags accounts that fetch in bursts.
    platform_intervals: Annotated[dict[Platform, float], NoDecode] = Field(
        default_factory=lambda: {Platform.INSTAGRAM: 2.0}
    )
    download_timeout_s: int = 900
    max_items_per_link: int = 20
    max_retries: int = 2
    retry_base_delay_s: float = 4

    proxy_urls: Annotated[list[str], NoDecode] = Field(default_factory=list)
    proxy_platforms: Annotated[list[Platform], NoDecode] = Field(
        default_factory=lambda: [Platform.INSTAGRAM, Platform.TIKTOK]
    )

    access_mode: str = "open"
    footer_text: str = ""
    cache_ttl_days: int = 30
    status_edit_interval_s: float = 3.0

    auto_update: bool = True
    update_interval_h: float = 12
    update_channel: str = "nightly"
    update_probe_urls: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "https://www.tiktok.com/@hankgreen1/video/7047596209028074758",
            "https://www.instagram.com/reel/Chunk8-jurw/",
            "https://www.pinterest.com/pin/664281013778109217/",
        ]
    )

    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _ints(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(v) for v in value.replace(";", ",").split(",") if v.strip()]
        return value

    @field_validator("proxy_urls", "update_probe_urls", "proxy_platforms", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @field_validator("platform_limits", "platform_intervals", mode="before")
    @classmethod
    def _per_platform(cls, value: object) -> object:
        if isinstance(value, str):
            pairs = (item.split("=", 1) for item in value.split(",") if "=" in item)
            return {Platform(k.strip()): float(v) for k, v in pairs}
        return value

    @property
    def cookies_dir(self) -> Path:
        return self.data_dir / "cookies"

    @property
    def extractors_dir(self) -> Path:
        return self.data_dir / "extractors"

    @property
    def max_upload_bytes(self) -> int:
        # api.telegram.org takes 50 MB from bots; a local server in --local mode takes 2000 MB.
        return (2000 if self.bot_api_url and self.bot_api_local else 50) * 1024 * 1024

    def platform_limit(self, platform: Platform) -> int:
        return self.platform_limits.get(platform, self.default_platform_limit)

    def platform_interval(self, platform: Platform) -> float:
        return self.platform_intervals.get(platform, 0.0)

    def cookie_files(self, platform: Platform) -> list[Path]:
        """instagram.txt, instagram-2.txt, ... Several accounts share the load."""
        if not self.cookies_dir.is_dir():
            return []
        return sorted(p for p in self.cookies_dir.glob(f"{platform.value}*.txt") if p.is_file())
