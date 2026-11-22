from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import CurrentMember
from app.api.schemas import MemberIn, MemberOut, MetaOut, TagOut
from app.config import Settings, get_settings
from app.db.models import Member
from app.db.session import get_session
from app.services import catalog
from app.services import dishes as dish_svc
from app.services.members import add_member, is_admin
from app.services.meta import KIND_ORDER, load_meta

router = APIRouter(prefix="/api")


class MeOut(BaseModel):
    id: int
    name: str
    family_id: int
    is_admin: bool


@router.get("/me", response_model=MeOut)
async def me(member: CurrentMember, settings: Annotated[Settings, Depends(get_settings)]) -> MeOut:
    return MeOut(
        id=member.id,
        name=member.name,
        family_id=member.family_id,
        is_admin=is_admin(member, settings.admin_tg_ids),
    )


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_admin(member: CurrentMember, settings: SettingsDep) -> Member:
    if not is_admin(member, settings.admin_tg_ids):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Лише для адміна")
    return member


AdminMember = Annotated[Member, Depends(require_admin)]


def member_out(m: Member, settings: Settings) -> MemberOut:
    return MemberOut(
        id=m.id,
        name=m.name,
        aliases=list(m.aliases),
        has_telegram=m.tg_user_id is not None,
        is_admin=is_admin(m, settings.admin_tg_ids),
    )


@router.get("/meta", response_model=MetaOut)
async def get_meta(member: CurrentMember, session: SessionDep, settings: SettingsDep) -> MetaOut:
    meta = await load_meta(session, member.family_id)
    counts = await catalog.tag_counts(session, member.family_id)
    tags: dict[str, list[TagOut]] = {k.value: [] for k in KIND_ORDER}
    for t in meta.tags:
        tags[t.kind.value].append(
            TagOut(
                id=t.id,
                kind=t.kind.value,
                slug=t.slug,
                label=t.label,
                emoji=t.emoji,
                count=counts.get(t.id, 0),
            )
        )
    return MetaOut(
        members=[member_out(m, settings) for m in meta.members],
        tags=tags,
        total_dishes=await dish_svc.count_active(session, member.family_id),
    )


@router.post("/members", response_model=MemberOut, status_code=201)
async def create_member(
    body: MemberIn, admin: AdminMember, session: SessionDep, settings: SettingsDep
) -> MemberOut:
    """Член сім'ї без Telegram (наприклад, бабуся) — щоб можна було вказати «хто вміє»."""
    if not body.name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Потрібне ім'я")
    m = await add_member(
        session, body.name.strip(), aliases=_aliases(body.aliases), family_id=admin.family_id
    )
    return member_out(m, settings)


@router.patch("/members/{member_id}", response_model=MemberOut)
async def patch_member(
    member_id: int, body: MemberIn, admin: AdminMember, session: SessionDep, settings: SettingsDep
) -> MemberOut:
    m = await session.get(Member, member_id)
    if m is None or m.family_id != admin.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if body.name:
        m.name = body.name.strip()
    if body.aliases is not None:
        m.aliases = _aliases(body.aliases)
    await session.commit()
    return member_out(m, settings)


def _aliases(values: list[str] | None) -> list[str]:
    return list(dict.fromkeys(a.strip().lower() for a in values or [] if a.strip()))
