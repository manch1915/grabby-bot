import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from grabby.domain.platforms import Platform, platform_for_host

_URL_RE = re.compile(r"""https?://[^\s<>"'«»]+""", re.IGNORECASE)
# Links pasted without a scheme ("vm.tiktok.com/ZM...") are common in .txt lists.
_BARE_RE = re.compile(
    r"""(?<![\w./@-])(?:www\.|m\.|vm\.|vt\.)?"""
    r"""(?:tiktok\.com|instagram\.com|youtube\.com|youtu\.be|x\.com|twitter\.com|"""
    r"""facebook\.com|fb\.watch|pin\.it|pinterest\.com|reddit\.com|redd\.it|vk\.com|"""
    r"""vkvideo\.ru|threads\.net|threads\.com|likee\.video|vimeo\.com|clips\.twitch\.tv)"""
    r"""/[^\s<>"'«»]+""",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}'\"»"

SHORT_HOSTS = frozenset(
    {
        "vm.tiktok.com",
        "vt.tiktok.com",
        "t.co",
        "pin.it",
        "vk.cc",
        "l.likee.video",
        "bit.ly",
        "tinyurl.com",
        "goo.gl",
        "is.gd",
        "cutt.ly",
        "clck.ru",
    }
)

# Query params worth keeping, everything else is tracking noise.
_KEEP_PARAMS: dict[Platform, frozenset[str]] = {
    Platform.YOUTUBE: frozenset({"v"}),
    Platform.FACEBOOK: frozenset({"v", "story_fbid", "id", "fbid"}),
    Platform.VK: frozenset({"z", "w", "list"}),
    Platform.VIMEO: frozenset({"h"}),
}
_TRACKING_PREFIXES = ("utm_", "share_", "fb_", "ga_")
_TRACKING_PARAMS = frozenset(
    {
        "igsh",
        "igshid",
        "si",
        "feature",
        "fbclid",
        "gclid",
        "yclid",
        "_t",
        "_r",
        "ref",
        "ref_src",
        "ref_url",
        "s",
        "t",
        "mibextid",
        "rdid",
        "checksum",
        "sender_device",
        "is_from_webapp",
        "is_copy_url",
        "lang",
        "context",
        "src",
        "source",
        "ab_channel",
    }
)


class Rejection(StrEnum):
    PROFILE = "profile"
    PLAYLIST = "playlist"


@dataclass(frozen=True, slots=True)
class Link:
    url: str
    platform: Platform

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    @property
    def is_short(self) -> bool:
        host = self.host
        if host in SHORT_HOSTS:
            return True
        path = urlsplit(self.url).path
        if self.platform is Platform.TIKTOK:
            return path.startswith("/t/")
        if self.platform in (Platform.INSTAGRAM, Platform.FACEBOOK):
            return path.startswith("/share/")
        if self.platform is Platform.REDDIT:
            return "/s/" in path
        return False


def find_urls(text: str) -> list[str]:
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for match in _URL_RE.finditer(text):
        found.append((match.start(), match.group(0)))
        taken.append(match.span())
    for match in _BARE_RE.finditer(text):
        start = match.start()
        if any(s <= start < e for s, e in taken):
            continue
        found.append((start, "https://" + match.group(0)))
    found.sort()
    return [_strip_trailing(url) for _, url in found]


def _strip_trailing(url: str) -> str:
    while url and url[-1] in _TRAILING:
        # keep balanced parentheses, e.g. wikipedia-style paths
        if url[-1] == ")" and url.count("(") >= url.count(")"):
            break
        url = url[:-1]
    return url


def normalize(raw: str) -> Link | None:
    raw = raw.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if "." not in host:
        return None
    platform = platform_for_host(host)
    path = parts.path or "/"
    query = _clean_query(parts.query, platform)
    host, path, query = _canonical(platform, host, path, query)
    url = urlunsplit(("https", host, path, query, ""))
    return Link(url=url, platform=platform)


def _clean_query(query: str, platform: Platform) -> str:
    keep = _KEEP_PARAMS.get(platform)
    pairs = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        lowered = key.lower()
        if keep is not None:
            if lowered in keep:
                pairs.append((key, value))
            continue
        if platform is not Platform.OTHER and platform is not Platform.VK:
            continue
        if lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PREFIXES):
            continue
        pairs.append((key, value))
    return urlencode(pairs)


def _canonical(platform: Platform, host: str, path: str, query: str) -> tuple[str, str, str]:
    match platform:
        case Platform.YOUTUBE:
            if host == "youtu.be":
                video_id = path.strip("/").split("/")[0]
                return "www.youtube.com", "/watch", urlencode({"v": video_id})
            if host != "music.youtube.com":
                host = "www.youtube.com"
        case Platform.X:
            if host != "t.co":
                host = "x.com"
        case Platform.INSTAGRAM:
            host = "www.instagram.com"
            path = re.sub(r"^/reels/", "/reel/", path)
            path = re.sub(r"^/(?!share/)[\w.]+/(p|reel|tv)/", r"/\1/", path)
        case Platform.TIKTOK:
            if host not in SHORT_HOSTS:
                host = "www.tiktok.com"
        case Platform.REDDIT:
            if host == "redd.it":
                return "www.reddit.com", "/comments" + path.rstrip("/"), query
            if host not in ("v.redd.it", "i.redd.it"):
                host = "www.reddit.com"
        case Platform.FACEBOOK:
            if host not in ("fb.watch",):
                host = "www.facebook.com"
        case Platform.VK:
            if host in ("m.vk.com", "vk.ru", "m.vk.ru", "www.vk.com"):
                host = "vk.com"
        case Platform.THREADS:
            host = "www.threads.com"
        case _:
            pass
    if platform is not Platform.OTHER and len(path) > 1:
        path = path.rstrip("/") or "/"
        if platform in (Platform.INSTAGRAM,):
            path += "/"
    return host, path, query


_IG_POST = re.compile(r"^/(p|reel|tv|share|stories)/")
_TT_POST = re.compile(r"^/(@[^/]*/(video|photo)/\d+|t/|share/video/|embed/|v/)")
_YT_CHANNEL = re.compile(r"^/(@[^/]+|c/|channel/|user/)")


def check_supported(link: Link) -> Rejection | None:
    if link.is_short:
        return None
    path = urlsplit(link.url).path
    match link.platform:
        case Platform.INSTAGRAM if not _IG_POST.match(path):
            return Rejection.PROFILE
        case Platform.TIKTOK if not _TT_POST.match(path):
            return Rejection.PROFILE
        case Platform.YOUTUBE:
            if path.startswith("/playlist"):
                return Rejection.PLAYLIST
            if _YT_CHANNEL.match(path) and "/shorts/" not in path:
                return Rejection.PROFILE
    return None
