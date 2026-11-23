"""Додавання страв одним повідомленням: текст або голосове → задача import_jobs."""

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.ai.client import AIClient
from app.bot.cards import render_job
from app.bot.handlers.shopping import add_from_text, shopping_request
from app.bot.handlers.what import send_what
from app.bot.util import safe_edit
from app.bot.views import existing_dish_message
from app.db.models import JobKind, Member
from app.importers.urls import clean_url, find_urls, strip_urls
from app.services import dishes as dish_svc
from app.services import drafts as dr
from app.services.jobs import enqueue
from app.services.meta import load_meta

MAX_TEXT = 3000


async def addressed_text(message: Message, bot: Bot) -> str | None:
    """Текст, адресований боту. У групах — лише зі згадкою @bot або у відповідь боту."""
    raw = message.text or message.caption or ""
    if message.chat.type == ChatType.PRIVATE:
        return raw
    me = await bot.me()
    mention = f"@{me.username}"
    replied = message.reply_to_message
    if mention.lower() in raw.lower():
        return raw.replace(mention, "").replace(mention.lower(), "").strip()
    if replied and replied.from_user and replied.from_user.id == me.id:
        return raw
    if message_urls(message):  # посилання в групі теж підхоплюємо
        return raw
    return None


MAX_LINKS = 5


def message_urls(message: Message) -> list[str]:
    """Посилання з тексту/підпису, зокрема приховані за словом (entity text_link)."""
    text = message.text or message.caption or ""
    urls: list[str] = []
    for e in message.entities or message.caption_entities or []:
        if e.type == "url":
            urls.append(e.extract_from(text))
        elif e.type == "text_link" and e.url:
            urls.append(e.url)
    urls += find_urls(text)
    return [u for u in dict.fromkeys(u.rstrip(".,;:!?)»") for u in urls) if u.startswith("http")]


def is_what_request(text: str) -> bool:
    t = text.lower().strip(" ?!.")
    return any(t.startswith(trigger) for trigger in texts.WHAT_TRIGGERS)


async def on_rename_reply(
    message: Message, bot: Bot, session: AsyncSession, member: Member
) -> None:
    replied = message.reply_to_message
    if not (replied and replied.from_user and replied.from_user.id == bot.id and message.text):
        raise SkipHandler
    job = await dr.find_rename_job(session, message.chat.id, replied.message_id)
    if job is None or job.family_id != member.family_id:
        raise SkipHandler
    i = job.result["rename"]["i"]
    title = message.text.strip()[:200]
    dr.drafts(job)[i]["title"] = title
    job.result["rename"] = None
    await dr.mark_existing(session, job)
    await session.commit()
    meta = await load_meta(session, job.family_id)
    text, markup = render_job(meta, job)
    if job.tg_chat_id and job.tg_message_id:
        await safe_edit(bot, job.tg_chat_id, job.tg_message_id, text, markup)
    await message.answer(texts.RENAMED.format(title=title))


async def on_text(
    message: Message, bot: Bot, session: AsyncSession, member: Member, ai: AIClient | None = None
) -> None:
    text = await addressed_text(message, bot)
    if not text:
        return
    if urls := message_urls(message):
        await enqueue_links(message, session, member, urls, strip_urls(text))
        return
    if (what := shopping_request(text)) is not None:
        await add_from_text(message, session, member, ai, what)
        return
    if is_what_request(text):
        await send_what(message, session, member)
        return
    if len(text) > MAX_TEXT:
        await message.answer(texts.TOO_LONG.format(n=MAX_TEXT))
        return
    reply = await message.answer(texts.PARSING)
    await enqueue(
        session,
        family_id=member.family_id,
        kind=JobKind.text,
        payload={"text": text},
        created_by=member.id,
        tg_chat_id=message.chat.id,
        tg_message_id=reply.message_id,
    )


async def enqueue_links(
    message: Message, session: AsyncSession, member: Member, urls: list[str], note: str
) -> None:
    """Кожне посилання — окрема задача й окрема картка. Відоме посилання — одразу страва."""
    for url in urls[:MAX_LINKS]:
        existing = await dish_svc.find_by_url(session, member.family_id, clean_url(url))
        if existing is not None:
            text, markup = await existing_dish_message(session, existing)
            await message.answer(text, reply_markup=markup)
            continue
        reply = await message.answer(texts.IMPORTING)
        await enqueue(
            session,
            family_id=member.family_id,
            kind=JobKind.url,
            payload={"url": url, "text": note[:MAX_TEXT]},
            created_by=member.id,
            tg_chat_id=message.chat.id,
            tg_message_id=reply.message_id,
        )


async def on_photo(message: Message, bot: Bot, session: AsyncSession, member: Member) -> None:
    if await addressed_text(message, bot) is None:
        return
    assert message.photo
    caption = message.caption or ""
    # приховані посилання (text_link) у підписі не видно як текст — дописуємо їх явно
    hidden = [u for u in message_urls(message) if u not in caption]
    if hidden:
        caption = (caption + "\n" + "\n".join(hidden)).strip()
    reply = await message.answer(texts.LOOKING_AT_PHOTO)
    await enqueue(
        session,
        family_id=member.family_id,
        kind=JobKind.photo,
        payload={"file_id": message.photo[-1].file_id, "caption": caption[:MAX_TEXT]},
        created_by=member.id,
        tg_chat_id=message.chat.id,
        tg_message_id=reply.message_id,
    )


async def on_voice(message: Message, bot: Bot, session: AsyncSession, member: Member) -> None:
    if message.chat.type != ChatType.PRIVATE and await addressed_text(message, bot) is None:
        return
    media = message.voice or message.audio
    assert media is not None
    reply = await message.answer(texts.TRANSCRIBING)
    await enqueue(
        session,
        family_id=member.family_id,
        kind=JobKind.voice,
        payload={"file_id": media.file_id, "duration": media.duration},
        created_by=member.id,
        tg_chat_id=message.chat.id,
        tg_message_id=reply.message_id,
    )


def build_router() -> Router:
    router = Router(name="add")
    router.message.register(on_rename_reply, F.reply_to_message, F.text)
    router.message.register(on_voice, F.voice | F.audio)
    router.message.register(on_photo, F.photo)
    router.message.register(on_text, F.text & ~F.text.startswith("/"))
    return router
