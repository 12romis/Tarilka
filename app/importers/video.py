"""TikTok, Instagram, YouTube: лише метадані (підпис, автор, обкладинка), відео не завантажуємо.

Порядок: yt-dlp (з cookies, якщо файл є) → oEmbed платформи. Будь-яка невдача — None,
далі працює резерв (текст користувача, фото, «лише посилання»).
"""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

import httpx
import yt_dlp

from app.db.models import SourceType
from app.importers.base import RawExtract

log = logging.getLogger("tarilka.import.video")

OEMBED = {
    SourceType.tiktok: "https://www.tiktok.com/oembed",
    SourceType.youtube: "https://www.youtube.com/oembed",
}


def _cookies_copy(cookies_file: str, tmp_dir: Path) -> str | None:
    """yt-dlp перезаписує файл cookies — даємо йому копію, оригінал (ro) не чіпаємо."""
    src = Path(cookies_file)
    if not cookies_file or not src.is_file() or src.stat().st_size == 0:
        return None
    dst = tmp_dir / "cookies.txt"
    shutil.copyfile(src, dst)
    return str(dst)


def _ytdlp_info(url: str, cookiefile: str | None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 15,
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def raw_from_info(url: str, st: SourceType, info: dict[str, Any]) -> RawExtract:
    description = (info.get("description") or "").strip()
    title = (info.get("title") or "").strip()
    # TikTok/Instagram кладуть у title початок підпису або «Video by …» — це не назва страви.
    if title.lower().startswith("video by") or (description and description.startswith(title)):
        title = ""
    raw = RawExtract(url=url, source_type=st, via="ytdlp", title=title or None)
    if description:
        raw.text_blocks.append(description)
    if info.get("thumbnail"):
        raw.image_urls.append(info["thumbnail"])
    uploader = info.get("uploader_id") or info.get("uploader") or info.get("channel")
    if uploader:
        raw.source_author = uploader if uploader.startswith("@") else f"@{uploader}"
    return raw


async def _oembed(client: httpx.AsyncClient, url: str, st: SourceType) -> RawExtract | None:
    endpoint = OEMBED.get(st)
    if endpoint is None:
        return None
    try:
        r = await client.get(endpoint, params={"url": url, "format": "json"})
        data = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return None
    title = (data.get("title") or "").strip()
    if not title and not data.get("thumbnail_url"):
        return None
    raw = RawExtract(url=url, source_type=st, via="oembed")
    if title:
        raw.text_blocks.append(title)  # у TikTok тут підпис цілком
    if data.get("thumbnail_url"):
        raw.image_urls.append(data["thumbnail_url"])
    if data.get("author_name"):
        raw.source_author = data["author_name"]
    return raw


async def fetch_video_meta(
    client: httpx.AsyncClient, url: str, st: SourceType, cookies_file: str, tmp_dir: Path
) -> tuple[RawExtract | None, dict[str, Any]]:
    """Повертає метадані й діагностику (що спрацювало, чи були cookies)."""
    cookiefile = _cookies_copy(cookies_file, tmp_dir)
    diag: dict[str, Any] = {"cookies": bool(cookiefile), "ytdlp": yt_dlp.version.__version__}
    try:
        info = await asyncio.to_thread(_ytdlp_info, url, cookiefile)
        raw = raw_from_info(url, st, info)
        if raw.has_content or raw.image_urls:
            diag["via"] = "ytdlp"
            return raw, diag
    except Exception as e:  # yt-dlp кидає різне: DownloadError, ExtractorError…
        diag["ytdlp_error"] = str(e).splitlines()[0][:300]
        log.info(
            "yt-dlp не впорався з %s (cookies=%s): %s", url, bool(cookiefile), diag["ytdlp_error"]
        )
    raw = await _oembed(client, url, st)
    diag["via"] = "oembed" if raw else None
    return raw, diag
