from pathlib import Path

import pytest

from grabby.domain.errors import ErrorKind
from grabby.domain.media import MediaBundle, PostInfo
from grabby.fetch.protocol import FetchRequest, FetchResult
from grabby.services.updater import ExtractorManager

PROBES = ["https://www.tiktok.com/@a/video/1", "https://www.instagram.com/reel/abc/"]


class ProbeFetcher:
    """Probe results depend on which extractor directory is on the path."""

    def __init__(self, results: dict[str | None, tuple[int, str]]) -> None:
        self.results = results

    async def fetch(
        self, key: str, request: FetchRequest, *, extractor_path: Path | None = None
    ) -> FetchResult:
        ok_count, version = self.results["new" if extractor_path else None]
        index = PROBES.index(request.url)
        versions = {"yt-dlp": version}
        if index < ok_count:
            return FetchResult(versions=versions, bundle=MediaBundle(info=PostInfo(url=request.url)))
        return FetchResult(versions=versions, error_kind=ErrorKind.NETWORK)


def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ExtractorManager:
    mgr = ExtractorManager(tmp_path / "ext", channel="nightly", work_dir=tmp_path / "work")

    async def fake_install(target: Path) -> None:
        target.mkdir(parents=True)  # noqa: ASYNC240

    monkeypatch.setattr(mgr, "_install", fake_install)
    return mgr


async def test_better_version_becomes_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = manager(tmp_path, monkeypatch)
    report = await mgr.check(ProbeFetcher({None: (1, "old"), "new": (2, "new")}), PROBES)  # type: ignore[arg-type]
    assert report.changed and report.versions == {"yt-dlp": "new"}
    assert mgr.current_path() is not None


async def test_worse_version_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = manager(tmp_path, monkeypatch)
    report = await mgr.check(ProbeFetcher({None: (2, "old"), "new": (1, "new")}), PROBES)  # type: ignore[arg-type]
    assert not report.changed
    assert "rejected" in report.note
    assert mgr.current_path() is None
    assert not list((tmp_path / "ext").glob("v*"))


async def test_same_version_is_discarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = manager(tmp_path, monkeypatch)
    report = await mgr.check(ProbeFetcher({None: (2, "same"), "new": (2, "same")}), PROBES)  # type: ignore[arg-type]
    assert not report.changed and not report.note


async def test_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = manager(tmp_path, monkeypatch)
    assert not mgr.rollback()
    await mgr.check(ProbeFetcher({None: (1, "a"), "new": (2, "b")}), PROBES)  # type: ignore[arg-type]
    assert mgr.rollback()
    assert mgr.current_path() is None
