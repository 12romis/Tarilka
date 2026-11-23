"""Спільний список покупок для Mini App / PWA."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.ai.client import AIClient, default_ai
from app.api.auth import CurrentMember
from app.api.meta import SessionDep
from app.services import dishes as dish_svc
from app.services import shopping

router = APIRouter(prefix="/api/shopping", tags=["shopping"])
AIDep = Annotated[AIClient | None, Depends(default_ai)]


class AmountOut(BaseModel):
    qty: float
    unit: str | None


class LineOut(BaseModel):
    product_id: int
    name: str
    staple: bool
    checked: bool
    amounts: list[AmountOut]
    to_taste: bool = Field(description="є рядки без кількості")
    sources: list[str]


class AisleOut(BaseModel):
    slug: str
    emoji: str
    label: str
    lines: list[LineOut]


class ListedDishOut(BaseModel):
    dish_id: int
    title: str
    servings: int | None


class ShoppingOut(BaseModel):
    aisles: list[AisleOut]  # некуплене, без «зазвичай є вдома»
    staples: list[LineOut]  # некуплене, що зазвичай є вдома
    checked: list[LineOut]
    dishes: list[ListedDishOut]
    open_count: int


class AddDishIn(BaseModel):
    dish_id: int
    servings: int | None = Field(None, ge=1, le=100)


class AddTextIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class CheckIn(BaseModel):
    checked: bool


def _line(line: shopping.Line) -> LineOut:
    return LineOut(
        product_id=line.product_id,
        name=line.name,
        staple=line.staple,
        checked=line.checked,
        amounts=[AmountOut(qty=q, unit=u) for q, u in line.parts],
        to_taste=line.no_qty,
        sources=line.sources,
    )


def render(view: shopping.ShoppingView) -> ShoppingOut:
    aisles: dict[str, AisleOut] = {}
    staples, checked = [], []
    for line in view.lines:
        if line.checked:
            checked.append(_line(line))
        elif line.staple:
            staples.append(_line(line))
        else:
            emoji, label = shopping.AISLES[line.aisle]
            aisles.setdefault(
                line.aisle, AisleOut(slug=line.aisle, emoji=emoji, label=label, lines=[])
            )
            aisles[line.aisle].lines.append(_line(line))
    return ShoppingOut(
        aisles=list(aisles.values()),
        staples=staples,
        checked=checked,
        dishes=[
            ListedDishOut(dish_id=d.dish_id, title=d.title, servings=d.servings)
            for d in view.dishes
        ],
        open_count=view.open_count,
    )


async def _view(session, member) -> ShoppingOut:
    return render(await shopping.load_view(session, member.family_id))


@router.get("", response_model=ShoppingOut)
async def get_list(member: CurrentMember, session: SessionDep) -> ShoppingOut:
    return await _view(session, member)


@router.post("/dishes", response_model=ShoppingOut)
async def add_dish(
    body: AddDishIn, member: CurrentMember, session: SessionDep, ai: AIDep
) -> ShoppingOut:
    dish = await dish_svc.get_family_dish(session, member.family_id, body.dish_id)
    if dish is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Страву не знайдено")
    await shopping.add_dish(session, ai, dish, member, body.servings)
    return await _view(session, member)


@router.delete("/dishes/{dish_id}", response_model=ShoppingOut)
async def remove_dish(dish_id: int, member: CurrentMember, session: SessionDep) -> ShoppingOut:
    await shopping.remove_dish(session, member.family_id, dish_id)
    return await _view(session, member)


@router.post("/items", response_model=ShoppingOut)
async def add_items(
    body: AddTextIn, member: CurrentMember, session: SessionDep, ai: AIDep
) -> ShoppingOut:
    await shopping.add_text(session, ai, member, body.text)
    return await _view(session, member)


@router.patch("/products/{product_id}", response_model=ShoppingOut)
async def check(
    product_id: int, body: CheckIn, member: CurrentMember, session: SessionDep
) -> ShoppingOut:
    await shopping.set_checked(session, member.family_id, product_id, body.checked)
    return await _view(session, member)


@router.delete("", response_model=ShoppingOut)
async def clear(
    member: CurrentMember, session: SessionDep, only_checked: bool = True
) -> ShoppingOut:
    await shopping.clear(session, member.family_id, only_checked=only_checked)
    return await _view(session, member)
