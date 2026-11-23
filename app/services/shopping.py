"""Спільний список покупок сім'ї.

Рядок списку (ShoppingItem) — внесок однієї страви чи додане вручну; показ зводить рядки
по продукту й одиниці (кг → г, л → мл). Назви інгредієнтів з рецептів («зубці часнику»,
«Куряче яйце») AI один раз зводить до канонічного продукту з відділом магазину; відповідність
кешується в ingredient_products, тож повторні додавання не кличуть AI.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, AIError
from app.ai.pricing import total_cost
from app.db.models import Dish, IngredientProduct, Member, Product, ShoppingItem
from app.services.units import canonical_unit

log = logging.getLogger(__name__)

# slug → (емодзі, назва); порядок — як ходимо магазином
AISLES: dict[str, tuple[str, str]] = {
    "produce": ("🥬", "Овочі, фрукти, зелень"),
    "meat": ("🥩", "М'ясо, риба"),
    "dairy": ("🧀", "Молочне, яйця"),
    "bakery": ("🍞", "Хліб"),
    "grocery": ("🌾", "Бакалія"),
    "canned": ("🥫", "Консерви, соуси"),
    "spices": ("🧂", "Спеції"),
    "frozen": ("❄️", "Заморожене"),
    "drinks": ("🥤", "Напої"),
    "sweets": ("🍫", "Солодощі"),
    "other": ("🛒", "Інше"),
}
Aisle = Literal[
    "produce", "meat", "dairy", "bakery", "grocery", "canned", "spices", "frozen", "drinks",
    "sweets", "other",
]  # fmt: skip

_RULES = """\
- product — канонічна назва продукту для списку покупок: називний відмінок, нижній регістр, \
без обробки й нарізки («зубці часнику» → «часник», «цибулина» → «цибуля», «Куряче яйце» → \
«яйця», «листочки свіжого базиліку» → «базилік», «вершки 33%» → «вершки»). Різні продукти \
не зливай: «свинячий фарш» і «яловичий фарш», «помідори» і «помідори у власному соку».
- Якщо продукт уже є в списку відомих — бери саме ту назву.
- aisle — відділ магазину: produce (овочі, фрукти, зелень, гриби), meat (м'ясо, птиця, риба, \
ковбаси), dairy (молочне, сир, яйця, масло вершкове), bakery (хліб, лаваш, тортильї), \
grocery (крупи, борошно, макарони, цукор, олія, тісто), canned (консерви, соуси, майонез), \
spices (спеції, сіль, приправи), frozen, drinks, sweets, other.
- staple=true лише для того, що довго зберігається й майже завжди є вдома: сіль, перець \
мелений, цукор, борошно, олія, оцет, сода, розпушувач, сухі спеції. Те, що псується й \
купується регулярно (яйця, молоко, масло вершкове, вершки, сир, хліб, овочі), — staple=false.
- skip=true для того, що не купують: вода, кип'яток, лід."""

CANON_SYSTEM = (
    """\
Тобі дають пронумеровані назви інгредієнтів з рецептів. Для кожної поверни i (той самий \
номер), product, aisle, staple, skip.
"""
    + _RULES
)

TEXT_SYSTEM = (
    """\
Користувач диктує або пише, що треба купити («молоко 2 л, хліб і десяток яєць»). Поверни \
items — по одному на продукт: product, qty (число або null), unit (г, кг, мл, л, шт. або \
null), aisle, staple, skip. Кількість не вигадуй; «десяток» — 10 шт.
"""
    + _RULES
)


class Canon(BaseModel):
    product: str
    aisle: Aisle
    staple: bool
    skip: bool


class CanonItem(Canon):
    i: int


class CanonOut(BaseModel):
    items: list[CanonItem]


class TextItem(Canon):
    qty: float | None
    unit: str | None


class TextOut(BaseModel):
    items: list[TextItem] = Field(description="продукти з тексту")


def raw_key(name: str) -> str:
    return " ".join(name.lower().split())[:200]


def _known_block(names: list[str]) -> str:
    return "Відомі продукти: " + (", ".join(sorted(names)) if names else "(поки немає)")


async def _upsert_product(session: AsyncSession, family_id: int, c: Canon) -> Product:
    """Продукт за назвою; новий — з відділом від AI. Одночасні вставки не конфліктують."""
    name = raw_key(c.product)[:100]
    await session.execute(
        insert(Product)
        .values(family_id=family_id, name=name, aisle=c.aisle, staple=c.staple, skip=c.skip)
        .on_conflict_do_nothing(index_elements=["family_id", "name"])
    )
    product = await session.scalar(
        select(Product).where(Product.family_id == family_id, Product.name == name)
    )
    assert product is not None
    return product


async def _known_names(session: AsyncSession, family_id: int) -> list[str]:
    return list(await session.scalars(select(Product.name).where(Product.family_id == family_id)))


async def canonicalize(
    session: AsyncSession, ai: AIClient | None, family_id: int, names: list[str], batch: int = 80
) -> tuple[dict[str, Product], float]:
    """raw_key → Product для всіх назв; нові — через AI (без AI — продукт з тією ж назвою)."""
    keys = list(dict.fromkeys(k for k in (raw_key(n) for n in names) if k))
    rows = await session.execute(
        select(IngredientProduct.raw, Product)
        .join(Product, Product.id == IngredientProduct.product_id)
        .where(IngredientProduct.family_id == family_id, IngredientProduct.raw.in_(keys))
    )
    found: dict[str, Product] = {raw: p for raw, p in rows.all()}
    missing = [k for k in keys if k not in found]
    cost = 0.0
    for start in range(0, len(missing), batch):
        chunk = missing[start : start + batch]
        canon: dict[int, Canon] = {}
        if ai is not None:
            prompt = (
                _known_block(await _known_names(session, family_id))
                + "\n\n"
                + "\n".join(f"{i}. {k}" for i, k in enumerate(chunk))
            )
            try:
                out, meta = await ai.structured(CANON_SYSTEM, prompt, CanonOut)
                cost += total_cost(meta)
                canon = {it.i: it for it in out.items if 0 <= it.i < len(chunk)}
            except AIError:
                log.exception("canonicalize: AI недоступний, беру назви як є")
        for i, key in enumerate(chunk):
            c = canon.get(i)
            product = await _upsert_product(
                session, family_id, c or Canon(product=key, aisle="other", staple=False, skip=False)
            )
            found[key] = product
            if c is not None:  # без AI відповідність не кешуємо — наступного разу спробуємо ще
                await session.execute(
                    insert(IngredientProduct)
                    .values(family_id=family_id, raw=key, product_id=product.id)
                    .on_conflict_do_nothing()
                )
    return found, cost


def _servings_factor(dish: Dish, servings: int | None) -> float:
    if not servings or not dish.servings:
        return 1.0
    return servings / dish.servings


async def add_dish(
    session: AsyncSession,
    ai: AIClient | None,
    dish: Dish,
    member: Member,
    servings: int | None = None,
) -> int:
    """Інгредієнти страви (з перерахунком на `servings`) — у список. Повторно — замінює."""
    ingredients = [i for i in dish.ingredients or [] if isinstance(i, dict) and i.get("name")]
    products, _ = await canonicalize(session, ai, dish.family_id, [i["name"] for i in ingredients])
    await session.execute(delete(ShoppingItem).where(ShoppingItem.dish_id == dish.id))
    factor = _servings_factor(dish, servings)
    added = 0
    for ing in ingredients:
        product = products[raw_key(ing["name"])]
        if product.skip:
            continue
        qty = ing.get("qty")
        session.add(
            ShoppingItem(
                family_id=dish.family_id,
                product_id=product.id,
                qty=round(qty * factor, 3) if qty else None,
                unit=canonical_unit(ing.get("unit")),
                dish_id=dish.id,
                servings=servings or dish.servings,
                added_by=member.id,
            )
        )
        added += 1
    await session.commit()
    return added


async def add_text(session: AsyncSession, ai: AIClient | None, member: Member, text: str) -> int:
    """«молоко 2 л, хліб» → рядки списку. Без AI — кожен пункт через кому як є."""
    items: list[TextItem] = []
    if ai is not None:
        prompt = _known_block(await _known_names(session, member.family_id)) + "\n\n" + text
        try:
            out, meta = await ai.structured(TEXT_SYSTEM, prompt, TextOut)
            items = out.items
            log.info("shopping text: %d продуктів, $%.5f", len(items), total_cost(meta))
        except AIError:
            log.exception("add_text: AI недоступний, ділю за комами")
    if not items:
        items = [
            TextItem(product=p, qty=None, unit=None, aisle="other", staple=False, skip=False)
            for p in (s.strip() for s in text.replace("\n", ",").split(","))
            if p
        ]
    added = 0
    for it in items:
        product = await _upsert_product(session, member.family_id, it)
        if product.skip:
            continue
        qty = it.qty if it.qty and it.qty > 0 else None
        session.add(
            ShoppingItem(
                family_id=member.family_id,
                product_id=product.id,
                qty=qty,
                unit=canonical_unit(it.unit),
                added_by=member.id,
            )
        )
        added += 1
    await session.commit()
    return added


async def remove_dish(session: AsyncSession, family_id: int, dish_id: int) -> None:
    await session.execute(
        delete(ShoppingItem).where(
            ShoppingItem.family_id == family_id, ShoppingItem.dish_id == dish_id
        )
    )
    await session.commit()


async def set_checked(
    session: AsyncSession, family_id: int, product_id: int, checked: bool
) -> None:
    """Відмітка стосується продукту цілком — усіх його рядків (з усіх страв)."""
    await session.execute(
        update(ShoppingItem)
        .where(ShoppingItem.family_id == family_id, ShoppingItem.product_id == product_id)
        .values(checked_at=datetime.now(UTC) if checked else None)
    )
    await session.commit()


async def clear(session: AsyncSession, family_id: int, *, only_checked: bool) -> None:
    stmt = delete(ShoppingItem).where(ShoppingItem.family_id == family_id)
    if only_checked:
        stmt = stmt.where(ShoppingItem.checked_at.is_not(None))
    await session.execute(stmt)
    await session.commit()


_UK = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"


def uk_sort_key(s: str) -> list[int]:
    """За українською абеткою («і» після «и», а не після «я», як у Unicode)."""
    return [_UK.index(ch) if ch in _UK else 100 + ord(ch) for ch in s.lower()]


# --- Показ ------------------------------------------------------------------------------------

_BASE = {"кг": ("г", 1000.0), "л": ("мл", 1000.0)}


@dataclass
class Line:
    product_id: int
    name: str
    aisle: str
    staple: bool
    checked: bool
    parts: list[tuple[float, str | None]]  # [(кількість, одиниця)] після зведення
    no_qty: bool  # є рядки без кількості («сіль» — за смаком)
    sources: list[str]


@dataclass
class ListedDish:
    dish_id: int
    title: str
    servings: int | None


@dataclass
class ShoppingView:
    lines: list[Line] = field(default_factory=list)
    dishes: list[ListedDish] = field(default_factory=list)

    @property
    def open_count(self) -> int:
        return sum(1 for line in self.lines if not line.checked)


def sum_parts(
    items: list[tuple[float | None, str | None]],
) -> tuple[list[tuple[float, str | None]], bool]:
    """Сума за одиницями: кг→г, л→мл, без одиниці → шт.; великі г/мл назад у кг/л."""
    totals: dict[str | None, float] = {}
    no_qty = False
    for qty, unit in items:
        if not qty:
            no_qty = True
            continue
        unit = unit or "шт."
        base, k = _BASE.get(unit, (unit, 1.0))
        totals[base] = totals.get(base, 0.0) + qty * k
    parts: list[tuple[float, str | None]] = []
    for unit, qty in totals.items():
        if unit in ("г", "мл") and qty >= 1000:
            parts.append((round(qty / 1000, 2), "кг" if unit == "г" else "л"))
        else:
            parts.append((round(qty, 2), unit))
    return parts, no_qty


async def load_view(session: AsyncSession, family_id: int) -> ShoppingView:
    rows = (
        await session.execute(
            select(ShoppingItem, Product, Dish.title, Member.name)
            .join(Product, Product.id == ShoppingItem.product_id)
            .outerjoin(Dish, Dish.id == ShoppingItem.dish_id)
            .outerjoin(Member, Member.id == ShoppingItem.added_by)
            .where(ShoppingItem.family_id == family_id)
            .order_by(ShoppingItem.id)
        )
    ).all()
    by_product: dict[int, list] = {}
    dishes: dict[int, ListedDish] = {}
    for item, product, title, member_name in rows:
        by_product.setdefault(product.id, []).append((item, product, title, member_name))
        if item.dish_id and item.dish_id not in dishes:
            dishes[item.dish_id] = ListedDish(item.dish_id, title, item.servings)
    view = ShoppingView(dishes=list(dishes.values()))
    for group in by_product.values():
        product = group[0][1]
        open_items = [g for g in group if g[0].checked_at is None]
        counted = open_items or group  # куплене показуємо повною кількістю
        parts, no_qty = sum_parts([(g[0].qty, g[0].unit) for g in counted])
        sources = list(dict.fromkeys(g[2] or g[3] or "вручну" for g in counted))
        view.lines.append(
            Line(
                product_id=product.id,
                name=product.name,
                aisle=product.aisle if product.aisle in AISLES else "other",
                staple=product.staple,
                checked=not open_items,
                parts=parts,
                no_qty=no_qty,
                sources=sources,
            )
        )
    order = list(AISLES)
    view.lines.sort(key=lambda line: (order.index(line.aisle), uk_sort_key(line.name)))
    return view
