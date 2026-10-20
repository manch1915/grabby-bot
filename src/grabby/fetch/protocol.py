"""Messages exchanged between the bot and the fetch subprocess (JSON over stdin/stdout)."""

from dataclasses import asdict, dataclass, field
from typing import Any, Self

from grabby.domain.errors import ErrorKind
from grabby.domain.media import MediaBundle, Quality

RESULT_MARKER = "@@grabby-result "


@dataclass(slots=True)
class FetchRequest:
    url: str
    platform: str
    workdir: str
    quality: Quality = Quality.MAX
    as_document: bool = False
    max_items: int = 20
    max_bytes: int = 2000 * 1024 * 1024
    cookies_file: str | None = None
    proxy: str | None = None
    probe_only: bool = False
    split_parts: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(**{**data, "quality": Quality(data["quality"])})


@dataclass(slots=True)
class FetchResult:
    bundle: MediaBundle | None = None
    error_kind: ErrorKind | None = None
    error: str = ""
    versions: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.bundle is not None and self.error_kind is None

    def to_json(self) -> dict[str, Any]:
        return {
            "bundle": self.bundle.to_dict() if self.bundle else None,
            "error_kind": self.error_kind.value if self.error_kind else None,
            "error": self.error,
            "versions": self.versions,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        bundle = data.get("bundle")
        kind = data.get("error_kind")
        return cls(
            bundle=MediaBundle.from_dict(bundle) if bundle else None,
            error_kind=ErrorKind(kind) if kind else None,
            error=data.get("error", ""),
            versions=data.get("versions", {}),
        )
