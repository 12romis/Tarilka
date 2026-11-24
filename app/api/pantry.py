"""Холодильник для Mini App / PWA: що є вдома і що з цього приготувати."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.auth import CurrentMember
from app.api.meta import SessionDep
from app.api.schemas import media_url
from app.api.shopping import AIDep
from app.db.models import Product
from app.services import dishes as dish_svc
from app.services import pantry, shopping

router = APIRouter(prefix="/api/pantry", tags=["pantry"])


class PantryProductOut(BaseModel):
    product_id: int
    name: str


class PantryAisleOut(BaseModel):
    slug: str
    emoji: str
    label: str
    items: list[PantryProductOut]


class PantryOut(BaseModel):
    aisles: list[PantryAisleOut]
    count: int


class CookOptionOut(BaseModel):
    dish_id: int
    title: str
    status: str
    thumb: str | None
    have: list[str]
    missing: list[str]


class IngredientStockOut(BaseModel):
    at_home: bool
    in_list: bool
    staple: bool = Field(description="зазвичай є вдома — наявність не відстежується")


class PantryTextIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


async def _view(session, member) -> PantryOut:
    aisles: dict[str, PantryAisleOut] = {}
    products = await pantry.load(session, member.family_id)
    for p in products:
        slug = p.aisle if p.aisle in shopping.AISLES else "other"
        emoji, label = shopping.AISLES[slug]
        aisles.setdefault(slug, PantryAisleOut(slug=slug, emoji=emoji, label=label, items=[]))
        aisles[slug].items.append(PantryProductOut(product_id=p.id, name=p.name))
    return PantryOut(aisles=list(aisles.values()), count=len(products))


@router.get("", response_model=PantryOut)
async def get_pantry(member: CurrentMember, session: SessionDep) -> PantryOut:
    return await _view(session, member)


@router.post("/items", response_model=PantryOut)
async def add_items(
    body: PantryTextIn, member: CurrentMember, session: SessionDep, ai: AIDep
) -> PantryOut:
    await pantry.add_text(session, ai, member, body.text)
    return await _view(session, member)


@router.post("/products/{product_id}", response_model=PantryOut)
async def add_product(product_id: int, member: CurrentMember, session: SessionDep) -> PantryOut:
    found = await session.scalar(
        select(Product.id).where(Product.id == product_id, Product.family_id == member.family_id)
    )
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Продукт не знайдено")
    await pantry.add(session, member, [product_id])
    return await _view(session, member)


@router.delete("/products/{product_id}", response_model=PantryOut)
async def remove_product(
    product_id: int, member: CurrentMember, session: SessionDep, to_list: bool = False
) -> PantryOut:
    """Закінчилось; to_list=true — одразу в список покупок."""
    removed = await pantry.remove(session, member.family_id, [product_id])
    if removed and to_list:
        await shopping.add_product(session, member, product_id)
    return await _view(session, member)


@router.get("/cook", response_model=list[CookOptionOut])
async def cook(member: CurrentMember, session: SessionDep, ai: AIDep) -> list[CookOptionOut]:
    options = await pantry.cook_options(session, ai, member.family_id)
    return [
        CookOptionOut(
            dish_id=o.dish_id,
            title=o.title,
            status=o.status,
            thumb=media_url(o.thumb),
            have=o.have,
            missing=o.missing,
        )
        for o in options
    ]


@router.get("/dish/{dish_id}", response_model=list[IngredientStockOut | None])
async def dish_stock(
    dish_id: int, member: CurrentMember, session: SessionDep, ai: AIDep
) -> list[IngredientStockOut | None]:
    """Позначки біля інгредієнтів страви — за тими ж індексами, що й dish.ingredients."""
    dish = await dish_svc.get_family_dish(session, member.family_id, dish_id)
    if dish is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Страву не знайдено")
    return [
        IngredientStockOut(at_home=s.at_home, in_list=s.in_list, staple=s.staple) if s else None
        for s in await pantry.dish_stock(session, ai, dish)
    ]
