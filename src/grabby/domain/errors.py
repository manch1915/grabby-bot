import re
from enum import StrEnum


class ErrorKind(StrEnum):
    NOT_FOUND = "not_found"
    PRIVATE = "private"
    LOGIN_REQUIRED = "login_required"
    AGE_RESTRICTED = "age_restricted"
    GEO_BLOCKED = "geo_blocked"
    UNSUPPORTED = "unsupported"
    NOT_A_POST = "not_a_post"
    NO_MEDIA = "no_media"
    TOO_LARGE = "too_large"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    TIMEOUT = "timeout"
    LIVE = "live"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def retryable(self) -> bool:
        return self in (ErrorKind.NETWORK, ErrorKind.RATE_LIMITED, ErrorKind.TIMEOUT)


class DownloadError(Exception):
    def __init__(self, kind: ErrorKind, detail: str = "") -> None:
        super().__init__(detail or kind.value)
        self.kind = kind
        self.detail = detail


# Order matters: the first match wins, more specific patterns go first.
_RULES: tuple[tuple[ErrorKind, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(pattern, re.IGNORECASE))
    for kind, pattern in (
        (ErrorKind.TOO_LARGE, r"file is larger than max|too large|max_filesize"),
        (ErrorKind.LIVE, r"is live|live stream|premieres in|will begin in"),
        (ErrorKind.AGE_RESTRICTED, r"age[- ]restricted|confirm your age|inappropriate for some users"),
        (ErrorKind.GEO_BLOCKED, r"available in your country|geo[- ]?restrict|blocked in your"),
        (
            ErrorKind.RATE_LIMITED,
            r"\b429\b|too many requests|rate[- ]limit|try again later|wait a few minutes|"
            r"ip address is blocked",
        ),
        (ErrorKind.PRIVATE, r"private|only available to|followers only|access restricted"),
        (
            ErrorKind.LOGIN_REQUIRED,
            r"login required|log in|sign in|cookies|requested content is not available|"
            r"empty media response|redirect to login|authentication|confirm you.re not a bot",
        ),
        (
            ErrorKind.NOT_FOUND,
            r"\b404\b|not found|removed|deleted|no longer available|does not exist|unavailable|"
            r"has been taken down|status 10204|post may have been",
        ),
        (ErrorKind.NO_MEDIA, r"no video|no media|there is no|nothing to download|no formats"),
        (ErrorKind.UNSUPPORTED, r"unsupported url|no suitable extractor|not supported"),
        (ErrorKind.TIMEOUT, r"timed? ?out"),
        (
            ErrorKind.NETWORK,
            r"connection|network|temporary failure|name resolution|ssl|reset by peer|"
            r"http error 5\d\d|unable to download|incompleteread|eof occurred",
        ),
    )
)


def classify(message: str) -> ErrorKind:
    for kind, pattern in _RULES:
        if pattern.search(message):
            return kind
    return ErrorKind.UNKNOWN
