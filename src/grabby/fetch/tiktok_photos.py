"""TikTok photo slideshows.

yt-dlp only returns the soundtrack for /photo/ posts and gallery-dl often gets 403
from TikTok, so we reuse yt-dlp's page fetching (impersonation, JS challenge) and
read the slideshow ourselves.
"""

import re
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import ExtractorError

from grabby.domain.errors import DownloadError, ErrorKind, classify
from grabby.domain.media import PostInfo
from grabby.fetch.protocol import FetchRequest
from grabby.fetch.ytdlp_backend import BROWSER_UA

_POST_ID = re.compile(r"/(?:photo|video)/(\d+)")


def fetch(req: FetchRequest) -> tuple[PostInfo, list[Path], Path | None]:
    match = _POST_ID.search(req.url)
    if not match:
        raise DownloadError(ErrorKind.UNSUPPORTED, "not a TikTok post link")
    post_id = match.group(1)
    page_url = req.url.replace("/photo/", "/video/")
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "http_headers": {"User-Agent": BROWSER_UA}}
    if req.cookies_file:
        opts["cookiefile"] = req.cookies_file
    if req.proxy:
        opts["proxy"] = req.proxy

    with yt_dlp.YoutubeDL(opts) as ydl:
        extractor = ydl.get_info_extractor("TikTok")
        try:
            data, status = extractor._extract_web_data_and_status(page_url, post_id)
        except ExtractorError as exc:
            raise DownloadError(classify(str(exc)), str(exc)) from exc
        if status in (10216, 10222):
            raise DownloadError(ErrorKind.PRIVATE, f"status {status}")
        if status != 0 or not data:
            raise DownloadError(ErrorKind.NOT_FOUND, f"status {status}")

        images = (data.get("imagePost") or {}).get("images") or []
        if not images:
            raise DownloadError(ErrorKind.NO_MEDIA, "not a photo post")
        author = (data.get("author") or {}).get("uniqueId")
        info = PostInfo(url=req.url, author=f"@{author}" if author else None, text=data.get("desc") or None)
        if req.probe_only:
            return info, [], None

        files: list[Path] = []
        for index, image in enumerate(images[: req.max_items]):
            urls = (image.get("imageURL") or {}).get("urlList") or []
            target = Path(req.workdir) / f"tt_{post_id}_{index:02d}.jpg"
            if _download_first(ydl, urls, target):
                files.append(target)
        if not files:
            raise DownloadError(ErrorKind.NETWORK, "could not download slideshow images")

        music = data.get("music") or {}
        audio: Path | None = None
        if play_url := music.get("playUrl"):
            target = Path(req.workdir) / f"tt_{post_id}_sound.mp3"
            if _download_first(ydl, [play_url], target):
                audio = target
        return info, files, audio


def _download_first(ydl: yt_dlp.YoutubeDL, urls: list[str], target: Path) -> bool:
    for url in urls:
        try:
            with ydl.urlopen(url) as resp, target.open("wb") as out:
                while chunk := resp.read(1 << 16):
                    out.write(chunk)
            return True
        except Exception:  # noqa: BLE001 - try the next CDN mirror
            target.unlink(missing_ok=True)
    return False
