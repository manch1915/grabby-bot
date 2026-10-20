"""Keeps yt-dlp and gallery-dl fresh without rebuilding the image.

New versions go into data/extractors/<stamp> and are put first on the fetch
subprocess's PYTHONPATH. A version only becomes current after it handles the probe
links at least as well as the one in use; otherwise it is thrown away.
"""

import asyncio
import logging
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from grabby.domain.links import normalize
from grabby.fetch.protocol import FetchRequest
from grabby.services.fetcher import Fetcher

log = logging.getLogger(__name__)

PACKAGES = ("yt-dlp[default,curl-cffi]", "gallery-dl")
_KEEP_VERSIONS = 2


@dataclass(slots=True)
class UpdateReport:
    changed: bool
    versions: dict[str, str]
    previous: dict[str, str]
    probes_ok: int
    probes_total: int
    note: str = ""


class ExtractorManager:
    def __init__(self, root: Path, *, channel: str, work_dir: Path) -> None:
        self.root = root
        self.channel = channel
        self.work_dir = work_dir
        self._lock = asyncio.Lock()

    def current_path(self) -> Path | None:
        pointer = self.root / "current"
        try:
            name = pointer.read_text().strip()
        except OSError:
            return None
        path = self.root / name
        return path if name and path.is_dir() else None

    def _set_current(self, path: Path) -> None:
        tmp = self.root / "current.tmp"
        tmp.write_text(path.name)
        tmp.replace(self.root / "current")

    async def _install(self, target: Path) -> None:
        args = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                "--no-cache-dir", "--upgrade", "--target", str(target)]  # fmt: skip
        if self.channel == "nightly":
            args.append("--pre")
        proc = await asyncio.create_subprocess_exec(
            *args, *PACKAGES, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"pip failed: {err.decode('utf-8', 'replace')[-400:]}")

    async def _probe(
        self, fetcher: Fetcher, urls: list[str], path: Path | None
    ) -> tuple[int, dict[str, str]]:
        ok = 0
        versions: dict[str, str] = {}
        for url in urls:
            link = normalize(url)
            if link is None:
                continue
            workdir = self.work_dir / f"probe-{int(time.time() * 1000)}"
            workdir.mkdir(parents=True, exist_ok=True)
            request = FetchRequest(
                url=link.url, platform=link.platform.value, workdir=str(workdir), probe_only=True
            )
            try:
                result = await fetcher.fetch(f"probe:{url}", request, extractor_path=path)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
            versions = result.versions or versions
            if result.ok:
                ok += 1
            else:
                log.info("probe %s failed: %s %s", url, result.error_kind, result.error[:200])
        return ok, versions

    async def check(self, fetcher: Fetcher, probe_urls: list[str]) -> UpdateReport:
        async with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            current = self.current_path()
            current_ok, current_versions = await self._probe(fetcher, probe_urls, current)

            candidate = self.root / time.strftime("v%Y%m%d-%H%M%S")
            try:
                await self._install(candidate)
            except (RuntimeError, TimeoutError) as exc:
                shutil.rmtree(candidate, ignore_errors=True)
                return UpdateReport(
                    False, current_versions, current_versions, current_ok, len(probe_urls), str(exc)
                )

            new_ok, new_versions = await self._probe(fetcher, probe_urls, candidate)
            if new_versions == current_versions:
                shutil.rmtree(candidate, ignore_errors=True)
                return UpdateReport(False, current_versions, current_versions, current_ok, len(probe_urls))
            if new_ok < current_ok or new_ok == 0:
                shutil.rmtree(candidate, ignore_errors=True)
                note = f"rejected {new_versions}: {new_ok}/{len(probe_urls)} probes vs {current_ok}"
                log.warning("extractor update %s", note)
                return UpdateReport(
                    False, current_versions, current_versions, current_ok, len(probe_urls), note
                )

            self._set_current(candidate)
            self._prune(keep=candidate)
            log.info("extractors updated %s -> %s", current_versions, new_versions)
            return UpdateReport(True, new_versions, current_versions, new_ok, len(probe_urls))

    def rollback(self) -> bool:
        """Drop the current downloaded version and fall back to the previous one (or the image)."""
        current = self.current_path()
        if current is None:
            return False
        older = sorted(p for p in self.root.glob("v*") if p.is_dir() and p != current)
        if older:
            self._set_current(older[-1])
        else:
            (self.root / "current").unlink(missing_ok=True)
        shutil.rmtree(current, ignore_errors=True)
        return True

    def _prune(self, keep: Path) -> None:
        versions = sorted(p for p in self.root.glob("v*") if p.is_dir())
        for path in versions[:-_KEEP_VERSIONS]:
            if path != keep:
                shutil.rmtree(path, ignore_errors=True)
