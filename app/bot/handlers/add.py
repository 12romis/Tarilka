"""Додавання страв одним повідомленням: текст або голосове → задача import_jobs."""

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.bot.cards import render_job
from app.bot.handlers.what import send_what
from app.bot.util import safe_edit
from app.db.models import JobKind, Member
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
    return None


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
    dr.touch(job)
    await session.commit()
    meta = await load_meta(session, job.family_id)
    text, markup = render_job(meta, job)
    if job.tg_chat_id and job.tg_message_id:
        await safe_edit(bot, job.tg_chat_id, job.tg_message_id, text, markup)
    await message.answer(texts.RENAMED.format(title=title))


async def on_text(message: Message, bot: Bot, session: AsyncSession, member: Member) -> None:
    text = await addressed_text(message, bot)
    if not text:
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
    router.message.register(on_text, F.text & ~F.text.startswith("/"))
    return router
