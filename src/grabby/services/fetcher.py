import asyncio
import json
import logging
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path

from grabby.domain.errors import ErrorKind
from grabby.fetch.protocol import RESULT_MARKER, FetchRequest, FetchResult

log = logging.getLogger(__name__)


class Fetcher:
    """Runs each download in its own `python -m grabby.fetch` process.

    A process per job can be killed on /cancel or timeout, keeps yt-dlp's memory
    out of the bot, and picks up freshly installed extractor versions at once.
    """

    def __init__(self, *, timeout: float, extractor_path: Callable[[], Path | None]) -> None:
        self.timeout = timeout
        self._extractor_path = extractor_path
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()

    def _env(self, extractor_path: Path | None) -> dict[str, str]:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        paths = [str(extractor_path)] if extractor_path else []
        if existing := env.get("PYTHONPATH"):
            paths.append(existing)
        if paths:
            env["PYTHONPATH"] = os.pathsep.join(paths)
        return env

    async def fetch(
        self, key: str, request: FetchRequest, *, extractor_path: Path | None = None
    ) -> FetchResult:
        path = extractor_path if extractor_path is not None else self._extractor_path()
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "grabby.fetch",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(path),
            start_new_session=sys.platform != "win32",
        )
        self._procs[key] = proc
        payload = json.dumps(request.to_json()).encode()
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(payload), timeout=self.timeout)
        except TimeoutError:
            _kill(proc)
            await proc.wait()
            return FetchResult(error_kind=ErrorKind.TIMEOUT, error=f"no result in {self.timeout:.0f}s")
        finally:
            self._procs.pop(key, None)

        if key in self._cancelled:
            self._cancelled.discard(key)
            return FetchResult(error_kind=ErrorKind.CANCELLED)
        for line in reversed(stdout.decode("utf-8", "replace").splitlines()):
            if line.startswith(RESULT_MARKER):
                return FetchResult.from_json(json.loads(line[len(RESULT_MARKER) :]))
        tail = stderr.decode("utf-8", "replace")[-500:]
        log.warning("fetch process for %s exited with %s: %s", request.url, proc.returncode, tail)
        return FetchResult(error_kind=ErrorKind.UNKNOWN, error=f"fetcher exited with {proc.returncode}")

    def cancel(self, key: str) -> bool:
        proc = self._procs.get(key)
        if proc is None or proc.returncode is not None:
            return False
        self._cancelled.add(key)
        _kill(proc)
        return True


def _kill(proc: asyncio.subprocess.Process) -> None:
    # ffmpeg children live in the same session; take them down too.
    try:
        if sys.platform != "win32":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
