import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError as YtDlpError

from grabby.domain.errors import DownloadError, ErrorKind, classify
from grabby.domain.media import PostInfo, Quality
from grabby.domain.platforms import Platform
from grabby.fetch.protocol import FetchRequest

# TikTok answers the default UA with an empty page lately, a real browser UA helps.
# Instagram is the opposite: a custom UA breaks it, so we only set it where needed.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
_PLAYABLE = "[vcodec~='^(avc|h264|hevc|hvc1|hev1|h265)']"
_PLAYABLE_FIRST = f"bv*{_PLAYABLE}+ba[acodec~='^(mp4a|aac)']/bv*{_PLAYABLE}+ba/b{_PLAYABLE}"
VIDEO_FORMAT = f"{_PLAYABLE_FIRST}/bv*+ba/b"
# Instagram serves DASH reels as VP9 only, while the progressive MP4s (no codec info in
# the metadata) are H.264 with sound. Those play inline; the VP9 original is for "send as file".
INSTAGRAM_VIDEO_FORMAT = f"{_PLAYABLE_FIRST}/b[format_id!*=dash]/bv*+ba/b"
FILE_FORMAT = "bv*+ba/b"
SOCIAL = frozenset(
    {
        Platform.TIKTOK, Platform.INSTAGRAM, Platform.X, Platform.THREADS, Platform.REDDIT,
        Platform.FACEBOOK, Platform.VK, Platform.PINTEREST, Platform.LIKEE, Platform.SNAPCHAT,
    }
)  # fmt: skip


