from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Self

# Telegram limits (local Bot API server).
MAX_UPLOAD_BYTES = 2000 * 1024 * 1024
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTO_SIDES = 10_000
ALBUM_SIZE = 10

STREAMABLE_VCODECS = frozenset({"h264", "hevc"})
MP4_SAFE_ACODECS = frozenset({"aac", "mp3", "alac", "ac3", "eac3", "opus", "flac"})
PHOTO_EXTS = frozenset({"jpg", "jpeg", "png"})
# A short clip in an odd codec is cheap to re-encode; a long one goes as a file.
MAX_TRANSCODE_SECONDS = 180


class MediaKind(StrEnum):
    VIDEO = "video"
    PHOTO = "photo"
    AUDIO = "audio"
    ANIMATION = "animation"
    DOCUMENT = "document"


class Quality(StrEnum):
    MAX = "max"
    ECONOMY = "economy"


@dataclass(slots=True)
class MediaItem:
    path: str
    kind: MediaKind
    size: int = 0
    width: int = 0
    height: int = 0
    duration: int = 0
    thumbnail: str | None = None
    title: str | None = None
    performer: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**{**data, "kind": MediaKind(data["kind"])})


@dataclass(slots=True)
class PostInfo:
    url: str
    author: str | None = None
    title: str | None = None
    text: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**data)


@dataclass(slots=True)
class MediaBundle:
    info: PostInfo
    items: list[MediaItem] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            info=PostInfo.from_dict(data["info"]),
            items=[MediaItem.from_dict(item) for item in data["items"]],
        )


class VideoAction(StrEnum):
    KEEP = "keep"
    REMUX = "remux"
    TRANSCODE = "transcode"
    AS_DOCUMENT = "as_document"


def plan_video(container: str, vcodec: str, acodec: str | None, duration: float) -> VideoAction:
    """Decide what a downloaded video needs before Telegram can play it inline."""
    audio_ok = acodec is None or acodec in MP4_SAFE_ACODECS
    if vcodec in STREAMABLE_VCODECS and audio_ok:
        return VideoAction.KEEP if container == "mp4" else VideoAction.REMUX
    if duration and duration <= MAX_TRANSCODE_SECONDS:
        return VideoAction.TRANSCODE
    return VideoAction.AS_DOCUMENT


def photo_fits(size: int, width: int, height: int) -> bool:
    if size > MAX_PHOTO_BYTES or width + height > MAX_PHOTO_SIDES:
        return False
    if width and height:
        ratio = max(width, height) / min(width, height)
        return ratio <= 20
    return True


def chunk_album(items: list[MediaItem]) -> list[list[MediaItem]]:
    """Split media into Telegram albums. Audio and documents can't mix with photos/videos."""
    visual = [i for i in items if i.kind in (MediaKind.PHOTO, MediaKind.VIDEO)]
    return [visual[i : i + ALBUM_SIZE] for i in range(0, len(visual), ALBUM_SIZE)]
