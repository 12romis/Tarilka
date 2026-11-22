"""Pydantic-схеми API. З OpenAPI генеруються TS-типи для Mini App (webapp/src/api-types.ts)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import CookLog, DishImage
from app.services.catalog import DishBundle

Status = Literal["can_cook", "want_to_try", "archived"]
SortKey = Literal["stale", "new", "rating", "title"]


def media_url(path: str | None) -> str | None:
    return f"/media/{path}" if path else None


class MemberOut(BaseModel):
    id: int
    name: str
    aliases: list[str]
    has_telegram: bool
    is_admin: bool


class TagOut(BaseModel):
    id: int
    kind: str
    slug: str
    label: str
    emoji: str | None
    count: int


class MetaOut(BaseModel):
    members: list[MemberOut]
    tags: dict[str, list[TagOut]]  # kind → теги в порядку sort
    total_dishes: int


class IngredientIO(BaseModel):
    name: str
    qty: float | None = None
    unit: str | None = None


class ImageOut(BaseModel):
    id: int
    url: str
    thumb_url: str | None
    is_cover: bool

    @classmethod
    def of(cls, img: DishImage) -> "ImageOut":
        return cls(
            id=img.id,
            url=media_url(img.path) or "",
            thumb_url=media_url(img.path_thumb),
            is_cover=img.is_cover,
        )


class CookLogOut(BaseModel):
    member_id: int | None
    cooked_at: datetime
    note: str | None

    @classmethod
    def of(cls, e: CookLog) -> "CookLogOut":
        return cls(member_id=e.member_id, cooked_at=e.cooked_at, note=e.note)


class DishCard(BaseModel):
    id: int
    title: str
    status: Status
    cook_time_min: int | None
    rating: int | None
    last_cooked_at: datetime | None
    cooked_count: int
    cover: ImageOut | None
    cook_ids: list[int]
    tag_ids: list[int]
    main_ingredient_id: int | None
    source_url: str | None
    source_type: str

    @classmethod
    def of(cls, b: DishBundle) -> "DishCard":
        d = b.dish
        cover = next((i for i in b.images if i.is_cover), b.images[0] if b.images else None)
        return cls(
            id=d.id,
            title=d.title,
            status=d.status.value,
            cook_time_min=d.cook_time_min,
            rating=d.rating,
            last_cooked_at=d.last_cooked_at,
            cooked_count=d.cooked_count,
            cover=ImageOut.of(cover) if cover else None,
            cook_ids=b.cook_ids,
            tag_ids=b.tag_ids,
            main_ingredient_id=d.main_ingredient_id,
            source_url=d.source_url,
            source_type=d.source_type.value,
        )


class DishFull(DishCard):
    description: str | None
    difficulty: int | None
    servings: int | None
    ingredients: list[IngredientIO]
    steps: list[str]
    notes: str | None
    source_author: str | None
    images: list[ImageOut]
    cook_log: list[CookLogOut]
    created_at: datetime

    @classmethod
    def of(cls, b: DishBundle) -> "DishFull":  # type: ignore[override]
        d = b.dish
        return cls(
            **DishCard.of(b).model_dump(),
            description=d.description,
            difficulty=d.difficulty,
            servings=d.servings,
            ingredients=[IngredientIO.model_validate(i) for i in d.ingredients or []],
            steps=list(d.steps or []),
            notes=d.notes,
            source_author=d.source_author,
            images=[ImageOut.of(i) for i in b.images],
            cook_log=[CookLogOut.of(e) for e in b.log],
            created_at=d.created_at,
        )


class DishPage(BaseModel):
    items: list[DishCard]
    total: int
    next_cursor: int | None


class DishIn(BaseModel):
    """Створення/редагування; у PATCH передаються лише змінені поля."""

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    status: Literal["can_cook", "want_to_try"] | None = None
    main_ingredient_id: int | None = None
    cook_time_min: int | None = Field(None, ge=1, le=24 * 60)
    difficulty: int | None = Field(None, ge=1, le=3)
    servings: int | None = Field(None, ge=1, le=100)
    ingredients: list[IngredientIO] | None = None
    steps: list[str] | None = None
    notes: str | None = None
    rating: int | None = Field(None, ge=1, le=5)
    source_url: str | None = None
    tag_ids: list[int] | None = None
    cook_ids: list[int] | None = None


class SuggestIn(BaseModel):
    meal: list[str] = []
    cook: list[int] = []
    quick: bool = False
    include_new: bool = False
    exclude_ids: list[int] = []


class SuggestOut(BaseModel):
    dishes: list[DishCard]
    total_candidates: int
    total_dishes: int


class CookedIn(BaseModel):
    note: str | None = None


class ImagePatch(BaseModel):
    is_cover: bool


class MemberIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    aliases: list[str] | None = None
