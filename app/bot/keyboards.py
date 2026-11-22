"""CallbackData (≤ 64 байти) і клавіатури бота."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import texts
from app.db.models import Dish


class DraftCb(CallbackData, prefix="d"):
    """draft:<job_id>:<action>[:<arg>]; i — індекс чернетки в задачі (-1 — зведена картка)."""

    job: int
    i: int
    a: str
    arg: str = ""


class CookCb(CallbackData, prefix="c"):
    dish: int


class RateCb(CallbackData, prefix="r"):
    dish: int
    n: int


class WhatCb(CallbackData, prefix="w"):
    a: str  # meal | new | more | reset
    meal: str = ""  # slug або "any"
    new: int = 0


def dish_buttons(dishes: list[Dish], label: str = texts.BTN_COOK) -> InlineKeyboardBuilder:
    """Рядок на страву: «Готую» і, якщо є джерело, «🔗» — відкрити рецепт чи відео."""
    kb = InlineKeyboardBuilder()
    for d in dishes:
        row = [
            InlineKeyboardButton(
                text=label.format(title=d.title[:40]), callback_data=CookCb(dish=d.id).pack()
            )
        ]
        if d.source_url:
            row.append(InlineKeyboardButton(text="🔗", url=d.source_url))
        kb.row(*row)
    return kb


def rating_keyboard(dish_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for n in range(1, 6):
        kb.button(text="⭐" * n if n <= 2 else f"{n}⭐", callback_data=RateCb(dish=dish_id, n=n))
    kb.adjust(5)
    return kb.as_markup()


def what_keyboard(dishes: list[Dish], meal: str, new: bool) -> InlineKeyboardMarkup:
    kb = dish_buttons(dishes)
    rows = [
        [
            InlineKeyboardButton(
                text=("• " if meal == slug else "") + label,
                callback_data=WhatCb(a="meal", meal=slug, new=int(new)).pack(),
            )
            for slug, label in texts.WHAT_MEALS
        ],
        [
            InlineKeyboardButton(
                text=texts.WHAT_NEW_ON if new else texts.WHAT_NEW_OFF,
                callback_data=WhatCb(a="new", meal=meal, new=int(not new)).pack(),
            ),
            InlineKeyboardButton(
                text=texts.WHAT_MORE, callback_data=WhatCb(a="more", meal=meal, new=int(new)).pack()
            ),
        ],
    ]
    for r in rows:
        kb.row(*r)
    return kb.as_markup()


def reset_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.WHAT_RESET, callback_data=WhatCb(a="reset", meal="any", new=1))
    return kb.as_markup()
