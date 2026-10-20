import pytest

from grabby.domain.links import Rejection, check_supported, find_urls, normalize
from grabby.domain.platforms import Platform


@pytest.mark.parametrize(
    ("raw", "url", "platform"),
    [
        (
            "https://www.tiktok.com/@hankgreen1/video/7047596209028074758?is_from_webapp=1&sender_device=pc",
            "https://www.tiktok.com/@hankgreen1/video/7047596209028074758",
            Platform.TIKTOK,
        ),
        (
            "https://m.tiktok.com/@user/photo/7347358669992070446/?_r=1&_t=abc",
            "https://www.tiktok.com/@user/photo/7347358669992070446",
            Platform.TIKTOK,
        ),
        ("https://vm.tiktok.com/ZMabc123/", "https://vm.tiktok.com/ZMabc123", Platform.TIKTOK),
        (
            "https://www.instagram.com/reel/Chunk8-jurw/?igsh=MTc4MmM1YmI2Ng==",
            "https://www.instagram.com/reel/Chunk8-jurw/",
            Platform.INSTAGRAM,
        ),
        (
            "https://instagram.com/reels/Cop84x6u7CP",
            "https://www.instagram.com/reel/Cop84x6u7CP/",
            Platform.INSTAGRAM,
        ),
        (
            "https://www.instagram.com/someuser/p/BqvsDleB3lV/?img_index=2",
            "https://www.instagram.com/p/BqvsDleB3lV/",
            Platform.INSTAGRAM,
        ),
        (
            "https://youtu.be/jNQXAC9IVRw?si=xyz&t=10",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            Platform.YOUTUBE,
        ),
        (
            "https://m.youtube.com/watch?v=jNQXAC9IVRw&list=PL1&feature=share",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            Platform.YOUTUBE,
        ),
        (
            "https://youtube.com/shorts/abcDEF12345?si=1",
            "https://www.youtube.com/shorts/abcDEF12345",
            Platform.YOUTUBE,
        ),
        ("https://twitter.com/user/status/123?s=20&t=abc", "https://x.com/user/status/123", Platform.X),
        ("https://vxtwitter.com/user/status/123", "https://x.com/user/status/123", Platform.X),
        ("https://redd.it/90bu6w", "https://www.reddit.com/comments/90bu6w", Platform.REDDIT),
        (
            "https://ru.pinterest.com/pin/664281013778109217/",
            "https://ru.pinterest.com/pin/664281013778109217",
            Platform.PINTEREST,
        ),
        ("https://pin.it/4d5e1Ihnq", "https://pin.it/4d5e1Ihnq", Platform.PINTEREST),
        ("https://m.vk.com/video-1_2?utm_source=x", "https://vk.com/video-1_2", Platform.VK),
        ("https://vk.com/feed?z=video-1_2", "https://vk.com/feed?z=video-1_2", Platform.VK),
        (
            "https://www.threads.net/@zuck/post/ABC123",
            "https://www.threads.com/@zuck/post/ABC123",
            Platform.THREADS,
        ),
        ("https://clips.twitch.tv/SomeClip", "https://clips.twitch.tv/SomeClip", Platform.TWITCH),
        (
            "https://example.org/video.mp4?utm_source=tg&id=5",
            "https://example.org/video.mp4?id=5",
            Platform.OTHER,
        ),
        ("vm.tiktok.com/ZMabc123", "https://vm.tiktok.com/ZMabc123", Platform.TIKTOK),
    ],
)
def test_normalize(raw: str, url: str, platform: Platform) -> None:
    link = normalize(raw)
    assert link is not None
    assert link.url == url
    assert link.platform is platform


@pytest.mark.parametrize("raw", ["", "ftp://example.org/x", "https://localhost/x", "not a url"])
def test_normalize_rejects_garbage(raw: str) -> None:
    assert normalize(raw) is None


@pytest.mark.parametrize(
    ("raw", "short"),
    [
        ("https://vm.tiktok.com/ZMabc/", True),
        ("https://vt.tiktok.com/ZSabc", True),
        ("https://www.tiktok.com/t/ZTabc/", True),
        ("https://www.instagram.com/share/reel/BAabc/", True),
        ("https://www.reddit.com/r/videos/s/AbCdEf", True),
        ("https://www.facebook.com/share/v/1Abc/", True),
        ("https://t.co/KDovkM4ON8", True),
        ("https://pin.it/abc", True),
        ("https://www.tiktok.com/@user/video/1", False),
        ("https://youtu.be/jNQXAC9IVRw", False),
    ],
)
def test_short_links(raw: str, short: bool) -> None:
    link = normalize(raw)
    assert link is not None
    assert link.is_short is short


def test_find_urls_keeps_order_and_strips_punctuation() -> None:
    text = (
        "Look (https://www.tiktok.com/@a/video/1), then vm.tiktok.com/ZMx/ and\n"
        "https://youtu.be/jNQXAC9IVRw. Also «https://x.com/u/status/9»!"
    )
    assert find_urls(text) == [
        "https://www.tiktok.com/@a/video/1",
        "https://vm.tiktok.com/ZMx/",
        "https://youtu.be/jNQXAC9IVRw",
        "https://x.com/u/status/9",
    ]


def test_find_urls_does_not_double_count_scheme_links() -> None:
    assert find_urls("https://www.instagram.com/p/abc/") == ["https://www.instagram.com/p/abc/"]


def test_find_urls_in_a_txt_list() -> None:
    lines = "\n".join(f"https://www.tiktok.com/@u/video/{i}" for i in range(150))
    assert len(find_urls(lines)) == 150


@pytest.mark.parametrize(
    ("raw", "rejection"),
    [
        ("https://www.instagram.com/natgeo/", Rejection.PROFILE),
        ("https://www.tiktok.com/@scout2015", Rejection.PROFILE),
        ("https://www.youtube.com/@MrBeast", Rejection.PROFILE),
        ("https://www.youtube.com/playlist?list=PL123", Rejection.PLAYLIST),
        ("https://www.instagram.com/p/BqvsDleB3lV/", None),
        ("https://www.instagram.com/stories/user/3100/", None),
        ("https://www.tiktok.com/@u/photo/1", None),
        ("https://vm.tiktok.com/ZMabc/", None),
        ("https://www.youtube.com/watch?v=jNQXAC9IVRw", None),
        ("https://x.com/user", None),
    ],
)
def test_check_supported(raw: str, rejection: Rejection | None) -> None:
    link = normalize(raw)
    assert link is not None
    assert check_supported(link) is rejection