class _Logger:
    """Keeps warnings: with ignore_no_formats_error the real reason only shows up there."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, msg: str) -> None:
        # The size cap is reported as a plain screen message, not as an error.
        if "max-filesize" in msg:
            self.messages.append(msg)

    def info(self, msg: str) -> None:
        self.debug(msg)

    def warning(self, msg: str) -> None:
        self.messages.append(msg)
        print(msg, file=sys.stderr)

    def error(self, msg: str) -> None:
        self.messages.append(msg)
        print(msg, file=sys.stderr)

    def explain(self) -> DownloadError:
        for message in reversed(self.messages):
            kind = classify(message)
            if kind not in (ErrorKind.UNKNOWN, ErrorKind.NO_MEDIA):
                return DownloadError(kind, message.removeprefix("ERROR: "))
        return DownloadError(ErrorKind.NO_MEDIA, "no video in this post")


@dataclass(slots=True)
class Downloaded:
    info: PostInfo
    files: list[Path] = field(default_factory=list)
    audio_only: bool = False


def _format(req: FetchRequest, platform: Platform) -> str:
    if req.as_document:
        return FILE_FORMAT
    return INSTAGRAM_VIDEO_FORMAT if platform is Platform.INSTAGRAM else VIDEO_FORMAT


def build_options(req: FetchRequest, platform: Platform, logger: _Logger) -> dict[str, Any]:
    sort = ["res", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"]
    if req.quality is Quality.ECONOMY:
        sort = ["res:720", "vcodec:h264", "acodec:aac", "size"]
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": logger,
        "paths": {"home": req.workdir, "temp": req.workdir},
        "outtmpl": "%(id).60s.%(playlist_index|0)s.%(ext)s",
        "restrictfilenames": True,
        "format": _format(req, platform),
        "format_sort": sort,
        "merge_output_format": "mp4/mkv" if req.as_document else "mp4",
        "noplaylist": True,
        "playlist_items": f"1:{req.max_items}",
        "retries": 3,
        "fragment_retries": 5,
        "extractor_retries": 2,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
        "overwrites": True,
    }
    if not req.split_parts:
        # Don't pull a 3 GB file only to learn Telegram won't take it.
        opts["max_filesize"] = req.max_bytes
    if req.cookies_file:
        opts["cookiefile"] = req.cookies_file
    if req.proxy:
        opts["proxy"] = req.proxy
    if platform is Platform.TIKTOK:
        opts["http_headers"] = {"User-Agent": BROWSER_UA}
    return opts


# yt-dlp fills empty titles with placeholders like "TikTok video #123" or "Video by xyz".
_PLACEHOLDER_TITLE = re.compile(
    r"^(TikTok video #\d+|(Video|Photo|Photos|Post) by .+|Instagram (post|reel).*)$", re.I
)
_AT_HANDLES = frozenset({Platform.TIKTOK, Platform.INSTAGRAM, Platform.X, Platform.THREADS})


def post_info(info: dict[str, Any], url: str, platform: Platform) -> PostInfo:
    author = info.get("uploader") or info.get("channel") or info.get("uploader_id") or info.get("creator")
    handle = info.get("channel") if platform is Platform.INSTAGRAM else info.get("uploader_id")
    if platform in _AT_HANDLES and isinstance(handle, str) and handle and not handle.isdigit():
        author = "@" + handle.lstrip("@")
    elif platform is Platform.TIKTOK and isinstance(author, str):
        author = "@" + author.lstrip("@")
    title = _clean(info.get("title") or info.get("fulltitle"))
    if title and _PLACEHOLDER_TITLE.match(title):
        title = None
    text = _clean(info.get("description")) if platform in SOCIAL else None
    return PostInfo(url=url, author=_clean(author), title=title, text=text or title)


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _best_image(entry: dict[str, Any]) -> str | None:
    thumbs = [t for t in entry.get("thumbnails") or [] if t.get("url")]
    if not thumbs:
        return entry.get("thumbnail")
    # Without dimensions yt-dlp keeps them ordered worst to best.
    sized = [t for t in thumbs if t.get("width")]
    best = max(sized, key=lambda t: t["width"] * (t.get("height") or 1)) if sized else thumbs[-1]
    return str(best["url"])


def _has_video(entry: dict[str, Any]) -> bool:
    return any(f.get("vcodec") not in (None, "none") for f in entry.get("formats") or [])


def _has_audio(entry: dict[str, Any]) -> bool:
    return any(f.get("acodec") not in (None, "none") for f in entry.get("formats") or [])


def fetch(req: FetchRequest, platform: Platform, *, images_from_thumbnails: bool) -> Downloaded:
    logger = _Logger()
    opts = build_options(req, platform, logger)
    # Photo posts only show up as "no formats" entries with a full-size thumbnail.
    opts["ignore_no_formats_error"] = images_from_thumbnails
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            # Extract first, download per entry: yt-dlp refuses to "download" photo
            # entries of a mixed carousel even with ignore_no_formats_error.
            info = ydl.extract_info(req.url, download=False)
            if info is None:
                raise DownloadError(ErrorKind.NO_MEDIA)
            result = Downloaded(info=post_info(info, req.url, platform))
            entries = [e for e in info.get("entries") or [info] if e]
            if req.probe_only:
                return result

            slots: list[Path | str] = []
            for entry in entries:
                if _has_video(entry):
                    done = ydl.process_ie_result(entry, download=True)
                    slots.extend(_downloaded_paths(done))
                elif platform is Platform.TIKTOK and _has_audio(entry):
                    result.audio_only = True
                    return result
                elif images_from_thumbnails and (image := _best_image(entry)):
                    slots.append(image)
            if not slots:
                raise logger.explain()

            for i, slot in enumerate(slots):
                if isinstance(slot, Path):
                    result.files.append(slot)
                    continue
                target = Path(req.workdir) / f"image_{i:02d}.jpg"
                with ydl.urlopen(slot) as resp, target.open("wb") as out:
                    shutil.copyfileobj(resp, out, 1 << 16)
                result.files.append(target)
            return result
    except YtDlpError as exc:
        message = str(exc).removeprefix("ERROR: ")
        raise DownloadError(classify(message), message) from exc


def _downloaded_paths(info: dict[str, Any]) -> list[Path]:
    paths = [Path(d["filepath"]) for d in info.get("requested_downloads") or [] if d.get("filepath")]
    return [p for p in paths if p.exists()]
