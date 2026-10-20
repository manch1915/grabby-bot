import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grabby.domain.media import (
    MAX_UPLOAD_BYTES,
    PHOTO_EXTS,
    MediaItem,
    MediaKind,
    VideoAction,
    photo_fits,
    plan_video,
)

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
VIDEO_EXTS = frozenset({"mp4", "m4v", "mov", "webm", "mkv", "flv", "avi", "ts", "3gp"})
AUDIO_EXTS = frozenset({"mp3", "m4a", "aac", "ogg", "opus", "wav", "flac"})
IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "webp", "heic", "heif", "avif", "bmp", "gif"})
# Downloaded files come from strangers. A "video" that is really an HLS or concat
# playlist could make ffmpeg read local files, so only plain media demuxers may open it.
_DEMUXERS = (
    "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,mp3,flac,ogg,wav,aac,flv,mpegts,avi,gif,"
    "image2,jpeg_pipe,png_pipe,webp_pipe,bmp_pipe"
)
SAFE_INPUT = ("-protocol_whitelist", "file", "-format_whitelist", _DEMUXERS)


def _input(path: Path) -> list[str]:
    return [*SAFE_INPUT, "-i", str(path)]


@dataclass(slots=True)
class Probe:
    container: str
    vcodec: str | None
    acodec: str | None
    width: int
    height: int
    duration: float


def probe(path: Path) -> Probe:
    out = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            *SAFE_INPUT,
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    data = json.loads(out or b"{}")
    video: dict[str, Any] = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio: dict[str, Any] = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    fmt = data.get("format", {})
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = _rotation(video)
    if rotation in (90, 270):
        width, height = height, width
    return Probe(
        container="mp4" if "mp4" in fmt.get("format_name", "") else fmt.get("format_name", ""),
        vcodec=video.get("codec_name"),
        acodec=audio.get("codec_name"),
        width=width,
        height=height,
        duration=float(fmt.get("duration") or video.get("duration") or 0),
    )


def _rotation(stream: dict[str, object]) -> int:
    for side in stream.get("side_data_list", []) or []:  # type: ignore[attr-defined]
        if isinstance(side, dict) and "rotation" in side:
            return abs(int(side["rotation"])) % 360
    tags = stream.get("tags") or {}
    if isinstance(tags, dict) and "rotate" in tags:
        return abs(int(tags["rotate"])) % 360
    return 0


def _ffmpeg(*args: str, timeout: int = 900) -> None:
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        timeout=timeout,
    )


def remux(src: Path) -> Path:
    dst = src.with_name(src.stem + ".remux.mp4")
    _ffmpeg(
        *_input(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", str(dst)
    )
    src.unlink(missing_ok=True)
    return dst


def transcode(src: Path) -> Path:
    # Only short clips get here; one thread keeps the shared server responsive.
    dst = src.with_name(src.stem + ".h264.mp4")
    _ffmpeg(
        *_input(src), "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-threads", "1", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst),
    )  # fmt: skip
    src.unlink(missing_ok=True)
    return dst


def make_thumbnail(video: Path, duration: float) -> Path | None:
    thumb = video.with_name(video.stem + ".thumb.jpg")
    at = "1" if duration >= 2 else "0"
    try:
        _ffmpeg(
            "-ss", at, *_input(video), "-frames:v", "1",
            "-vf", "scale='if(gt(iw,ih),320,-2)':'if(gt(iw,ih),-2,320)'", "-q:v", "4", str(thumb),
            timeout=60,
        )  # fmt: skip
    except (subprocess.SubprocessError, OSError):
        return None
    return thumb if thumb.exists() and thumb.stat().st_size < 200_000 else None


def to_jpeg(src: Path) -> Path:
    dst = src.with_suffix(".jpg") if src.suffix.lower() != ".jpg" else src.with_name(src.stem + ".conv.jpg")
    _ffmpeg(*_input(src), "-frames:v", "1", "-q:v", "2", str(dst), timeout=60)
    src.unlink(missing_ok=True)
    return dst


def split_video(src: Path, duration: float, limit: int = MAX_UPLOAD_BYTES) -> list[Path]:
    """Cut a too-big video into playable parts by time, without re-encoding."""
    size = src.stat().st_size
    parts = math.ceil(size / (limit * 0.9))
    segment = max(1, math.floor(duration / parts))
    pattern = src.with_name(src.stem + ".part%02d.mp4")
    _ffmpeg(
        *_input(src), "-map", "0", "-c", "copy", "-f", "segment", "-segment_time", str(segment),
        "-reset_timestamps", "1", "-movflags", "+faststart", str(pattern),
    )  # fmt: skip
    src.unlink(missing_ok=True)
    return sorted(src.parent.glob(src.stem + ".part*.mp4"))


def prepare_video(path: Path, *, as_document: bool) -> MediaItem:
    info = probe(path)
    if as_document:
        return MediaItem(
            str(path), MediaKind.DOCUMENT, path.stat().st_size, info.width, info.height, round(info.duration)
        )
    if info.vcodec is None:
        return prepare_audio(path)
    action = plan_video(info.container, info.vcodec, info.acodec, info.duration)
    if action is VideoAction.REMUX:
        path = remux(path)
    elif action is VideoAction.TRANSCODE:
        path = transcode(path)
    elif action is VideoAction.AS_DOCUMENT:
        return MediaItem(
            str(path), MediaKind.DOCUMENT, path.stat().st_size, info.width, info.height, round(info.duration)
        )
    thumb = make_thumbnail(path, info.duration)
    return MediaItem(
        path=str(path),
        kind=MediaKind.VIDEO,
        size=path.stat().st_size,
        width=info.width,
        height=info.height,
        duration=round(info.duration),
        thumbnail=str(thumb) if thumb else None,
    )


def prepare_photo(path: Path, *, as_document: bool) -> MediaItem:
    ext = path.suffix.lower().lstrip(".")
    if ext == "gif":
        return MediaItem(str(path), MediaKind.ANIMATION, path.stat().st_size)
    if ext not in PHOTO_EXTS:
        path = to_jpeg(path)
    if as_document:
        return MediaItem(str(path), MediaKind.DOCUMENT, path.stat().st_size)
    try:
        info = probe(path)
        width, height = info.width, info.height
    except (subprocess.SubprocessError, OSError, ValueError):
        width = height = 0
    size = path.stat().st_size
    kind = MediaKind.PHOTO if photo_fits(size, width, height) else MediaKind.DOCUMENT
    return MediaItem(str(path), kind, size, width, height)


def prepare_audio(path: Path, title: str | None = None, performer: str | None = None) -> MediaItem:
    try:
        duration = round(probe(path).duration)
    except (subprocess.SubprocessError, OSError, ValueError):
        duration = 0
    return MediaItem(
        str(path), MediaKind.AUDIO, path.stat().st_size, duration=duration, title=title, performer=performer
    )


def prepare(path: Path, *, as_document: bool) -> MediaItem:
    ext = path.suffix.lower().lstrip(".")
    if ext in IMAGE_EXTS:
        return prepare_photo(path, as_document=as_document)
    if ext in AUDIO_EXTS:
        return prepare_audio(path)
    if ext in VIDEO_EXTS:
        return prepare_video(path, as_document=as_document)
    return MediaItem(str(path), MediaKind.DOCUMENT, path.stat().st_size)
