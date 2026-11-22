"""Імпорт за посиланням: метадані → LLM-нормалізація → фото → чернетка.

Ланцюжок резервів (посилання не губиться ніколи):
1. відеоплатформа — yt-dlp/oEmbed; сайт — schema.org Recipe або текст сторінки;
2. нічого не дістали — коментар користувача (текст поруч із посиланням);
3. нічого немає — чернетка «лише посилання» (want_to_try) без виклику LLM.
Фото з повідомлення (photo-задача) підставляє PhotoImporter, якщо посилання «мовчить».
"""

import json
import logging
from typing import Any
from urllib.parse import urlsplit

from app import texts
from app.ai.prompts import link_system_prompt
from app.ai.schemas import TextParse, clean_draft
from app.db.models import ImportJob, SourceType
from app.importers.base import DuplicateUrl, ImportContext, ImportOutput, RawExtract
from app.importers.urls import VIDEO_TYPES, author_from_url, normalize_url, source_type_of
from app.importers.video import fetch_video_meta
from app.importers.web import fetch_web
from app.services import dishes as dish_svc
from app.services.media import download_image, save_image

log = logging.getLogger("tarilka.import.link")

PLATFORM_NAMES = {
    SourceType.tiktok: "TikTok",
    SourceType.instagram: "Instagram",
    SourceType.youtube: "YouTube",
}


def link_user_prompt(raw: RawExtract, user_text: str) -> str:
    parts = [f"Посилання: {raw.url}", f"Джерело: {PLATFORM_NAMES.get(raw.source_type, 'сайт')}"]
    if raw.source_author:
        parts.append(f"Автор: {raw.source_author}")
    parts.append(f"Коментар користувача: {user_text or '—'}")
    if raw.title:
        parts.append(f"Назва: {raw.title}")
    if raw.structured:
        parts.append(
            "Структурований рецепт (JSON):\n" + json.dumps(raw.structured, ensure_ascii=False)
        )
    for block in raw.text_blocks:
        parts.append("Текст:\n" + block)
    return "\n\n".join(parts)


def link_only_draft(url: str, st: SourceType, author: str | None) -> dict[str, Any]:
    """Остання лінія: назва з платформи й автора, щоб посилання не загубилося."""
    if st in PLATFORM_NAMES:
        title = texts.LINK_ONLY_VIDEO.format(
            platform=PLATFORM_NAMES[st], author=f" {author}" if author else ""
        )
    else:
        title = texts.LINK_ONLY_SITE.format(
            host=(urlsplit(url).hostname or url).removeprefix("www.")
        )
    return {
        "title": title,
        "status": "want_to_try",
        "cooks": [],
        "notes": texts.LINK_ONLY_NOTE,
        "has_recipe": False,
        "confidence": 0.0,
    }


async def save_remote_images(ctx: ImportContext, urls: list[str], limit: int = 1) -> list[dict]:
    images: list[dict] = []
    for url in urls:
        if len(images) >= limit:
            break
        data = await download_image(ctx.http, url, ctx.settings.max_image_mb)
        if data:
            try:
                images.append(save_image(data, ctx.settings.media_dir, "og_image"))
            except ValueError:
                continue
    return images


async def extract(ctx: ImportContext, url: str, meta: dict[str, Any]) -> RawExtract:
    st = source_type_of(url)
    raw: RawExtract | None
    if st in VIDEO_TYPES:
        raw, diag = await fetch_video_meta(
            ctx.http, url, st, ctx.settings.ytdlp_cookies_file, ctx.tmp_dir
        )
        meta["video"] = diag
    else:
        raw = await fetch_web(ctx.http, url, ctx.settings.page_text_limit)
    raw = raw or RawExtract(url=url, source_type=st, via="link")
    raw.source_author = raw.source_author or author_from_url(url)
    return raw


async def import_link(
    ctx: ImportContext, job: ImportJob, url: str, user_text: str
) -> tuple[dict[str, Any] | None, RawExtract, dict[str, Any]]:
    """Чернетка за посиланням або None, якщо даних немає (тоді вирішує викликач)."""
    url = await normalize_url(ctx.http, url)
    if existing := await dish_svc.find_by_url(ctx.session, job.family_id, url):
        raise DuplicateUrl(existing.id)
    meta: dict[str, Any] = {"url": url}
    raw = await extract(ctx, url, meta)
    meta["via"] = raw.via
    draft = None
    if raw.has_content or user_text:
        system = link_system_prompt(ctx.meta.prompt_members(), ctx.meta.prompt_tags())
        parsed, meta["llm"] = await ctx.ai.structured(
            system, link_user_prompt(raw, user_text), TextParse
        )
        if parsed.dishes and parsed.dishes[0].title.strip():
            draft = clean_draft(
                parsed.dishes[0],
                slugs=ctx.meta.slugs(),
                member_ids=set(ctx.meta.member_by_id),
                default_cook=None,
            )
    if draft is not None:
        # Кроки зі structured-рецепта сайту — дослівно: LLM схильна їх стискати.
        if raw.structured and raw.structured.get("steps"):
            draft["steps"] = raw.structured["steps"]
        draft["images"] = await save_remote_images(ctx, raw.image_urls)
    return draft, raw, meta


def attach_source(draft: dict[str, Any], raw: RawExtract) -> dict[str, Any]:
    draft["source_url"] = raw.url
    draft["source_author"] = raw.source_author
    draft["source_type"] = raw.source_type.value
    return draft


class UrlImporter:
    async def run(self, job: ImportJob, ctx: ImportContext) -> ImportOutput:
        user_text = job.payload.get("text") or ""
        draft, raw, meta = await import_link(ctx, job, job.payload["url"], user_text)
        if draft is None:
            draft = link_only_draft(raw.url, raw.source_type, raw.source_author)
            draft["images"] = await save_remote_images(ctx, raw.image_urls)
        return ImportOutput(
            drafts=[attach_source(draft, raw)],
            source_text=user_text or raw.url,
            source_type=raw.source_type,
            meta=meta,
        )
