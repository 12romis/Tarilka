"""Повідомлення зі списками страв, яким потрібна БД (кухарі, теги). Чисте форматування — cards."""

from datetime import UTC, datetime

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.bot.cards import dish_line
from app.bot.keyboards import dish_buttons
from app.db.models import Dish
from app.services import dishes as dish_svc
from app.services.meta import load_meta


async def numbered_lines(session: AsyncSession, family_id: int, dishes: list[Dish]) -> list[str]:
    """«1. 🍲 Борщ ⏎ 👤 Зірка · готували 3 дн. тому» для кожної страви."""
    meta = await load_meta(session, family_id)
    now = datetime.now(UTC)
    infos = await dish_svc.load_info(session, meta, dishes)
    return [f"\n{n}. {dish_line(info, now)}" for n, info in enumerate(infos, 1)]


async def existing_dish_message(
    session: AsyncSession, dish: Dish
) -> tuple[str, InlineKeyboardMarkup]:
    """Відповідь на посилання, яке вже є в каталозі: страва + «Готую» і «🔗»."""
    lines = await numbered_lines(session, dish.family_id, [dish])
    text = texts.ALREADY_HAVE_LINK + "\n\n" + lines[0].removeprefix("\n1. ")
    return text, dish_buttons([dish]).as_markup()
