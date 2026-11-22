"""Кнопки картки-чернетки: зберегти, хто вміє, теги, назва, статус, відкинути."""

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, ForceReply
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.bot import cards
from app.bot.keyboards import DraftCb
from app.bot.util import safe_edit
from app.db.models import DishStatus, ImportJob, JobStatus, Member
from app.services import dishes as dish_svc
from app.services import drafts as dr
from app.services.meta import load_meta


def _next_pending(job: ImportJob, after: int) -> int | str:
    pending = dr.pending_indexes(job)
    later = [p for p in pending if p > after]
    return (later or pending or ["summary"])[0]


async def on_draft(
    cq: CallbackQuery, callback_data: DraftCb, bot: Bot, session: AsyncSession, member: Member
) -> None:
    job = await session.get(ImportJob, callback_data.job, with_for_update=True)
    if job is None or job.family_id != member.family_id or cq.message is None:
        await cq.answer(texts.DRAFT_GONE, show_alert=True)
        return
    a, i, arg = callback_data.a, callback_data.i, callback_data.arg
    meta = await load_meta(session, job.family_id)
    chat_id, msg_id = cq.message.chat.id, cq.message.message_id

    if a == "asis" and job.status == JobStatus.failed:
        source = (job.payload or {}).get("text") or ""
        dish = await dish_svc.create_from_draft(
            session,
            meta,
            job.family_id,
            {
                "title": source.strip()[:100] or "Без назви",
                "status": DishStatus.can_cook.value,
                "cooks": [member.id],
            },
            created_by=member.id,
        )
        job.status, job.dish_id = JobStatus.done, dish.id
        await session.commit()
        await safe_edit(
            bot, chat_id, msg_id, texts.SAVED_DISHES.format(n=1, titles=dish.title), None
        )
        await cq.answer()
        return

    ds = dr.drafts(job)
    if job.status not in (JobStatus.draft_ready, JobStatus.done) or (i >= 0 and i >= len(ds)):
        await cq.answer(texts.DRAFT_GONE, show_alert=True)
        return
    if i >= 0 and ds[i]["state"] != dr.PENDING and a not in ("sum",):
        text, markup = cards.render_job(meta, job)
        await safe_edit(bot, chat_id, msg_id, text, markup)
        await cq.answer(texts.DRAFT_GONE)
        return

    card: cards.Card | None = None
    match a:
        case "save":
            await dr.save_one(session, meta, job, i)
            job.result["view"] = _next_pending(job, i)
            dr.touch(job)
        case "all":
            await dr.save_all(session, meta, job)
        case "del":
            await dr.discard(session, job, None if i < 0 else i)
            if i >= 0:
                job.result["view"] = _next_pending(job, i)
                dr.touch(job)
        case "one":
            job.result["view"] = _next_pending(job, -1)
            dr.touch(job)
        case "next":
            job.result["view"] = _next_pending(job, i)
            dr.touch(job)
        case "sum":
            job.result["view"] = "summary"
            dr.touch(job)
        case "card":
            card = cards.render_draft(meta, job, i)
        case "st":
            dr.toggle_status(ds[i])
            dr.touch(job)
            card = cards.render_draft(meta, job, i)
        case "cooks":
            card = cards.render_cooks_menu(meta, job, i)
        case "tc":
            if int(arg) in meta.member_by_id:
                dr.toggle_cook(ds[i], int(arg))
                dr.touch(job)
            card = cards.render_cooks_menu(meta, job, i)
        case "tags":
            card = cards.render_tag_kinds(meta, job, i)
        case "tk":
            card = cards.render_tag_toggles(meta, job, i, arg)
        case "tt":
            kind, _, slug = arg.partition(".")
            if meta.tag(kind, slug):
                dr.toggle_tag(ds[i], kind, slug)
                dr.touch(job)
            card = cards.render_tag_toggles(meta, job, i, kind)
        case "ren":
            prompt = await bot.send_message(
                chat_id,
                texts.RENAME_PROMPT.format(title=ds[i]["title"]),
                reply_markup=ForceReply(input_field_placeholder=texts.RENAME_PLACEHOLDER),
            )
            job.result["rename"] = {"msg_id": prompt.message_id, "i": i}
            dr.touch(job)
            await session.commit()
            await cq.answer()
            return
    await session.commit()
    text, markup = card or cards.render_job(meta, job)
    await safe_edit(bot, chat_id, msg_id, text, markup)
    await cq.answer()


def build_router() -> Router:
    router = Router(name="drafts")
    router.callback_query.register(on_draft, DraftCb.filter())
    return router
