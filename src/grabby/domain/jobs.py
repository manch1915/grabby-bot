import secrets
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Self

from grabby.domain.errors import ErrorKind
from grabby.domain.media import Quality
from grabby.domain.platforms import Platform


def new_id() -> str:
    return secrets.token_hex(6)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


@dataclass(slots=True)
class Job:
    batch_id: str
    user_id: int
    chat_id: int
    index: int
    url: str
    platform: Platform
    quality: Quality = Quality.MAX
    as_document: bool = False
    split_parts: bool = False
    status: JobStatus = JobStatus.QUEUED
    attempts: int = 0
    error_kind: ErrorKind | None = None
    error: str = ""
    id: str = field(default_factory=new_id)

    @property
    def cache_key(self) -> str:
        variant = f"{self.quality.value}:{'doc' if self.as_document else 'media'}"
        return f"{variant}:{self.url}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        kind = data.get("error_kind")
        return cls(
            **{
                **data,
                "platform": Platform(data["platform"]),
                "quality": Quality(data["quality"]),
                "status": JobStatus(data["status"]),
                "error_kind": ErrorKind(kind) if kind else None,
            }
        )


@dataclass(slots=True)
class Batch:
    user_id: int
    chat_id: int
    lang: str
    total: int = 0
    done: int = 0
    failed: int = 0
    cancelled: int = 0
    duplicates: int = 0
    delivered: int = 0
    status_message_id: int | None = None
    reply_to: int | None = None
    closed: bool = False
    created: float = field(default_factory=time.time)
    id: str = field(default_factory=new_id)

    @property
    def processed(self) -> int:
        return self.done + self.failed + self.cancelled

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.delivered >= self.total

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**data)


class SendMode(StrEnum):
    MEDIA = "media"
    DOCUMENT = "document"


@dataclass(slots=True)
class UserSettings:
    lang: str = "ru"
    quality: Quality = Quality.MAX
    send_mode: SendMode = SendMode.MEDIA
    caption: bool = True
    footer: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        base = cls()
        return cls(
            lang=data.get("lang", base.lang),
            quality=Quality(data.get("quality", base.quality)),
            send_mode=SendMode(data.get("send_mode", base.send_mode)),
            caption=bool(data.get("caption", base.caption)),
            footer=bool(data.get("footer", base.footer)),
        )
