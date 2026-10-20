import pytest

from grabby.domain.captions import CAPTION_LIMIT, build_caption
from grabby.domain.errors import ErrorKind, classify
from grabby.domain.jobs import Job, UserSettings
from grabby.domain.media import (
    MediaBundle,
    MediaItem,
    MediaKind,
    PostInfo,
    Quality,
    VideoAction,
    chunk_album,
    photo_fits,
    plan_video,
)
from grabby.domain.platforms import Platform, platform_for_host


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("[TikTok] 123: Your IP address is blocked from accessing this post", ErrorKind.RATE_LIMITED),
        ("[Instagram] Chunk: Instagram sent an empty media response", ErrorKind.LOGIN_REQUIRED),
        ("[youtube] x: Sign in to confirm you’re not a bot. Use --cookies", ErrorKind.LOGIN_REQUIRED),
        ("HTTP redirect to login page (https://www.instagram.com/accounts/login/)", ErrorKind.LOGIN_REQUIRED),
        ("[vk] Access restricted", ErrorKind.PRIVATE),
        (
            "[Instagram] x: This content isn't available to everyone: It can't be seen by certain audiences",
            ErrorKind.LOGIN_REQUIRED,
        ),
        ("[youtube] abc: Private video. Sign in if you've been granted access", ErrorKind.PRIVATE),
        ("[twitter] 1: Video #1 is unavailable", ErrorKind.NOT_FOUND),
        ("HTTP Error 404: Not Found", ErrorKind.NOT_FOUND),
        ("HTTP Error 429: Too Many Requests", ErrorKind.RATE_LIMITED),
        ("Unsupported URL: https://example.org/", ErrorKind.UNSUPPORTED),
        ("There is no video in this post", ErrorKind.NO_MEDIA),
        ("[youtube] x: This live event will begin in 3 hours", ErrorKind.LIVE),
        (
            "Sign in to confirm your age. This video may be inappropriate for some users.",
            ErrorKind.AGE_RESTRICTED,
        ),
        ("The uploader has not made this video available in your country", ErrorKind.GEO_BLOCKED),
        ("Connection reset by peer", ErrorKind.NETWORK),
        ("[facebook] 3676516585958356: Cannot parse data", ErrorKind.UNKNOWN),
        ("Read timed out", ErrorKind.TIMEOUT),
    ],
)
def test_classify(message: str, kind: ErrorKind) -> None:
    assert classify(message) is kind


def test_retryable_kinds() -> None:
    assert ErrorKind.NETWORK.retryable
    assert not ErrorKind.NOT_FOUND.retryable


@pytest.mark.parametrize(
    ("container", "vcodec", "acodec", "duration", "action"),
    [
        ("mp4", "h264", "aac", 30, VideoAction.KEEP),
        ("mp4", "hevc", "aac", 30, VideoAction.KEEP),
        ("matroska,webm", "h264", "aac", 30, VideoAction.REMUX),
        ("mp4", "h264", None, 5, VideoAction.KEEP),
        ("matroska,webm", "vp9", "opus", 60, VideoAction.TRANSCODE),
        ("mp4", "av1", "aac", 3600, VideoAction.AS_DOCUMENT),
        ("mp4", "h264", "pcm_s16le", 30, VideoAction.TRANSCODE),
    ],
)
def test_plan_video(
    container: str, vcodec: str, acodec: str | None, duration: float, action: VideoAction
) -> None:
    assert plan_video(container, vcodec, acodec, duration) is action


def test_photo_limits() -> None:
    assert photo_fits(2_000_000, 2160, 2880)
    assert not photo_fits(11 * 1024 * 1024, 1000, 1000)
    assert not photo_fits(100_000, 300, 9000)  # 1:30 strip


def test_albums_skip_audio_and_split_by_ten() -> None:
    items = [MediaItem(f"{i}.jpg", MediaKind.PHOTO) for i in range(23)] + [
        MediaItem("a.mp3", MediaKind.AUDIO)
    ]
    albums = chunk_album(items)
    assert [len(a) for a in albums] == [10, 10, 3]


def test_caption_has_author_link_and_short_text() -> None:
    info = PostInfo(url="https://www.tiktok.com/@a/video/1", author="@a", text="word " * 400)
    caption = build_caption(info, footer="@grabby_bot")
    assert caption.startswith('<b>@a</b> · <a href="https://www.tiktok.com/@a/video/1">')
    assert caption.endswith("@grabby_bot")
    assert "…" in caption
    assert len(caption) < CAPTION_LIMIT


def test_caption_escapes_html() -> None:
    info = PostInfo(url="https://x.com/u/status/1?a=1&b=2", author="<script>", text="1 < 2 & 3")
    caption = build_caption(info)
    assert "&lt;script&gt;" in caption
    assert "1 &lt; 2 &amp; 3" in caption
    assert "a=1&amp;b=2" in caption


def test_caption_without_text() -> None:
    info = PostInfo(url="https://x.com/u/status/1", author="u", text="hello")
    assert "hello" not in build_caption(info, with_text=False)


def test_bundle_roundtrip() -> None:
    bundle = MediaBundle(
        info=PostInfo(url="u", author="a"),
        items=[
            MediaItem(
                "v.mp4", MediaKind.VIDEO, size=10, width=720, height=1280, duration=5, thumbnail="t.jpg"
            )
        ],
    )
    assert MediaBundle.from_dict(bundle.to_dict()) == bundle


def test_job_roundtrip_and_cache_key() -> None:
    job = Job(
        batch_id="b", user_id=1, chat_id=1, index=0, url="https://x.com/u/status/1", platform=Platform.X
    )
    assert Job.from_dict(job.to_dict()) == job
    doc = Job(**{**job.to_dict(), "as_document": True, "platform": Platform.X})
    eco = Job(**{**job.to_dict(), "quality": Quality.ECONOMY, "platform": Platform.X})
    assert len({job.cache_key, doc.cache_key, eco.cache_key}) == 3


def test_settings_from_partial_dict() -> None:
    prefs = UserSettings.from_dict({"lang": "en", "caption": False})
    assert prefs.lang == "en"
    assert not prefs.caption
    assert prefs.quality is Quality.MAX


@pytest.mark.parametrize(
    ("host", "platform"),
    [
        ("www.tiktok.com", Platform.TIKTOK),
        ("pinterest.co.uk", Platform.PINTEREST),
        ("v.redd.it", Platform.REDDIT),
        ("clips.twitch.tv", Platform.TWITCH),
        ("notyoutube.com", Platform.OTHER),
    ],
)
def test_platform_for_host(host: str, platform: Platform) -> None:
    assert platform_for_host(host) is platform
