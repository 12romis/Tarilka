"""Кулінарні сайти: schema.org Recipe через recipe-scrapers, інакше чистий текст сторінки."""

import logging
from typing import Any

import httpx
import trafilatura
from recipe_scrapers import scrape_html

from app.db.models import SourceType
from app.importers.base import RawExtract
from app.services.media import BROWSER_UA

log = logging.getLogger("tarilka.import.web")


def _safe(fn: Any) -> Any:
    try:
        return fn()
    except Exception:
        return None


def extract_from_html(html: str, url: str, text_limit: int) -> RawExtract:
    """Чиста функція (без мережі) — тестується на HTML-фікстурах."""
    raw = RawExtract(url=url, source_type=SourceType.web, via="web")
    scraper = _safe(lambda: scrape_html(html, org_url=url, supported_only=False))
    if scraper is not None:
        ingredients = _safe(scraper.ingredients) or []
        steps = _safe(scraper.instructions_list) or []
        raw.title = _safe(scraper.title)
        image = _safe(scraper.image)
        if image:
            raw.image_urls.append(image)
        raw.source_author = _safe(scraper.author)
        if ingredients or steps:
            raw.structured = {
                "title": raw.title,
                "ingredients": ingredients,
                "steps": steps,
                "total_time_min": _safe(scraper.total_time),
                "yields": _safe(scraper.yields),
                "description": _safe(scraper.description),
            }
    meta = trafilatura.extract_metadata(html, default_url=url)
    if meta is not None:
        raw.title = raw.title or meta.title
        if meta.image and meta.image not in raw.image_urls:
            raw.image_urls.append(meta.image)
    if raw.structured is None:
        text = trafilatura.extract(html, url=url, include_comments=False) or ""
        if text:
            raw.text_blocks.append(text[:text_limit])
    return raw


async def fetch_web(client: httpx.AsyncClient, url: str, text_limit: int) -> RawExtract | None:
    try:
        r = await client.get(url, follow_redirects=True, headers={"User-Agent": BROWSER_UA})
    except httpx.HTTPError as e:
        log.info("сторінка недоступна %s: %r", url, e)
        return None
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        log.info("сторінка %s: HTTP %s", url, r.status_code)
        return None
    return extract_from_html(r.text, url, text_limit)
