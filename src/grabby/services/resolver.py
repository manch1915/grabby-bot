"""Expands short and share links before they reach the extractors.

Each site wants something different: TikTok short links redirect properly only for
link-preview bots, Instagram /share/ needs browser fetch headers, pin.it has its own
API endpoint, and t.co answers browsers with an HTML page instead of a redirect.
"""

import asyncio
import logging
import re
from urllib.parse import urljoin, urlsplit

import aiohttp

from grabby.domain.links import Link, normalize
from grabby.services.netguard import is_public_host

log = logging.getLogger(__name__)

PREVIEW_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
_META_REFRESH = re.compile(r"""<meta[^>]+url=['"]?([^'">\s]+)""", re.IGNORECASE)
_MAX_HOPS = 6


class LinkResolver:
    def __init__(self, session: aiohttp.ClientSession, *, concurrency: int = 8) -> None:
        self.session = session
        self._sem = asyncio.Semaphore(concurrency)

    async def resolve_all(self, links: list[Link]) -> list[Link]:
        return list(await asyncio.gather(*(self.resolve(link) for link in links)))

    async def resolve(self, link: Link) -> Link:
        if not link.is_short:
            return link
        async with self._sem:
            try:
                final = await self._expand(link)
            except (aiohttp.ClientError, TimeoutError) as exc:
                log.info("could not expand %s: %s", link.url, exc)
                return link
        resolved = normalize(final)
        # Dead short links bounce to the site's home page; keep the original so the
        # user sees "post not found" for the link they actually sent.
        if resolved is None or resolved.url == link.url or urlsplit(resolved.url).path in ("", "/"):
            return link
        # A share link may point at another short link (t.co -> vm.tiktok.com).
        return await self.resolve(resolved) if resolved.is_short else resolved

    async def _expand(self, link: Link) -> str:
        host = link.host
        path = urlsplit(link.url).path
        if host == "pin.it":
            code = path.strip("/").split("/")[0]
            url = f"https://api.pinterest.com/url_shortener/{code}/redirect/"
            return await self._follow(url, headers={"User-Agent": BROWSER_UA}, start=link.url)
        if host.endswith("tiktok.com"):
            return await self._follow(link.url, headers={"User-Agent": PREVIEW_UA}, method="HEAD")
        if host.endswith("instagram.com"):
            headers = {
                "User-Agent": BROWSER_UA,
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
            }
            return await self._follow(link.url, headers=headers)
        if host == "t.co":
            return await self._follow(link.url, headers={"User-Agent": "curl/8.10"})
        return await self._follow(link.url, headers={"User-Agent": BROWSER_UA})

    async def _follow(
        self, url: str, *, headers: dict[str, str], method: str = "GET", start: str | None = None
    ) -> str:
        current = url
        last_good = start or url
        timeout = aiohttp.ClientTimeout(total=15)
        for _ in range(_MAX_HOPS):
            if not await is_public_host(urlsplit(current).hostname or ""):
                log.warning("refusing to follow %s: not a public host", current)
                return last_good if last_good != current else url
            async with self.session.request(
                method, current, headers=headers, allow_redirects=False, timeout=timeout
            ) as resp:
                location: str | None = resp.headers.get("Location")
                if location:
                    current = urljoin(current, location)
                    last_good = current
                    if not _is_shortener(current):
                        return current
                    continue
                if resp.status == 200 and method == "GET":
                    body = await resp.content.read(64_000)
                    match = _META_REFRESH.search(body.decode("utf-8", "replace"))
                    if match:
                        current = urljoin(current, str(match.group(1)).replace("&amp;", "&"))
                        last_good = current
                        if not _is_shortener(current):
                            return current
                        continue
                return last_good
        return last_good


def _is_shortener(url: str) -> bool:
    link = normalize(url)
    return link is not None and link.is_short
