"""Холодильник: що є вдома (лише наявність) і що з цього можна приготувати.

Продукти ті самі, що й у списку покупок (services/shopping): назви інгредієнтів страв
зводяться до них через кеш ingredient_products. «Зазвичай є вдома» (сіль, олія) вважаються
наявними, тож не заважають підбору страв.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
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
    """Позначити «є вдома»: новий продукт або той, що закінчився. «Не купують» (вода) — ні."""
    now = at or datetime.now(UTC)
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
        stmt = insert(PantryItem).values(
            family_id=member.family_id, product_id=pid, added_by=member.id, created_at=now
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["family_id", "product_id"],
                set_={"out_at": None, "restored_at": now},
                where=PantryItem.out_at.is_not(None),  # те, що вже є, не чіпаємо
            )
        )
    if commit:
        await session.commit()


async def undo_add(session: AsyncSession, family_id: int, product_id: int, since: datetime) -> None:
    """Скасувати `add` з моменту since (зняли відмітку «куплено»): новий — забути, закінчений —
    знову «закінчилось». Те, що вже було вдома раніше, не чіпаємо."""
    where = (PantryItem.family_id == family_id, PantryItem.product_id == product_id)
    await session.execute(delete(PantryItem).where(*where, PantryItem.created_at >= since))
    await session.execute(
        update(PantryItem)
        .where(*where, PantryItem.restored_at >= since)
        .values(out_at=datetime.now(UTC), restored_at=None)
    )


async def mark_out(session: AsyncSession, family_id: int, product_ids: list[int]) -> list[int]:
    """Закінчилось (лишається на сторінці зі статусом). Повертає id, що справді були вдома."""
    result = await session.execute(
        update(PantryItem)
        .where(
            PantryItem.family_id == family_id,
            PantryItem.product_id.in_(product_ids),
            PantryItem.out_at.is_(None),
        )
        .values(out_at=datetime.now(UTC))
        .returning(PantryItem.product_id)
    )
    changed = list(result.scalars())
    await session.commit()
    return changed


async def forget(session: AsyncSession, family_id: int, product_id: int) -> None:
    """Прибрати продукт зі сторінки зовсім."""
    await session.execute(
        delete(PantryItem).where(
            PantryItem.family_id == family_id, PantryItem.product_id == product_id
        )
    )
    await session.commit()


async def add_text(
    session: AsyncSession, ai: AIClient | None, member: Member, text: str
) -> list[Product]:
    """«молоко, яйця і сир» → «є вдома». Повертає розпізнані продукти."""
    products = [p for p, _ in await shopping.parse_products(session, ai, member.family_id, text)]
    await add(session, member, [p.id for p in products])
    return [p for p in products if not p.skip]


async def out_text(
    session: AsyncSession, ai: AIClient | None, member: Member, text: str
) -> list[str]:
    """«закінчилось молоко» → «закінчилось». Повертає назви, які справді були вдома."""
    products = [p for p, _ in await shopping.parse_products(session, ai, member.family_id, text)]
    changed = set(await mark_out(session, member.family_id, [p.id for p in products]))
    return [p.name for p in products if p.id in changed]


@dataclass
class PantryLine:
    product: Product
    present: bool
    in_list: bool  # некуплене в списку покупок


async def in_list_ids(session: AsyncSession, family_id: int) -> set[int]:
    return set(
        await session.scalars(
            select(ShoppingItem.product_id).where(
                ShoppingItem.family_id == family_id, ShoppingItem.checked_at.is_(None)
            )
        )
    )


async def load(session: AsyncSession, family_id: int) -> list[PantryLine]:
    """Усі продукти сторінки «Вдома»: за відділами магазину, далі за абеткою."""
    rows = (
        await session.execute(
            select(Product, PantryItem.out_at)
            .join(PantryItem, PantryItem.product_id == Product.id)
            .where(PantryItem.family_id == family_id)
        )
    ).all()
    listed = await in_list_ids(session, family_id)
    lines = [PantryLine(p, out_at is None, p.id in listed) for p, out_at in rows]
    order = list(AISLES)
    lines.sort(
        key=lambda x: (
            order.index(x.product.aisle) if x.product.aisle in AISLES else 99,
            uk_sort_key(x.product.name),
        )
    )
    return lines


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
            select(PantryItem.product_id).where(
                PantryItem.family_id == family_id, PantryItem.out_at.is_(None)
            )
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
    in_list = await in_list_ids(session, dish.family_id)
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
