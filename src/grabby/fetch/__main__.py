"""Fetch subprocess: reads a FetchRequest as JSON on stdin, prints a FetchResult line."""

import json
import subprocess
import sys
import traceback

from grabby.domain.errors import DownloadError, ErrorKind, classify
from grabby.fetch.protocol import RESULT_MARKER, FetchRequest, FetchResult


def _versions() -> dict[str, str]:
    import gallery_dl.version
    import yt_dlp.version

    return {"yt-dlp": yt_dlp.version.__version__, "gallery-dl": gallery_dl.version.__version__}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    request = FetchRequest.from_json(json.loads(sys.stdin.buffer.read()))
    result = FetchResult()
    try:
        from grabby.fetch.runner import acquire

        result.versions = _versions()
        result.bundle = acquire(request)
    except DownloadError as exc:
        result.error_kind, result.error = exc.kind, exc.detail
    except subprocess.TimeoutExpired as exc:
        result.error_kind, result.error = ErrorKind.TIMEOUT, str(exc)
    except Exception as exc:  # noqa: BLE001 - everything must come back as a result
        traceback.print_exc(file=sys.stderr)
        result.error_kind, result.error = classify(str(exc)), f"{type(exc).__name__}: {exc}"
    sys.stdout.write(RESULT_MARKER + json.dumps(result.to_json(), ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
