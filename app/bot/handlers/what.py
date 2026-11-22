"""«Що приготувати?» у чаті, «Готую!», оцінка, /find і /cooked."""

from collections import OrderedDict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.bot.cards import dish_line
from app.bot.keyboards import (
    CookCb,
    RateCb,
    WhatCb,
    cook_buttons,
    rating_keyboard,
    reset_keyboard,
    what_keyboard,
)
from app.config import get_settings
from app.db.models import Member
from app.services import dishes as dish_svc
from app.services.meta import load_meta
from app.services.suggest import Filters, default_meal, suggest

EMPTY_DB_THRESHOLD = 10


class ShownCache:
    """Вже показані в цьому повідомленні страви (exclude_ids для «🎲 Ще»). Живе в пам'яті."""

    def __init__(self, limit: int = 500) -> None:
        self._data: OrderedDict[tuple[int, int], list[int]] = OrderedDict()
        self.limit = limit

    def get(self, chat_id: int, msg_id: int) -> list[int]:
        return list(self._data.get((chat_id, msg_id), []))

    def set(self, chat_id: int, msg_id: int, ids: list[int]) -> None:
        self._data[(chat_id, msg_id)] = ids
        self._data.move_to_end((chat_id, msg_id))
        while len(self._data) > self.limit:
            self._data.popitem(last=False)


shown = ShownCache()


async def build_what(
    session: AsyncSession, member: Member, meal: str, new: bool, exclude: list[int]
) -> tuple[str, InlineKeyboardMarkup, list[int]]:
    settings = get_settings()
    now = datetime.now(UTC)
    f = Filters(meal=[] if meal == "any" else [meal], include_new=new, exclude_ids=exclude)
    dishes, total = await suggest(session, member.family_id, f, settings, now)
    meta = await load_meta(session, member.family_id)
    label = dict(texts.WHAT_MEALS).get(meal, "") if meal != "any" else ""
    lines = [
        texts.WHAT_HEADER.format(meal=texts.WHAT_MEAL_SUFFIX.format(label=label) if label else "")
    ]
    infos = await dish_svc.load_info(session, meta, dishes)
    lines += [f"\n{n}. {dish_line(info, now)}" for n, info in enumerate(infos, 1)]
    if not dishes:
        lines.append("\n" + texts.WHAT_NONE)
    elif total < 3:
        lines.append("\n" + texts.WHAT_FEW.format(n=total))
    if await dish_svc.count_active(session, member.family_id) < EMPTY_DB_THRESHOLD:
        lines.append(texts.WHAT_EMPTY_DB)
    markup = what_keyboard(dishes, meal, new)
    if total < 3 and (meal != "any" or not new):
        markup.inline_keyboard.append(reset_keyboard().inline_keyboard[0])
    return "\n".join(lines), markup, [d.id for d in dishes]


async def send_what(message: Message, session: AsyncSession, member: Member) -> None:
    hour = datetime.now(ZoneInfo(get_settings().timezone)).hour
    meal = default_meal(hour)
    text, markup, ids = await build_what(session, member, meal, False, [])
    sent = await message.answer(text, reply_markup=markup)
    shown.set(sent.chat.id, sent.message_id, ids)


async def cmd_what(message: Message, session: AsyncSession, member: Member) -> None:
    await send_what(message, session, member)


async def on_what(
    cq: CallbackQuery, callback_data: WhatCb, session: AsyncSession, member: Member
) -> None:
    if cq.message is None:
        return
    key = (cq.message.chat.id, cq.message.message_id)
    exclude = shown.get(*key) if callback_data.a == "more" else []
    text, markup, ids = await build_what(
        session, member, callback_data.meal or "any", bool(callback_data.new), exclude
    )
    if callback_data.a == "more" and not ids and exclude:
        # Показали все — починаємо коло заново.
        exclude = []
        text, markup, ids = await build_what(
            session, member, callback_data.meal or "any", bool(callback_data.new), []
        )
    shown.set(*key, exclude + ids)
    await cq.message.edit_text(text, reply_markup=markup)
    await cq.answer()


async def on_cook(
    cq: CallbackQuery, callback_data: CookCb, bot: Bot, session: AsyncSession, member: Member
) -> None:
    dish = await dish_svc.get_family_dish(session, member.family_id, callback_data.dish)
    if dish is None or cq.message is None:
        await cq.answer(texts.DISH_NOT_FOUND, show_alert=True)
        return
    await dish_svc.mark_cooked(session, dish, member)
    await cq.answer(texts.COOKED_OK)
    await bot.send_message(
        cq.message.chat.id,
        texts.RATE_PROMPT.format(title=dish.title),
        reply_markup=rating_keyboard(dish.id),
    )


async def on_rate(
    cq: CallbackQuery, callback_data: RateCb, session: AsyncSession, member: Member
) -> None:
    dish = await dish_svc.get_family_dish(session, member.family_id, callback_data.dish)
    if dish is None or cq.message is None:
        await cq.answer(texts.DISH_NOT_FOUND, show_alert=True)
        return
    await dish_svc.set_rating(session, dish, callback_data.n)
    await cq.message.edit_text(texts.RATED.format(title=dish.title, stars="⭐" * dish.rating))
    await cq.answer()


async def _search_reply(
    message: Message, session: AsyncSession, member: Member, q: str, cooked: bool
) -> None:
    found = await dish_svc.search(session, member.family_id, q)
    if not found:
        await message.answer(texts.FIND_NOTHING.format(q=q))
        return
    now = datetime.now(UTC)
    meta = await load_meta(session, member.family_id)
    infos = await dish_svc.load_info(session, meta, found)
    head = texts.COOKED_PICK if cooked else texts.FIND_RESULTS.format(q=q)
    body = "\n".join(f"\n{n}. {dish_line(i, now)}" for n, i in enumerate(infos, 1))
    kb = cook_buttons(found, texts.BTN_COOKED if cooked else texts.BTN_COOK)
    await message.answer(head + "\n" + body, reply_markup=kb.as_markup())


async def cmd_find(
    message: Message, command: CommandObject, session: AsyncSession, member: Member
) -> None:
    if not (command.args or "").strip():
        await message.answer(texts.FIND_USAGE)
        return
    await _search_reply(message, session, member, command.args.strip(), cooked=False)


async def cmd_cooked(
    message: Message, command: CommandObject, session: AsyncSession, member: Member
) -> None:
    if not (command.args or "").strip():
        await message.answer(texts.COOKED_USAGE)
        return
    await _search_reply(message, session, member, command.args.strip(), cooked=True)


def build_router() -> Router:
    router = Router(name="what")
    router.message.register(cmd_what, Command("what"))
    router.message.register(cmd_find, Command("find"))
    router.message.register(cmd_cooked, Command("cooked"))
    router.callback_query.register(on_what, WhatCb.filter())
    router.callback_query.register(on_cook, CookCb.filter())
    router.callback_query.register(on_rate, RateCb.filter())
    return router
