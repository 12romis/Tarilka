"""Каталог для Mini App: список із фільтрами, повна страва, редагування, фото.

Фільтри: усередині групи — OR, між групами — AND. Теги в запитах — slug (зручно для URL).
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CookLog,
    Dish,
    DishCook,
    DishImage,
    DishStatus,
    DishTag,
    ImageOrigin,
    Tag,
)
from app.services.media import delete_images, save_image

SORTS = ("stale", "new", "rating", "title")


@dataclass
class ListFilters:
    q: str = ""
    status: list[str] = field(default_factory=lambda: ["can_cook", "want_to_try"])
    meal: list[str] = field(default_factory=list)
    course: list[str] = field(default_factory=list)
    ingredient: list[str] = field(default_factory=list)
    trait: list[str] = field(default_factory=list)
    cuisine: list[str] = field(default_factory=list)
    cook: list[int] = field(default_factory=list)
    max_time: int | None = None
    sort: str = "stale"


def _tag_filter(kind: str, slugs: list[str]):
    return exists().where(
        DishTag.dish_id == Dish.id, DishTag.tag_id == Tag.id, Tag.kind == kind, Tag.slug.in_(slugs)
    )


def build_query(family_id: int, f: ListFilters) -> Select:
    stmt = select(Dish).where(Dish.family_id == family_id)
    statuses = [s for s in f.status if s in DishStatus.__members__] or ["can_cook", "want_to_try"]
    stmt = stmt.where(Dish.status.in_([DishStatus(s) for s in statuses]))
    if q := f.q.strip():
        stmt = stmt.where(
            or_(Dish.title.ilike(f"%{q}%"), func.word_similarity(q, Dish.title) > 0.4)
        )
    for kind in ("meal", "course", "ingredient", "trait", "cuisine"):
        if slugs := getattr(f, kind):
            stmt = stmt.where(_tag_filter(kind, slugs))
    if f.cook:
        stmt = stmt.where(
            exists().where(DishCook.dish_id == Dish.id, DishCook.member_id.in_(f.cook))
        )
    if f.max_time:
        stmt = stmt.where(Dish.cook_time_min <= f.max_time)
    order = {
        # «давно не готували»: ніколи не готовані — першими, далі найдавніші
        "stale": [Dish.last_cooked_at.asc().nulls_first(), Dish.id],
        "new": [Dish.created_at.desc(), Dish.id.desc()],
        "rating": [Dish.rating.desc().nulls_last(), Dish.title],
        "title": [Dish.title, Dish.id],
    }[f.sort if f.sort in SORTS else "stale"]
    return stmt.order_by(*order)


async def list_dishes(
    session: AsyncSession, family_id: int, f: ListFilters, limit: int, offset: int
) -> tuple[list[Dish], int]:
    stmt = build_query(family_id, f)
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    dishes = list(await session.scalars(stmt.limit(limit).offset(offset)))
    return dishes, total or 0


@dataclass
class DishBundle:
    """Страва з усім, що треба для картки й сторінки."""

    dish: Dish
    cook_ids: list[int]
    tag_ids: list[int]
    images: list[DishImage]
    log: list[CookLog] = field(default_factory=list)


async def bundles(
    session: AsyncSession, dishes: list[Dish], with_log: bool = False
) -> list[DishBundle]:
    ids = [d.id for d in dishes]
    if not ids:
        return []
    cooks: dict[int, list[int]] = {i: [] for i in ids}
    for dish_id, member_id in await session.execute(
        select(DishCook.dish_id, DishCook.member_id).where(DishCook.dish_id.in_(ids))
    ):
        cooks[dish_id].append(member_id)
    tags: dict[int, list[int]] = {i: [] for i in ids}
    for dish_id, tag_id in await session.execute(
        select(DishTag.dish_id, DishTag.tag_id).where(DishTag.dish_id.in_(ids))
    ):
        tags[dish_id].append(tag_id)
    images: dict[int, list[DishImage]] = {i: [] for i in ids}
    for img in await session.scalars(
        select(DishImage)
        .where(DishImage.dish_id.in_(ids))
        .order_by(DishImage.is_cover.desc(), DishImage.position)
    ):
        images[img.dish_id].append(img)
    logs: dict[int, list[CookLog]] = {i: [] for i in ids}
    if with_log:
        for entry in await session.scalars(
            select(CookLog)
            .where(CookLog.dish_id.in_(ids))
            .order_by(CookLog.cooked_at.desc())
            .limit(10 * len(ids))
        ):
            if len(logs[entry.dish_id]) < 10:
                logs[entry.dish_id].append(entry)
    return [DishBundle(d, cooks[d.id], tags[d.id], images[d.id], logs[d.id]) for d in dishes]


EDITABLE = (
    "title",
    "description",
    "status",
    "main_ingredient_id",
    "cook_time_min",
    "difficulty",
    "servings",
    "ingredients",
    "steps",
    "notes",
    "rating",
    "source_url",
)


async def update_dish(
    session: AsyncSession,
    dish: Dish,
    data: dict[str, Any],
    family_tag_ids: set[int],
    family_member_ids: set[int],
) -> Dish:
    """Часткове оновлення: змінюються лише передані поля; tag_ids/cook_ids — повна заміна."""
    for key in EDITABLE:
        if key in data:
            value = data[key]
            if key == "status":
                value = DishStatus(value)
            if key == "main_ingredient_id" and value is not None and value not in family_tag_ids:
                value = None
            setattr(dish, key, value)
    if "tag_ids" in data:
        wanted = {t for t in data["tag_ids"] if t in family_tag_ids}
        if dish.main_ingredient_id:
            wanted.add(dish.main_ingredient_id)
        await session.execute(delete(DishTag).where(DishTag.dish_id == dish.id))
        session.add_all(DishTag(dish_id=dish.id, tag_id=t) for t in wanted)
    if "cook_ids" in data:
        await session.execute(delete(DishCook).where(DishCook.dish_id == dish.id))
        session.add_all(
            DishCook(dish_id=dish.id, member_id=m)
            for m in dict.fromkeys(data["cook_ids"])
            if m in family_member_ids
        )
    await session.commit()
    return dish


async def add_upload(session: AsyncSession, dish: Dish, data: bytes, media_dir: str) -> DishImage:
    saved = save_image(data, media_dir, ImageOrigin.upload.value)
    has_cover = await session.scalar(
        select(exists().where(and_(DishImage.dish_id == dish.id, DishImage.is_cover)))
    )
    position = await session.scalar(
        select(func.coalesce(func.max(DishImage.position), -1) + 1).where(
            DishImage.dish_id == dish.id
        )
    )
    img = DishImage(
        dish_id=dish.id,
        path=saved["path"],
        path_thumb=saved["path_thumb"],
        position=position,
        is_cover=not has_cover,
        origin=ImageOrigin.upload,
    )
    session.add(img)
    await session.commit()
    return img


async def set_cover(session: AsyncSession, dish: Dish, image_id: int) -> None:
    for img in await session.scalars(select(DishImage).where(DishImage.dish_id == dish.id)):
        img.is_cover = img.id == image_id
    await session.commit()


async def remove_image(session: AsyncSession, dish: Dish, image_id: int, media_dir: str) -> bool:
    img = await session.get(DishImage, image_id)
    if img is None or img.dish_id != dish.id:
        return False
    was_cover = img.is_cover
    delete_images(media_dir, [{"path": img.path, "path_thumb": img.path_thumb}])
    await session.delete(img)
    await session.flush()
    if was_cover:
        first = await session.scalar(
            select(DishImage).where(DishImage.dish_id == dish.id).order_by(DishImage.position)
        )
        if first:
            first.is_cover = True
    await session.commit()
    return True


async def tag_counts(session: AsyncSession, family_id: int) -> dict[int, int]:
    """Скільки не архівних страв має кожен тег (для чипів фільтрів)."""
    rows = await session.execute(
        select(DishTag.tag_id, func.count())
        .join(Dish, Dish.id == DishTag.dish_id)
        .where(Dish.family_id == family_id, Dish.status != DishStatus.archived)
        .group_by(DishTag.tag_id)
    )
    return dict(rows.all())
