from html import escape

from grabby.domain.media import PostInfo

CAPTION_LIMIT = 1024
TEXT_PREVIEW = 300


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:-") + "…"


def build_caption(
    info: PostInfo,
    *,
    with_text: bool = True,
    footer: str | None = None,
) -> str:
    """HTML caption: author, a short piece of the post text, source link, optional footer."""
    lines: list[str] = []
    head = info.author or ""
    link = f'<a href="{escape(info.url, quote=True)}">{escape(_short_url(info.url))}</a>'
    if head:
        lines.append(f"<b>{escape(head)}</b> · {link}")
    else:
        lines.append(link)

    body = info.text or info.title
    if with_text and body and body != info.author:
        reserve = sum(len(line) for line in lines) + len(footer or "") + 16
        lines.append(escape(_shorten(body, min(TEXT_PREVIEW, CAPTION_LIMIT - reserve))))

    if footer:
        lines.append(footer)
    return "\n".join(lines)


def _short_url(url: str) -> str:
    bare = url.split("://", 1)[-1].removeprefix("www.")
    return bare if len(bare) <= 48 else bare[:47] + "…"
