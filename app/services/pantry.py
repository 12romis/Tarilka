"""Холодильник: що є вдома (лише наявність) і що з цього можна приготувати.

Продукти ті самі, що й у списку покупок (services/shopping): назви інгредієнтів страв
зводяться до них через кеш ingredient_products. «Зазвичай є вдома» (сіль, олія) вважаються
наявними, тож не заважають підбору страв.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient
from app.db.models import Dish, DishImage, DishStatus, Member, PantryItem, Product, ShoppingItem
from app.services import shopping
from app.services.shopping import AISLES, raw_key, uk_sort_key

MAX_MISSING = 3  # страви, яким бракує більше, «з наявного» вже не приготуєш


async def add(
    session: AsyncSession,
    member: Member,
    product_ids: list[int],
    *,
    at: datetime | None = None,
    commit: bool = True,
) -> None:
    """У холодильник; повторне додавання нічого не змінює. «Не купують» (вода) не кладемо."""
    ids = list(
        await session.scalars(
            select(Product.id).where(
                Product.family_id == member.family_id,
                Product.id.in_(product_ids),
                Product.skip.is_(False),
            )
        )
    )
    for pid in ids:
        await session.execute(
            insert(PantryItem)
            .values(
                family_id=member.family_id,
                product_id=pid,
                added_by=member.id,
                created_at=at or datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["family_id", "product_id"])
        )
    if commit:
        await session.commit()


async def remove(session: AsyncSession, family_id: int, product_ids: list[int]) -> list[int]:
    """Прибрати з холодильника; повертає id, які справді там були."""
    result = await session.execute(
        delete(PantryItem)
        .where(PantryItem.family_id == family_id, PantryItem.product_id.in_(product_ids))
        .returning(PantryItem.product_id)
    )
    removed = list(result.scalars())
    await session.commit()
    return removed


async def add_text(
    session: AsyncSession, ai: AIClient | None, member: Member, text: str
) -> list[Product]:
    """«молоко, яйця і сир» → у холодильник. Повертає розпізнані продукти."""
    products = [p for p, _ in await shopping.parse_products(session, ai, member.family_id, text)]
    await add(session, member, [p.id for p in products])
    return [p for p in products if not p.skip]


async def remove_text(
    session: AsyncSession, ai: AIClient | None, member: Member, text: str
) -> list[str]:
    """«закінчилось молоко» → прибрати. Повертає назви, які справді були в холодильнику."""
    products = [p for p, _ in await shopping.parse_products(session, ai, member.family_id, text)]
    removed = set(await remove(session, member.family_id, [p.id for p in products]))
    return [p.name for p in products if p.id in removed]


async def load(session: AsyncSession, family_id: int) -> list[Product]:
    """Вміст холодильника: за відділами магазину, далі за абеткою."""
    products = list(
        await session.scalars(
            select(Product)
            .join(PantryItem, PantryItem.product_id == Product.id)
            .where(PantryItem.family_id == family_id)
        )
    )
    order = list(AISLES)
    products.sort(
        key=lambda p: (order.index(p.aisle) if p.aisle in AISLES else 99, uk_sort_key(p.name))
    )
    return products


# --- Що приготувати з наявного -----------------------------------------------------------------


@dataclass
class CookOption:
    dish_id: int
    title: str
    status: str
    thumb: str | None
    have: list[str]
    missing: list[str]


async def _at_home(session: AsyncSession, family_id: int) -> set[int]:
    return set(
        await session.scalars(
            select(PantryItem.product_id).where(PantryItem.family_id == family_id)
        )
    )


def _names(dish: Dish) -> list[str]:
    return [i["name"] for i in dish.ingredients or [] if isinstance(i, dict) and i.get("name")]


@dataclass
class Coverage:
    dish: Dish
    have: list[str]
    missing: list[str]

    @property
    def ready(self) -> bool:
        """Усе є вдома. Страва лише з «зазвичай є» (тісто) сюди не потрапляє: нічого не звірено."""
        return bool(self.have) and not self.missing


async def coverage(session: AsyncSession, ai: AIClient | None, family_id: int) -> list[Coverage]:
    """Для кожної активної страви з інгредієнтами — що з потрібного є вдома, а чого бракує.

    Рахуються лише продукти, які треба купувати: «зазвичай є вдома» і «не купують» — ні.
    Нові назви інгредієнтів зводяться до продуктів тут же (AI, один раз — далі кеш).
    """
    at_home = await _at_home(session, family_id)
    if not at_home:
        return []
    dishes = list(
        await session.scalars(
            select(Dish)
            .where(
                Dish.family_id == family_id,
                Dish.status != DishStatus.archived,
                Dish.ingredients.is_not(None),
            )
            .order_by(Dish.id)
        )
    )
    names = {d.id: _names(d) for d in dishes}
    products, _ = await shopping.canonicalize(
        session, ai, family_id, [n for ns in names.values() for n in ns]
    )
    await session.commit()  # кеш відповідностей — навіть якщо далі щось впаде

    out: list[Coverage] = []
    for dish in dishes:
        needed = {
            p.id: p
            for p in (products[raw_key(n)] for n in names[dish.id] if raw_key(n))
            if not p.staple and not p.skip
        }
        have = [p.name for pid, p in needed.items() if pid in at_home]
        missing = [p.name for pid, p in needed.items() if pid not in at_home]
        out.append(Coverage(dish, have, missing))
    return out


async def ready_dish_ids(session: AsyncSession, ai: AIClient | None, family_id: int) -> set[int]:
    """Страви, для яких усе є вдома (фільтр «🧊 Усе є вдома» в підборі)."""
    return {c.dish.id for c in await coverage(session, ai, family_id) if c.ready}


async def cook_options(
    session: AsyncSession, ai: AIClient | None, family_id: int, limit: int = 30
) -> list[CookOption]:
    """Страви, для яких усе є або бракує до MAX_MISSING продуктів; спершу — найповніші."""
    options = [
        CookOption(c.dish.id, c.dish.title, c.dish.status.value, None, c.have, c.missing)
        for c in await coverage(session, ai, family_id)
        if c.have and len(c.missing) <= MAX_MISSING
    ]
    options.sort(
        key=lambda o: (
            len(o.missing),
            o.status != DishStatus.can_cook.value,  # спершу те, що вже вміємо
            -len(o.have),
            uk_sort_key(o.title),
        )
    )
    options = options[:limit]
    thumbs = await _thumbs(session, [o.dish_id for o in options])
    for o in options:
        o.thumb = thumbs.get(o.dish_id)
    return options


@dataclass
class IngredientStock:
    at_home: bool
    in_list: bool
    staple: bool  # «зазвичай є вдома» — наявність не відстежується


async def dish_stock(
    session: AsyncSession, ai: AIClient | None, dish: Dish
) -> list[IngredientStock | None]:
    """Для кожного рядка інгредієнтів страви (за індексом): чи є вдома і чи вже в списку.

    None — рядок без назви або продукт, який не купують (вода).
    """
    items = dish.ingredients or []
    raws = [i.get("name") if isinstance(i, dict) else None for i in items]
    products, _ = await shopping.canonicalize(session, ai, dish.family_id, [n for n in raws if n])
    await session.commit()
    at_home = await _at_home(session, dish.family_id)
    in_list = set(
        await session.scalars(
            select(ShoppingItem.product_id).where(
                ShoppingItem.family_id == dish.family_id, ShoppingItem.checked_at.is_(None)
            )
        )
    )
    out: list[IngredientStock | None] = []
    for raw in raws:
        p = products.get(raw_key(raw)) if raw and raw_key(raw) else None
        if p is None or p.skip:
            out.append(None)
        else:
            out.append(IngredientStock(p.id in at_home, p.id in in_list, p.staple))
    return out


async def _thumbs(session: AsyncSession, dish_ids: list[int]) -> dict[int, str]:
    rows = await session.execute(
        select(DishImage.dish_id, DishImage.path_thumb, DishImage.path)
        .where(DishImage.dish_id.in_(dish_ids))
        .order_by(DishImage.dish_id, DishImage.is_cover.desc(), DishImage.position)
    )
    out: dict[int, str] = {}
    for dish_id, thumb, path in rows.all():
        out.setdefault(dish_id, thumb or path)
    return out
