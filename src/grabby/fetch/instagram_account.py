"""Tells a broken post from a broken account.

With a session attached, Instagram answers HTTP 400 both for posts it won't show and for
accounts it has put on a checkpoint, and yt-dlp only reports the status code. Asking the
media endpoint directly gives the reason, e.g. {"message": "checkpoint_required"}.
"""

import http.cookiejar
import json
import re
import urllib.error
import urllib.request

_SHORTCODE = re.compile(r"/(?:p|reel|reels|tv)/([\w-]+)")
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_APP_ID = "936619743392459"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
_BLOCKED = frozenset({"checkpoint_required", "challenge_required", "login_required", "feedback_required"})


def media_id(shortcode: str) -> int:
    value = 0
    for char in shortcode:
        value = value * 64 + _ALPHABET.index(char)
    return value


def blocked_reason(cookies_file: str, url: str, proxy: str | None = None) -> str | None:
    """The reason Instagram refuses this session, or None if the session looks fine."""
    match = _SHORTCODE.search(url)
    if not match:
        return None
    jar = http.cookiejar.MozillaCookieJar(cookies_file)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError):
        return "cookie file can't be read"
    csrf = next((c.value or "" for c in jar if c.name == "csrftoken"), "")
    handlers: list[urllib.request.BaseHandler] = [urllib.request.HTTPCookieProcessor(jar)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    request = urllib.request.Request(
        f"https://www.instagram.com/api/v1/media/{media_id(match.group(1))}/info/",
        headers={"User-Agent": _UA, "X-IG-App-ID": _APP_ID, "X-CSRFToken": csrf},
    )
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=20):
            return None
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
        except ValueError:
            return None
        message = str(body.get("message", ""))
        if message in _BLOCKED or body.get("lock"):
            where = str(body.get("checkpoint_url", "")).rsplit("instagram.com", 1)[-1]
            return f"{message} {where}".strip()
        return None
    except OSError:
        return None
