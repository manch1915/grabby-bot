from enum import StrEnum


class Platform(StrEnum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    YOUTUBE = "youtube"
    X = "x"
    FACEBOOK = "facebook"
    PINTEREST = "pinterest"
    REDDIT = "reddit"
    VK = "vk"
    THREADS = "threads"
    LIKEE = "likee"
    SNAPCHAT = "snapchat"
    TWITCH = "twitch"
    VIMEO = "vimeo"
    OTHER = "other"

    @property
    def label(self) -> str:
        return _TITLES[self]


_TITLES = {
    Platform.TIKTOK: "TikTok",
    Platform.INSTAGRAM: "Instagram",
    Platform.YOUTUBE: "YouTube",
    Platform.X: "X",
    Platform.FACEBOOK: "Facebook",
    Platform.PINTEREST: "Pinterest",
    Platform.REDDIT: "Reddit",
    Platform.VK: "VK",
    Platform.THREADS: "Threads",
    Platform.LIKEE: "Likee",
    Platform.SNAPCHAT: "Snapchat",
    Platform.TWITCH: "Twitch",
    Platform.VIMEO: "Vimeo",
    Platform.OTHER: "Other",
}

# Registrable domains; subdomains match too (m.tiktok.com, ru.pinterest.com).
_DOMAINS: dict[str, Platform] = {
    "tiktok.com": Platform.TIKTOK,
    "instagram.com": Platform.INSTAGRAM,
    "instagr.am": Platform.INSTAGRAM,
    "ddinstagram.com": Platform.INSTAGRAM,
    "youtube.com": Platform.YOUTUBE,
    "youtu.be": Platform.YOUTUBE,
    "youtube-nocookie.com": Platform.YOUTUBE,
    "x.com": Platform.X,
    "twitter.com": Platform.X,
    "fxtwitter.com": Platform.X,
    "vxtwitter.com": Platform.X,
    "fixupx.com": Platform.X,
    "fixvx.com": Platform.X,
    "t.co": Platform.X,
    "facebook.com": Platform.FACEBOOK,
    "fb.com": Platform.FACEBOOK,
    "fb.watch": Platform.FACEBOOK,
    "pin.it": Platform.PINTEREST,
    "reddit.com": Platform.REDDIT,
    "redd.it": Platform.REDDIT,
    "vk.com": Platform.VK,
    "vk.ru": Platform.VK,
    "vkvideo.ru": Platform.VK,
    "vk.cc": Platform.VK,
    "threads.net": Platform.THREADS,
    "threads.com": Platform.THREADS,
    "likee.video": Platform.LIKEE,
    "likee.com": Platform.LIKEE,
    "snapchat.com": Platform.SNAPCHAT,
    "twitch.tv": Platform.TWITCH,
    "vimeo.com": Platform.VIMEO,
}


def platform_for_host(host: str) -> Platform:
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if (platform := _DOMAINS.get(".".join(parts[i:]))) is not None:
            return platform
    # pinterest.com, pinterest.co.uk, ru.pinterest.com, pinterest.de ...
    if "pinterest" in parts:
        return Platform.PINTEREST
    return Platform.OTHER
