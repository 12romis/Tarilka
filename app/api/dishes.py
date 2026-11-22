"""REST для Mini App: каталог, страва, редагування, «Готую», фото, «Що приготувати?»."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, UploadFile, status

from app.api.auth import CurrentMember
from app.api.meta import SessionDep, SettingsDep
from app.api.schemas import (
    CookedIn,
    DishCard,
    DishFull,
    DishIn,
    DishPage,
    ImagePatch,
    SortKey,
    SuggestIn,
    SuggestOut,
)
from app.db.models import Dish, DishStatus, Member, SourceType
from app.services import catalog
from app.services import dishes as dish_svc
from app.services.meta import load_meta
from app.services.suggest import Filters, suggest

router = APIRouter(prefix="/api")

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
MAX_PAGE = 60


async def _dish_or_404(session, member: Member, dish_id: int) -> Dish:
    dish = await dish_svc.get_family_dish(session, member.family_id, dish_id)
    if dish is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Страву не знайдено")
    return dish


async def _full(session, dish: Dish) -> DishFull:
    await session.refresh(dish)
    return DishFull.of((await catalog.bundles(session, [dish], with_log=True))[0])


@router.get("/dishes", response_model=DishPage)
async def list_dishes(
    member: CurrentMember,
    session: SessionDep,
    q: str = "",
    status_: Annotated[list[str], Query(alias="status")] = [],  # noqa: B006 — FastAPI Query
    meal: Annotated[list[str], Query()] = [],  # noqa: B006
    course: Annotated[list[str], Query()] = [],  # noqa: B006
    ingredient: Annotated[list[str], Query()] = [],  # noqa: B006
    trait: Annotated[list[str], Query()] = [],  # noqa: B006
    cuisine: Annotated[list[str], Query()] = [],  # noqa: B006
    cook: Annotated[list[int], Query()] = [],  # noqa: B006
    max_time: int | None = None,
    sort: SortKey = "stale",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 30,
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> DishPage:
    f = catalog.ListFilters(
        q=q,
        meal=meal,
        course=course,
        ingredient=ingredient,
        trait=trait,
        cuisine=cuisine,
        cook=cook,
        max_time=max_time,
        sort=sort,
    )
    if status_:
        f.status = status_
    dishes, total = await catalog.list_dishes(session, member.family_id, f, limit, cursor)
    items = [DishCard.of(b) for b in await catalog.bundles(session, dishes)]
    nxt = cursor + len(items)
    return DishPage(items=items, total=total, next_cursor=nxt if nxt < total else None)


@router.get("/dishes/{dish_id}", response_model=DishFull)
async def get_dish(dish_id: int, member: CurrentMember, session: SessionDep) -> DishFull:
    return await _full(session, await _dish_or_404(session, member, dish_id))


async def _apply(session, member: Member, dish: Dish, body: DishIn) -> None:
    meta = await load_meta(session, member.family_id)
    data = body.model_dump(exclude_unset=True)
    if "ingredients" in data and data["ingredients"] is not None:
        data["ingredients"] = [i for i in data["ingredients"] if i.get("name")]
    await catalog.update_dish(session, dish, data, set(meta.by_id), set(meta.member_by_id))


@router.post("/dishes", response_model=DishFull, status_code=201)
async def create_dish(body: DishIn, member: CurrentMember, session: SessionDep) -> DishFull:
    if not body.title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Потрібна назва")
    if existing := await dish_svc.find_by_title(session, member.family_id, body.title):
        raise HTTPException(status.HTTP_409_CONFLICT, {"existing_id": existing.id})
    dish = Dish(
        family_id=member.family_id,
        title=body.title.strip(),
        created_by=member.id,
        source_type=SourceType.manual,
    )
    session.add(dish)
    await session.flush()
    await _apply(session, member, dish, body)
    return await _full(session, dish)


@router.patch("/dishes/{dish_id}", response_model=DishFull)
async def patch_dish(
    dish_id: int, body: DishIn, member: CurrentMember, session: SessionDep
) -> DishFull:
    dish = await _dish_or_404(session, member, dish_id)
    await _apply(session, member, dish, body)
    return await _full(session, dish)


@router.delete("/dishes/{dish_id}", status_code=204)
async def archive_dish(dish_id: int, member: CurrentMember, session: SessionDep) -> None:
    """М'яке видалення: страва зникає з каталогу й пропозицій, але дані лишаються."""
    dish = await _dish_or_404(session, member, dish_id)
    dish.status = DishStatus.archived
    await session.commit()


@router.post("/dishes/{dish_id}/cooked", response_model=DishFull)
async def cooked(
    dish_id: int, body: CookedIn, member: CurrentMember, session: SessionDep
) -> DishFull:
    dish = await _dish_or_404(session, member, dish_id)
    await dish_svc.mark_cooked(session, dish, member, body.note)
    return await _full(session, dish)


@router.post("/dishes/{dish_id}/images", response_model=DishFull, status_code=201)
async def upload_image(
    dish_id: int,
    file: UploadFile,
    member: CurrentMember,
    session: SessionDep,
    settings: SettingsDep,
) -> DishFull:
    dish = await _dish_or_404(session, member, dish_id)
    if (file.content_type or "") not in IMAGE_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Лише JPEG, PNG, WebP, HEIC")
    data = await file.read(settings.max_image_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_image_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Фото більше 10 МБ")
    try:
        await catalog.add_upload(session, dish, data, settings.media_dir)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Це не зображення") from e
    return await _full(session, dish)


@router.patch("/dishes/{dish_id}/images/{image_id}", response_model=DishFull)
async def patch_image(
    dish_id: int, image_id: int, body: ImagePatch, member: CurrentMember, session: SessionDep
) -> DishFull:
    dish = await _dish_or_404(session, member, dish_id)
    if body.is_cover:
        await catalog.set_cover(session, dish, image_id)
    return await _full(session, dish)


@router.delete("/dishes/{dish_id}/images/{image_id}", response_model=DishFull)
async def delete_image(
    dish_id: int, image_id: int, member: CurrentMember, session: SessionDep, settings: SettingsDep
) -> DishFull:
    dish = await _dish_or_404(session, member, dish_id)
    if not await catalog.remove_image(session, dish, image_id, settings.media_dir):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return await _full(session, dish)


@router.post("/suggest", response_model=SuggestOut)
async def suggest_dishes(
    body: SuggestIn, member: CurrentMember, session: SessionDep, settings: SettingsDep
) -> SuggestOut:
    f = Filters(
        meal=body.meal,
        cooks=body.cook,
        quick=body.quick,
        include_new=body.include_new,
        exclude_ids=body.exclude_ids,
    )
    dishes, total = await suggest(session, member.family_id, f, settings, datetime.now(UTC))
    return SuggestOut(
        dishes=[DishCard.of(b) for b in await catalog.bundles(session, dishes)],
        total_candidates=total,
        total_dishes=await dish_svc.count_active(session, member.family_id),
    )
