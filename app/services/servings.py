"""Дозаповнення порцій для страв, де вони не вказані, але є інгредієнти з кількостями.

Без кількості порцій Mini App не може перерахувати інгредієнти. Нові імпорти оцінюють
порції самі (промпт), а цей сервіс один раз проходить по вже збережених стравах.
"""

import json

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient
from app.ai.pricing import total_cost
from app.db.models import Dish, DishStatus

SYSTEM = """\
Тобі дають рецепти: id, назву й інгредієнти з кількостями. Для кожного оціни, на скільки \
порцій розрахована саме ця кількість інгредієнтів (звичайна порція дорослої людини). \
Для випічки, тортів, заготовок, соусів і тіста — кількість порцій або шматків, на яку це \
зазвичай ділять. Поверни servings — ціле число 1..24 — для кожного id."""


class ServingsItem(BaseModel):
    id: int
    servings: int


class ServingsGuess(BaseModel):
    items: list[ServingsItem]


def _prompt(dishes: list[Dish]) -> str:
    return json.dumps(
        [
            {
                "id": d.id,
                "title": d.title,
                "ingredients": [
                    " ".join(str(v) for v in (i.get("qty"), i.get("unit"), i.get("name")) if v)
                    for i in d.ingredients or []
                ],
            }
            for d in dishes
        ],
        ensure_ascii=False,
    )


async def fill_servings(
    session: AsyncSession, ai: AIClient, *, batch: int = 20, dry_run: bool = False
) -> tuple[dict[int, int], float]:
    """Повертає {dish_id: servings} і вартість AI. Порції з тексту рецепта не перезаписуються."""
    dishes = [
        d
        for d in await session.scalars(
            select(Dish)
            .where(Dish.servings.is_(None), Dish.status != DishStatus.archived)
            .order_by(Dish.id)
        )
        if any(isinstance(i, dict) and i.get("qty") for i in d.ingredients or [])
    ]
    result: dict[int, int] = {}
    cost = 0.0
    for start in range(0, len(dishes), batch):
        chunk = dishes[start : start + batch]
        guess, meta = await ai.structured(SYSTEM, _prompt(chunk), ServingsGuess)
        cost += total_cost(meta) or 0.0
        by_id = {d.id: d for d in chunk}
        for item in guess.items:
            if item.id in by_id and 1 <= item.servings <= 24:
                result[item.id] = item.servings
                if not dry_run:
                    by_id[item.id].servings = item.servings
    if not dry_run:
        await session.commit()
    return result, cost
