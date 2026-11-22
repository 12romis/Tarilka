"""Фото з Telegram: скріншот рецепта, сторінка книги або фото готової страви.

Якщо в підписі є посилання — спершу пробуємо його (link.py). Коли посилання «мовчить»
(наприклад, TikTok без cookies), назву й рецепт дає саме фото, а посилання прикріплюється.
Фото користувача завжди стає обкладинкою.
"""

import asyncio

from app.ai.prompts import photo_system_prompt
from app.ai.schemas import PhotoParse, clean_draft
from app.db.models import ImportJob, SourceType
from app.importers.base import ImportContext, ImportOutput
from app.importers.link import attach_source, import_link, link_only_draft
from app.importers.urls import find_urls, strip_urls
from app.services.media import save_image


async def _vision(ctx: ImportContext, job: ImportJob, photo: bytes, caption: str):
    system = photo_system_prompt(ctx.meta.prompt_members(), ctx.meta.prompt_tags())
    user = f"Підпис користувача: {caption or '—'}"
    parsed, meta = await ctx.ai.structured(system, user, PhotoParse, images=[photo])
    # Фото готової страви — її приготував автор повідомлення; для рецепта кухаря не вгадуємо.
    default_cook = job.created_by if parsed.kind == "dish" else None
    drafts = [
        clean_draft(
            d,
            slugs=ctx.meta.slugs(),
            member_ids=set(ctx.meta.member_by_id),
            default_cook=default_cook,
        )
        for d in parsed.dishes
        if d.title.strip()
    ]
    return parsed.kind, drafts, meta


class PhotoImporter:
    async def run(self, job: ImportJob, ctx: ImportContext) -> ImportOutput:
        path = ctx.tmp_dir / "photo.jpg"
        await ctx.bot.download(job.payload["file_id"], destination=path)
        photo = await asyncio.to_thread(path.read_bytes)
        caption = job.payload.get("caption") or ""
        urls = find_urls(caption)
        user_text = strip_urls(caption)
        meta: dict = {}

        link_draft = raw = None
        if urls:
            link_draft, raw, meta["link"] = await import_link(ctx, job, urls[0], user_text)

        if link_draft is not None and link_draft.get("has_recipe"):
            drafts = [link_draft]
        else:
            kind, drafts, meta["vision"] = await _vision(ctx, job, photo, user_text)
            meta["kind"] = kind
            if not drafts and link_draft is not None:
                drafts = [link_draft]
            elif drafts and link_draft is not None:
                # посилання дало лише картинку — віддаємо її фото-чернетці як другу
                drafts[0]["images"] = link_draft.get("images", [])
            if not drafts and raw is not None:
                drafts = [link_only_draft(raw.url, raw.source_type, raw.source_author)]

        if drafts:
            user_img = save_image(photo, ctx.settings.media_dir, "upload")
            drafts[0]["images"] = [user_img, *drafts[0].get("images", [])]
            if raw is not None:
                attach_source(drafts[0], raw)
        source_type = raw.source_type if raw is not None else SourceType.photo
        return ImportOutput(
            drafts=drafts, source_text=caption or "фото", source_type=source_type, meta=meta
        )
