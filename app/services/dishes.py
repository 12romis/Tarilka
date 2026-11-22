"""Страви: створення з чернетки, «Готую!», оцінка, пошук, експорт. Спільне для бота й API."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CookLog,
    Dish,
    DishCook,
    DishImage,
    DishStatus,
    DishTag,
    Family,
    ImageOrigin,
    Member,
    SourceType,
    Tag,
)
from app.services.draft_fields import Draft, tag_slugs
from app.services.meta import KIND_ORDER, FamilyMeta


async def create_from_draft(
    session: AsyncSession,
    meta: FamilyMeta,
    family_id: int,
    draft: Draft,
    *,
    created_by: int | None,
    source_type: SourceType = SourceType.manual,
    raw_import: dict[str, Any] | None = None,
    commit: bool = True,
) -> Dish:
    main = meta.tag("ingredient", draft.get("main_ingredient"))
    dish = Dish(
        family_id=family_id,
        title=draft["title"],
        description=draft.get("description"),
        status=DishStatus(draft.get("status") or DishStatus.can_cook),
        main_ingredient_id=main.id if main else None,
        cook_time_min=draft.get("cook_time_min"),
        difficulty=draft.get("difficulty"),
        servings=draft.get("servings"),
        ingredients=draft.get("ingredients"),
        steps=draft.get("steps"),
        notes=draft.get("notes"),
        source_url=draft.get("source_url"),
        source_type=source_type,
        source_author=draft.get("source_author"),
        raw_import=raw_import,
        created_by=created_by,
    )
    session.add(dish)
    await session.flush()
    tag_ids = list(
        dict.fromkeys(
            t.id
            for kind in KIND_ORDER
            for slug in tag_slugs(draft, kind.value)
            if (t := meta.tag(kind.value, slug))
        )
    )
    session.add_all(DishTag(dish_id=dish.id, tag_id=tid) for tid in tag_ids)
    cooks = [c for c in dict.fromkeys(draft.get("cooks") or []) if c in meta.member_by_id]
    session.add_all(DishCook(dish_id=dish.id, member_id=c) for c in cooks)
    await add_images(session, dish, draft.get("images") or [])
    if commit:
        await session.commit()
    return dish


async def add_images(session: AsyncSession, dish: Dish, images: list[dict[str, Any]]) -> None:
    """Додає фото з чернетки; перше стає обкладинкою, якщо її ще немає."""
    if not images:
        return
    existing = list(await session.scalars(select(DishImage).where(DishImage.dish_id == dish.id)))
    has_cover = any(i.is_cover for i in existing)
    known = {i.path for i in existing}
    pos = max((i.position for i in existing), default=-1) + 1
    for img in images:
        if img["path"] in known:
            continue
        session.add(
            DishImage(
                dish_id=dish.id,
                path=img["path"],
                path_thumb=img.get("path_thumb"),
                position=pos,
                is_cover=not has_cover,
                origin=ImageOrigin(img.get("origin", "upload")),
            )
        )
        has_cover, pos = True, pos + 1


# Різні апострофи й лапки в назвах вважаємо однаковими: «Олів’є» = «Олів'є».
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "´": "'"})


def normalize_title(title: str) -> str:
    return " ".join(title.translate(_APOSTROPHES).lower().split())


async def find_by_title(session: AsyncSession, family_id: int, title: str) -> Dish | None:
    """Наявна (не архівна) страва з тією самою назвою — без урахування регістру й пробілів."""
    key = normalize_title(title)
    if not key:
        return None
    # Сім'я — це сотні страв, тож порівняння в Python простіше за функціональний індекс.
    for dish in await session.scalars(
        select(Dish)
        .where(Dish.family_id == family_id, Dish.status != DishStatus.archived)
        .order_by(Dish.id)
    ):
        if normalize_title(dish.title) == key:
            return dish
    return None


async def find_by_url(session: AsyncSession, family_id: int, url: str) -> Dish | None:
    return await session.scalar(
        select(Dish).where(Dish.family_id == family_id, Dish.source_url == url).limit(1)
    )


async def merge_draft(session: AsyncSession, meta: FamilyMeta, dish: Dish, draft: Draft) -> Dish:
    """Доповнює наявну страву чернеткою: нічого не перезаписує, лише додає.

    Нові кухарі й теги додаються, порожні поля заповнюються, «хочемо спробувати» стає
    «вміємо», якщо чернетка каже «вміємо».
    """
    for fld in (
        "description",
        "cook_time_min",
        "difficulty",
        "servings",
        "ingredients",
        "steps",
        "notes",
        "source_url",
        "source_author",
    ):
        if getattr(dish, fld) in (None, [], "") and draft.get(fld) not in (None, [], ""):
            setattr(dish, fld, draft[fld])
    if dish.main_ingredient_id is None and (
        main := meta.tag("ingredient", draft.get("main_ingredient"))
    ):
        dish.main_ingredient_id = main.id
    if dish.status == DishStatus.want_to_try and draft.get("status") == DishStatus.can_cook:
        dish.status = DishStatus.can_cook
    tag_ids = {
        t.id
        for kind in KIND_ORDER
        for slug in tag_slugs(draft, kind.value)
        if (t := meta.tag(kind.value, slug))
    }
    if tag_ids:
        await session.execute(
            insert(DishTag)
            .values([{"dish_id": dish.id, "tag_id": t} for t in tag_ids])
            .on_conflict_do_nothing()
        )
    await add_images(session, dish, draft.get("images") or [])
    cooks = [c for c in dict.fromkeys(draft.get("cooks") or []) if c in meta.member_by_id]
    if cooks:
        await session.execute(
            insert(DishCook)
            .values([{"dish_id": dish.id, "member_id": c} for c in cooks])
            .on_conflict_do_nothing()
        )
    return dish


async def mark_cooked(
    session: AsyncSession, dish: Dish, member: Member | None, note: str | None = None
) -> Dish:
    """Запис у cook_log; want_to_try → can_cook; автор стає тим, хто вміє."""
    now = datetime.now(UTC)
    session.add(
        CookLog(dish_id=dish.id, member_id=member.id if member else None, cooked_at=now, note=note)
    )
    dish.last_cooked_at = now
    dish.cooked_count = (dish.cooked_count or 0) + 1
    if dish.status == DishStatus.want_to_try:
        dish.status = DishStatus.can_cook
    if member:
        await session.execute(
            insert(DishCook).values(dish_id=dish.id, member_id=member.id).on_conflict_do_nothing()
        )
    await session.commit()
    return dish


async def set_rating(session: AsyncSession, dish: Dish, rating: int) -> None:
    dish.rating = max(1, min(5, rating))
    await session.commit()


async def get_family_dish(session: AsyncSession, family_id: int, dish_id: int) -> Dish | None:
    dish = await session.get(Dish, dish_id)
    return dish if dish and dish.family_id == family_id else None


async def search(session: AsyncSession, family_id: int, q: str, limit: int = 10) -> list[Dish]:
    """Пошук за назвою: підрядок або trigram-схожість (pg_trgm), найближчі — першими."""
    q = q.strip()
    sim = func.similarity(Dish.title, q)
    stmt = (
        select(Dish)
        .where(
            Dish.family_id == family_id,
            Dish.status != DishStatus.archived,
            (Dish.title.ilike(f"%{q}%"))
            | (sim > 0.2)
            | (func.word_similarity(q, Dish.title) > 0.4),
        )
        .order_by(Dish.title.ilike(f"{q}%").desc(), sim.desc(), Dish.title)
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def count_active(session: AsyncSession, family_id: int) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Dish)
            .where(Dish.family_id == family_id, Dish.status != DishStatus.archived)
        )
        or 0
    )


@dataclass
class DishInfo:
    """Страва з іменами кухарів і тегами — для карток."""

    dish: Dish
    cooks: list[Member]
    tags: list[Tag]


async def load_info(session: AsyncSession, meta: FamilyMeta, dishes: list[Dish]) -> list[DishInfo]:
    ids = [d.id for d in dishes]
    if not ids:
        return []
    cooks: dict[int, list[Member]] = {i: [] for i in ids}
    for dish_id, member_id in await session.execute(
        select(DishCook.dish_id, DishCook.member_id).where(DishCook.dish_id.in_(ids))
    ):
        if m := meta.member_by_id.get(member_id):
            cooks[dish_id].append(m)
    tags: dict[int, list[Tag]] = {i: [] for i in ids}
    for dish_id, tag_id in await session.execute(
        select(DishTag.dish_id, DishTag.tag_id).where(DishTag.dish_id.in_(ids))
    ):
        if t := meta.by_id.get(tag_id):
            tags[dish_id].append(t)
    return [DishInfo(d, cooks[d.id], tags[d.id]) for d in dishes]


def _row(obj: Any) -> dict[str, Any]:
    out = {}
    for col in obj.__table__.columns:
        v = getattr(obj, col.key)
        out[col.key] = v.isoformat() if isinstance(v, datetime) else v
    return out


async def export_family(session: AsyncSession, family_id: int) -> dict[str, Any]:
    """Уся база сім'ї в JSON — простий власний бекап (/export)."""
    dish_ids = select(Dish.id).where(Dish.family_id == family_id)
    out: dict[str, Any] = {
        "exported_at": datetime.now(UTC).isoformat(),
        "family": _row(await session.get(Family, family_id)),
    }
    queries = {
        "members": select(Member).where(Member.family_id == family_id),
        "tags": select(Tag).where(Tag.family_id == family_id),
        "dishes": select(Dish).where(Dish.family_id == family_id),
        "dish_cooks": select(DishCook).where(DishCook.dish_id.in_(dish_ids)),
        "dish_tags": select(DishTag).where(DishTag.dish_id.in_(dish_ids)),
        "dish_images": select(DishImage).where(DishImage.dish_id.in_(dish_ids)),
        "cook_log": select(CookLog).where(CookLog.dish_id.in_(dish_ids)),
    }
    for key, stmt in queries.items():
        out[key] = [_row(o) for o in await session.scalars(stmt)]
    return out
