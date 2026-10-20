from pathlib import Path

from grabby.domain.errors import DownloadError, ErrorKind
from grabby.domain.media import MAX_UPLOAD_BYTES, MediaBundle, MediaItem, MediaKind, PostInfo
from grabby.domain.platforms import Platform
from grabby.fetch import gallery_backend, postprocess, threads, tiktok_photos, ytdlp_backend
from grabby.fetch.protocol import FetchRequest

_GALLERY_FALLBACK = frozenset({ErrorKind.NO_MEDIA, ErrorKind.UNSUPPORTED, ErrorKind.UNKNOWN})


def acquire(req: FetchRequest) -> MediaBundle:
    platform = Platform(req.platform)
    if platform is Platform.THREADS:
        return _finish(req, *threads.fetch(req))

    if platform is Platform.TIKTOK and "/photo/" in req.url:
        return _tiktok_photos(req)

    try:
        result = ytdlp_backend.fetch(req, platform, images_from_thumbnails=platform is Platform.INSTAGRAM)
    except DownloadError as error:
        if not _try_gallery(error, platform, req):
            raise
        return _gallery(req, error)

    if result.audio_only:
        # A slideshow opened through a /video/ link: yt-dlp only got the soundtrack.
        for path in result.files:
            path.unlink(missing_ok=True)
        return _tiktok_photos(req)
    return _finish(req, result.info, result.files)


def _try_gallery(error: DownloadError, platform: Platform, req: FetchRequest) -> bool:
    if platform in (Platform.YOUTUBE, Platform.TIKTOK, Platform.TWITCH, Platform.VIMEO):
        return False
    if error.kind in _GALLERY_FALLBACK:
        return True
    return (
        platform is Platform.INSTAGRAM and error.kind is ErrorKind.LOGIN_REQUIRED and bool(req.cookies_file)
    )


def _gallery(req: FetchRequest, first_error: DownloadError) -> MediaBundle:
    try:
        info, files = gallery_backend.fetch(req)
    except DownloadError as error:
        # Keep whichever explanation tells the user more.
        if first_error.kind in _GALLERY_FALLBACK and error.kind not in (
            ErrorKind.UNSUPPORTED,
            ErrorKind.UNKNOWN,
        ):
            raise error from first_error
        raise first_error from error
    return _finish(req, info, files)


def _tiktok_photos(req: FetchRequest) -> MediaBundle:
    info, images, audio = tiktok_photos.fetch(req)
    return _finish(req, info, images, audio)


def _finish(req: FetchRequest, info: PostInfo, files: list[Path], audio: Path | None = None) -> MediaBundle:
    bundle = MediaBundle(info=info)
    if req.probe_only:
        return bundle
    for path in files:
        bundle.items.extend(_prepare(req, path))
    if audio is not None:
        bundle.items.append(postprocess.prepare_audio(audio, performer=info.author))
    if not bundle.items:
        raise DownloadError(ErrorKind.NO_MEDIA)
    return bundle


def _prepare(req: FetchRequest, path: Path) -> list[MediaItem]:
    item = postprocess.prepare(path, as_document=req.as_document)
    if item.size <= min(req.max_bytes, MAX_UPLOAD_BYTES):
        return [item]
    if req.split_parts and item.kind in (MediaKind.VIDEO, MediaKind.DOCUMENT) and item.duration:
        if item.thumbnail:
            Path(item.thumbnail).unlink(missing_ok=True)
        parts = postprocess.split_video(Path(item.path), item.duration, min(req.max_bytes, MAX_UPLOAD_BYTES))
        return [postprocess.prepare(part, as_document=req.as_document) for part in parts]
    raise DownloadError(ErrorKind.TOO_LARGE, f"{item.size} bytes")
