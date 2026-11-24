"""Холодильник у боті: /fridge, «є вдома яйця, сир», «закінчилось молоко».

Тут же — розбір побутових повідомлень (покупки й холодильник) для тексту й голосових.
"""

from html import escape
from typing import Literal

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.ai.client import AIClient
from app.bot.handlers.shopping import buy_reply, page_markup, shopping_request
from app.db.models import Member
from app.services import pantry, shopping

COOK_IN_CHAT = 5

PantryAction = Literal["add", "remove"]


def pantry_request(text: str) -> tuple[PantryAction, str] | None:
    """«є вдома яйця» → ("add", "яйця"); «закінчилось молоко» → ("remove", "молоко")."""
    if (what := shopping.after_trigger(text, texts.PANTRY_ADD_TRIGGERS)) is not None:
        return "add", what
    if (what := shopping.after_trigger(text, texts.PANTRY_OUT_TRIGGERS)) is not None:
        return "remove", what
    return None


async def render_pantry(session: AsyncSession, ai: AIClient | None, member: Member) -> str:
    lines = await pantry.load(session, member.family_id)
    present = [line.product for line in lines if line.present]
    if not present:
        out = [texts.PANTRY_EMPTY]
    else:
        out = [texts.PANTRY_HEADER.format(n=len(present))]
    aisle = None
    for p in present:
        if p.aisle != aisle:
            aisle = p.aisle
            emoji, label = shopping.AISLES.get(aisle, shopping.AISLES["other"])
            out.append(f"\n<b>{emoji} {label}</b>")
        out.append(f"• {escape(p.name)}")
    if gone := [line for line in lines if not line.present]:
        names = ", ".join(escape(x.product.name) + (" 🛒" if x.in_list else "") for x in gone)
        out.append("\n" + texts.PANTRY_OUT.format(names=names))
    if not present:
        return "\n".join(out)
    options = await pantry.cook_options(session, ai, member.family_id, limit=COOK_IN_CHAT)
    out.append("")
    if not options:
        out.append(texts.PANTRY_COOK_NONE)
    else:
        out.append(texts.PANTRY_COOK_HEADER)
        for o in options:
            title = escape(o.title)
            out.append(
                texts.PANTRY_COOK_MISSING.format(title=title, missing=escape(", ".join(o.missing)))
                if o.missing
                else texts.PANTRY_COOK_READY.format(title=title)
            )
    return "\n".join(out)


async def pantry_reply(
    session: AsyncSession, member: Member, ai: AIClient | None, action: PantryAction, what: str
) -> str:
    if not what:
        return await render_pantry(session, ai, member)
    if action == "add":
        products = await pantry.add_text(session, ai, member, what)
        if not products:
            return texts.PANTRY_NOTHING
        return texts.PANTRY_ADDED.format(names=escape(", ".join(p.name for p in products)))
    removed = await pantry.out_text(session, ai, member, what)
    if not removed:
        return texts.PANTRY_NOT_THERE
    return texts.PANTRY_REMOVED.format(names=escape(", ".join(removed)))


async def household_reply(
    session: AsyncSession, member: Member, ai: AIClient | None, text: str, *, private: bool
) -> tuple[str, InlineKeyboardMarkup | None] | None:
    """Відповідь на «купити …», «є вдома …», «закінчилось …»; None — повідомлення не про це."""
    if (what := shopping_request(text)) is not None:
        reply = await buy_reply(session, member, ai, what)
        return reply, page_markup(private, "/shopping", texts.BTN_OPEN_SHOPPING)
    if (req := pantry_request(text)) is not None:
        reply = await pantry_reply(session, member, ai, *req)
        return reply, page_markup(private, "/fridge", texts.BTN_OPEN_PANTRY)
    return None


async def cmd_fridge(
    message: Message, session: AsyncSession, member: Member, ai: AIClient | None = None
) -> None:
    text = await render_pantry(session, ai, member)
    private = message.chat.type == ChatType.PRIVATE
    markup = page_markup(private, "/fridge", texts.BTN_OPEN_PANTRY)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


def build_router() -> Router:
    router = Router(name="pantry")
    router.message.register(cmd_fridge, Command("fridge"))
    return router
