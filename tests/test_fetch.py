"""Post-processing against real ffmpeg output. Skipped where ffmpeg isn't installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

from grabby.domain.errors import ErrorKind
from grabby.domain.media import MediaBundle, MediaItem, MediaKind, PostInfo, Quality
from grabby.domain.platforms import Platform
from grabby.fetch import postprocess
from grabby.fetch.protocol import FetchRequest, FetchResult
from grabby.fetch.ytdlp_backend import FILE_FORMAT, _format, post_info

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_clip(path: Path, *codec: str, seconds: int = 3) -> Path:
    video = f"testsrc=size=320x568:rate=25:duration={seconds}"
    audio = f"sine=frequency=440:duration={seconds}"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio,
         *codec, str(path)],
        check=True,
    )  # fmt: skip
    return path


@needs_ffmpeg
def test_h264_mp4_is_kept_with_metadata_and_thumbnail(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "a.mp4", "-c:v", "libx264", "-c:a", "aac")
    item = postprocess.prepare(clip, as_document=False)
    assert item.kind is MediaKind.VIDEO
    assert (item.width, item.height, item.duration) == (320, 568, 3)
    assert item.path == str(clip)
    assert item.thumbnail and Path(item.thumbnail).stat().st_size < 200_000


@needs_ffmpeg
def test_h264_in_mkv_is_remuxed_not_reencoded(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "a.mkv", "-c:v", "libx264", "-c:a", "aac")
    item = postprocess.prepare(clip, as_document=False)
    assert item.path.endswith(".remux.mp4")
    assert postprocess.probe(Path(item.path)).vcodec == "h264"


@needs_ffmpeg
def test_short_vp9_is_transcoded(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "a.webm", "-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus")
    item = postprocess.prepare(clip, as_document=False)
    probe = postprocess.probe(Path(item.path))
    assert (probe.vcodec, probe.acodec) == ("h264", "aac")


@needs_ffmpeg
def test_document_mode_keeps_the_original(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "a.webm", "-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus")
    item = postprocess.prepare(clip, as_document=True)
    assert item.kind is MediaKind.DOCUMENT
    assert item.path == str(clip)


@needs_ffmpeg
def test_split_into_parts(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "a.mp4", "-c:v", "libx264", "-g", "25", "-c:a", "aac", seconds=10)
    size = clip.stat().st_size
    parts = postprocess.split_video(clip, 10, limit=size // 2)
    assert len(parts) >= 2
    assert all(p.stat().st_size < size for p in parts)


@needs_ffmpeg
def test_webp_photo_becomes_jpeg(tmp_path: Path) -> None:
    src = tmp_path / "p.webp"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x480",
            "-frames:v",
            "1",
            str(src),
        ],
        check=True,
    )
    item = postprocess.prepare(src, as_document=False)
    assert item.kind is MediaKind.PHOTO
    assert item.path.endswith(".jpg")
    assert (item.width, item.height) == (640, 480)


def test_protocol_roundtrip() -> None:
    request = FetchRequest(
        url="u", platform="tiktok", workdir="/w", quality=Quality.ECONOMY, as_document=True
    )
    assert FetchRequest.from_json(request.to_json()) == request
    bundle = MediaBundle(info=PostInfo(url="u"), items=[MediaItem("p.jpg", MediaKind.PHOTO, size=1)])
    result = FetchResult(bundle=bundle, versions={"yt-dlp": "2026.08.19"})
    assert FetchResult.from_json(result.to_json()) == result
    failed = FetchResult(error_kind=ErrorKind.PRIVATE, error="x")
    assert not FetchResult.from_json(failed.to_json()).ok


@pytest.mark.parametrize(
    ("platform", "info", "author", "text"),
    [
        (
            Platform.TIKTOK,
            {"uploader": "hankgreen1", "title": "TikTok video #7047596209028074758"},
            "@hankgreen1",
            None,
        ),
        (
            Platform.INSTAGRAM,
            {"uploader": "Instagram", "channel": "instagram", "description": "Gingerton"},
            "@instagram",
            "Gingerton",
        ),
        (
            Platform.YOUTUBE,
            {"uploader": "jawed", "title": "Me at the zoo", "description": "long"},
            "jawed",
            "Me at the zoo",
        ),
        (
            Platform.X,
            {"uploader": "Star Wars", "uploader_id": "starwars", "description": "May the 4th"},
            "@starwars",
            "May the 4th",
        ),
    ],
)
def test_post_info(platform: Platform, info: dict[str, str], author: str, text: str | None) -> None:
    result = post_info(info, "https://u", platform)
    assert result.author == author
    assert result.text == text


def test_instagram_prefers_progressive_h264_over_vp9_dash() -> None:
    video = FetchRequest(url="u", platform="instagram", workdir="/w")
    original = FetchRequest(url="u", platform="instagram", workdir="/w", as_document=True)
    assert "b[format_id!*=dash]" in _format(video, Platform.INSTAGRAM)
    assert _format(original, Platform.INSTAGRAM) == FILE_FORMAT
    assert "format_id" not in _format(video, Platform.TIKTOK)
