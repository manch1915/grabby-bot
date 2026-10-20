import logging
from pathlib import Path
from typing import Any

from gallery_dl import config, exception, job

from grabby.domain.errors import DownloadError, ErrorKind, classify
from grabby.domain.media import PostInfo
from grabby.fetch.protocol import FetchRequest


class _ErrorCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class _CollectingJob(job.DownloadJob):  # type: ignore[misc]
    def __init__(self, url: str, probe_only: bool) -> None:
        super().__init__(url)
        self.files: list[Path] = []
        self.meta: dict[str, Any] = {}
        self.probe_only = probe_only

    def handle_url(self, url: str, kwdict: dict[str, Any]) -> None:
        if not self.meta:
            self.meta = dict(kwdict)
        if self.probe_only:
            return
        super().handle_url(url, kwdict)
        path = Path(self.pathfmt.path)
        if path.exists() and path not in self.files:
            self.files.append(path)


def _configure(req: FetchRequest) -> None:
    config.clear()
    config.set((), "base-directory", req.workdir)
    config.set((), "directory", [])
    config.set((), "parent-directory", False)
    config.set(("extractor",), "filename", "{category}_{id|filename}_{num|filename}.{extension}")
    config.set(("extractor",), "image-range", f"1-{req.max_items}")
    config.set(("extractor",), "timeout", 30)
    config.set(("extractor",), "retries", 3)
    config.set(("extractor",), "skip", False)
    config.set(("extractor", "instagram"), "sleep-request", [1.0, 2.0])
    config.set(("extractor", "tiktok"), "audio", True)
    config.set(("extractor", "tiktok"), "videos", False)
    config.set(("downloader",), "part-directory", req.workdir)
    config.set(("output",), "mode", "null")
    if req.cookies_file:
        config.set(("extractor",), "cookies", req.cookies_file)
    if req.proxy:
        config.set(("extractor",), "proxy", req.proxy)
        config.set(("downloader",), "proxy", req.proxy)


def _author(meta: dict[str, Any]) -> str | None:
    for key in ("username", "author", "user", "owner", "uploader"):
        value = meta.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("nick") or value.get("username") or value.get("uniqueId")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _text(meta: dict[str, Any]) -> str | None:
    for key in ("description", "content", "desc", "title", "caption"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def fetch(req: FetchRequest) -> tuple[PostInfo, list[Path]]:
    _configure(req)
    collector = _ErrorCollector()
    root = logging.getLogger()
    root.addHandler(collector)
    try:
        try:
            runner = _CollectingJob(req.url, req.probe_only)
        except exception.NoExtractorError as exc:
            raise DownloadError(ErrorKind.UNSUPPORTED, str(exc)) from exc
        status = runner.run()
    finally:
        root.removeHandler(collector)

    info = PostInfo(url=req.url, author=_author(runner.meta), text=_text(runner.meta))
    if req.probe_only:
        if not runner.meta:
            raise _failure(collector.messages, status)
        return info, []
    if not runner.files:
        raise _failure(collector.messages, status)
    return info, runner.files


def _failure(messages: list[str], status: int) -> DownloadError:
    message = "; ".join(messages) or f"gallery-dl status {status}"
    kind = classify(message)
    if kind is ErrorKind.UNKNOWN and status & 16:
        kind = ErrorKind.LOGIN_REQUIRED
    if kind is ErrorKind.UNKNOWN and not messages:
        kind = ErrorKind.NO_MEDIA
    return DownloadError(kind, message)
