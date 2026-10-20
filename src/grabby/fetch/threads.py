"""Threads posts. Neither yt-dlp nor gallery-dl supports Threads, but the server-rendered
page carries the post as JSON, the same shape Instagram's API uses."""

import json
import re
import shutil
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from grabby.domain.errors import DownloadError, ErrorKind
from grabby.domain.media import PostInfo
from grabby.fetch.protocol import FetchRequest

_CODE = re.compile(r"/post/([\w-]+)")
_BLOB = re.compile(r'<script type="application/json"[^>]*data-sjs[^>]*>(.*?)</script>', re.S)
_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def find_post(html: str, code: str) -> dict[str, Any] | None:
    for blob in _BLOB.findall(html):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for post in _walk(data, code):
            return post
    return None


def _walk(node: Any, code: str) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if node.get("code") == code and ("image_versions2" in node or "carousel_media" in node):
            yield node
        for value in node.values():
            yield from _walk(value, code)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, code)


def media_urls(post: dict[str, Any]) -> list[tuple[str, str]]:
    """(url, ext) pairs in post order; videos win over their cover images."""
    items = post.get("carousel_media") or [post]
    result: list[tuple[str, str]] = []
    for item in items:
        videos = item.get("video_versions") or []
        if videos and videos[0].get("url"):
            result.append((videos[0]["url"], "mp4"))
            continue
        candidates = (item.get("image_versions2") or {}).get("candidates") or []
        if candidates:
            best = max(candidates, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))
            result.append((best["url"], "jpg"))
    return result


def _open(url: str, proxy: str | None) -> Any:
    handlers: list[urllib.request.BaseHandler] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.8"})
    return opener.open(request, timeout=30)


def fetch(req: FetchRequest) -> tuple[PostInfo, list[Path]]:
    match = _CODE.search(req.url)
    if not match:
        raise DownloadError(ErrorKind.UNSUPPORTED, "not a Threads post link")
    code = match.group(1)
    try:
        with _open(req.url, req.proxy) as resp:
            html = resp.read().decode("utf-8", "replace")
    except OSError as exc:
        raise DownloadError(ErrorKind.NETWORK, str(exc)) from exc

    post = find_post(html, code)
    if post is None:
        raise DownloadError(ErrorKind.NOT_FOUND, "post not found on the page")
    username = (post.get("user") or {}).get("username")
    info = PostInfo(
        url=req.url,
        author=f"@{username}" if username else None,
        text=(post.get("caption") or {}).get("text") or None,
    )
    urls = media_urls(post)
    if not urls:
        raise DownloadError(ErrorKind.NO_MEDIA, "text-only post")
    if req.probe_only:
        return info, []

    files: list[Path] = []
    for index, (url, ext) in enumerate(urls[: req.max_items]):
        target = Path(req.workdir) / f"threads_{code}_{index:02d}.{ext}"
        try:
            with _open(url, req.proxy) as resp, target.open("wb") as out:
                shutil.copyfileobj(resp, out, 1 << 16)
        except OSError as exc:
            raise DownloadError(ErrorKind.NETWORK, str(exc)) from exc
        files.append(target)
    return info, files
