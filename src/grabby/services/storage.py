import shutil
import time
from pathlib import Path

MB = 1024 * 1024


class WorkDir:
    """Temporary download area with a hard size budget. The server disk is nearly full,
    so new downloads wait until both the budget and the free space allow them."""

    def __init__(self, root: Path, *, limit_mb: int, min_free_mb: int) -> None:
        self.root = root
        self.limit = limit_mb * MB
        self.min_free = min_free_mb * MB
        # du over a big tree is not free; the scheduler asks often.
        self._cached_usage = 0
        self._cached_at = 0.0

    def reset(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for child in self.root.iterdir():
            _remove(child)
        self._cached_at = 0.0

    def job_dir(self, job_id: str) -> Path:
        path = self.root / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def release(self, job_id: str) -> None:
        _remove(self.root / job_id)
        self._cached_at = 0.0

    def usage(self) -> int:
        now = time.monotonic()
        if now - self._cached_at > 2:
            self._cached_usage = sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())
            self._cached_at = now
        return self._cached_usage

    def free(self) -> int:
        return shutil.disk_usage(self.root).free

    def has_room(self) -> bool:
        return self.usage() < self.limit and self.free() > self.min_free

    def sweep(self, older_than_s: float, keep: set[str]) -> int:
        """Remove leftovers of jobs that are no longer running."""
        removed = 0
        cutoff = time.time() - older_than_s
        for child in self.root.iterdir():
            if child.name in keep:
                continue
            if child.stat().st_mtime < cutoff:
                _remove(child)
                removed += 1
        if removed:
            self._cached_at = 0.0
        return removed


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
