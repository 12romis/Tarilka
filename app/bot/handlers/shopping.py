"""Список покупок у боті: /list і повідомлення «купити молоко, хліб»."""

from html import escape

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.ai.client import AIClient
from app.config import get_settings
from app.db.models import Member
from app.services import shopping


def shopping_request(text: str) -> str | None:
    """Що купити — текст після тригера («купити молоко» → «молоко»); None — не про покупки."""
    return shopping.after_trigger(text, texts.SHOPPING_TRIGGERS)


def fmt_qty(q: float) -> str:
    whole, frac = int(q), round(q - int(q), 2)
    glyph = {0.25: "¼", 0.5: "½", 0.75: "¾"}.get(frac)
    if glyph:
        return (str(whole) if whole else "") + glyph
    return f"{q:g}".replace(".", ",")


def fmt_amount(line: shopping.Line) -> str:
    parts = [f"{fmt_qty(q)} {u}" if u else fmt_qty(q) for q, u in line.parts]
    return " + ".join(parts)


def names(lines: list[shopping.Line]) -> str:
    return escape(", ".join(line.name for line in lines))


def render_list(view: shopping.ShoppingView) -> str:
    open_lines = [line for line in view.lines if not line.checked]
    if not open_lines:
        return texts.SHOPPING_EMPTY
    out = [texts.SHOPPING_HEADER.format(n=view.open_count)]
    aisle = None
    for line in open_lines:
        if line.staple or line.at_home:
            continue
        if line.aisle != aisle:
            aisle = line.aisle
            emoji, label = shopping.AISLES[aisle]
            out.append(f"\n<b>{emoji} {label}</b>")
        amount = fmt_amount(line)
        out.append(f"• {escape(line.name)}" + (f" — {amount}" if amount else ""))
    if staples := [line for line in open_lines if line.staple and not line.at_home]:
        out.append("\n" + texts.SHOPPING_STAPLES.format(names=names(staples)))
    if at_home := [line for line in open_lines if line.at_home]:
        out.append("\n" + texts.SHOPPING_AT_HOME.format(names=names(at_home)))
    if view.dishes:
        titles = escape(", ".join(d.title for d in view.dishes))
        out.append("\n" + texts.SHOPPING_DISHES.format(titles=titles))
    return "\n".join(out)


def page_markup(private: bool, path: str, label: str) -> InlineKeyboardMarkup | None:
    """Кнопка сторінки Mini App; web_app-кнопки Telegram дозволяє лише в особистому чаті."""
    base = get_settings().public_base_url
    if not private or not base:
        return None
    button = InlineKeyboardButton(text=label, web_app=WebAppInfo(url=f"{base.rstrip('/')}{path}"))
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def list_markup(message: Message) -> InlineKeyboardMarkup | None:
    private = message.chat.type == ChatType.PRIVATE
    return page_markup(private, "/shopping", texts.BTN_OPEN_SHOPPING)


async def send_list(message: Message, session: AsyncSession, member: Member) -> None:
    view = await shopping.load_view(session, member.family_id)
    await message.answer(render_list(view), parse_mode="HTML", reply_markup=list_markup(message))


async def buy_reply(session: AsyncSession, member: Member, ai: AIClient | None, what: str) -> str:
    """Додає продукти з тексту в список; повертає відповідь для чату (HTML)."""
    if not what:
        return render_list(await shopping.load_view(session, member.family_id))
    before = {
        line.product_id for line in (await shopping.load_view(session, member.family_id)).lines
    }
    added = await shopping.add_text(session, ai, member, what)
    if not added:
        return texts.SHOPPING_NOTHING
    view = await shopping.load_view(session, member.family_id)
    names = [line.name for line in view.lines if line.product_id not in before] or [
        line.name for line in view.lines[-added:]
    ]
    return texts.SHOPPING_ADDED.format(names=escape(", ".join(names)), n=view.open_count)


async def cmd_list(message: Message, session: AsyncSession, member: Member) -> None:
    await send_list(message, session, member)


def build_router() -> Router:
    router = Router(name="shopping")
    router.message.register(cmd_list, Command("list"))
    return router
