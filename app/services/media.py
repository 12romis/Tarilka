"""Фото страв на диску: WebP 1200 px і прев'ю 400 px (Pillow). Шляхи — відносно MEDIA_DIR."""

import io
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps

log = logging.getLogger("tarilka.media")

FULL_PX, THUMB_PX = 1200, 400
SUBDIR = "dishes"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def _resized(img: Image.Image, px: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((px, px))
    return img


def save_image(data: bytes, media_dir: str, origin: str) -> dict[str, Any]:
    """Зберігає зображення у двох розмірах. Кидає ValueError, якщо це не картинка."""
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        img = img.convert("RGB")
    except Exception as e:
        raise ValueError("не зображення") from e
    name = uuid.uuid4().hex
    folder = Path(media_dir) / SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    _resized(img, FULL_PX).save(folder / f"{name}.webp", "WEBP", quality=82)
    _resized(img, THUMB_PX).save(folder / f"{name}_thumb.webp", "WEBP", quality=78)
    return {
        "path": f"{SUBDIR}/{name}.webp",
        "path_thumb": f"{SUBDIR}/{name}_thumb.webp",
        "origin": origin,
    }


def jpeg_for_telegram(media_dir: str, rel_path: str) -> bytes:
    """Telegram надійніше показує JPEG як фото, ніж WebP."""
    buf = io.BytesIO()
    Image.open(Path(media_dir) / rel_path).convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


def delete_images(media_dir: str, images: list[dict[str, Any]]) -> None:
    for img in images:
        for key in ("path", "path_thumb"):
            if img.get(key):
                (Path(media_dir) / img[key]).unlink(missing_ok=True)


async def download_image(client: httpx.AsyncClient, url: str, max_mb: int) -> bytes | None:
    """Завантажує картинку за URL; None, якщо недоступна, не картинка або завелика."""
    try:
        async with client.stream("GET", url, headers={"User-Agent": BROWSER_UA}) as r:
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                return None
            data = bytearray()
            async for chunk in r.aiter_bytes():
                data += chunk
                if len(data) > max_mb * 1024 * 1024:
                    return None
            return bytes(data)
    except httpx.HTTPError as e:
        log.info("картинку не завантажено %s: %r", url, e)
        return None
