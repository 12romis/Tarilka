"""Члени сім'ї, білий список і інвайти. Спільне для бота й API."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite, Member

DEFAULT_FAMILY_ID = 1
INVITE_PREFIX = "inv_"


@dataclass(frozen=True)
class TgUser:
    """Мінімум даних про користувача Telegram, потрібний для доступу."""

    id: int
    username: str | None = None
    first_name: str = ""


def normalize_username(username: str | None) -> str | None:
    return username.lstrip("@").lower() if username else None


async def resolve_member(
    session: AsyncSession, user: TgUser, admin_ids: list[int] | tuple[int, ...] = ()
) -> Member | None:
    """Знаходить члена сім'ї за Telegram ID.

    Якщо ID ще не прив'язаний: шукає за username (запис, доданий через CLI), а для
    `ADMIN_TG_IDS` створює адміна з ім'ям із Telegram. Чужим повертає None.
    """
    member = await session.scalar(select(Member).where(Member.tg_user_id == user.id))
    if member:
        return member
    username = normalize_username(user.username)
    if username:
        member = await session.scalar(
            select(Member).where(Member.tg_username == username, Member.tg_user_id.is_(None))
        )
        if member:
            member.tg_user_id = user.id
            await session.commit()
            return member
    if user.id in admin_ids:
        member = Member(
            family_id=DEFAULT_FAMILY_ID,
            name=user.first_name or username or str(user.id),
            tg_user_id=user.id,
            tg_username=username,
            is_admin=True,
        )
        session.add(member)
        await session.commit()
        return member
    return None


async def add_member(
    session: AsyncSession,
    name: str,
    *,
    username: str | None = None,
    tg_user_id: int | None = None,
    is_admin: bool = False,
    aliases: list[str] | None = None,
    family_id: int = DEFAULT_FAMILY_ID,
) -> Member:
    member = Member(
        family_id=family_id,
        name=name,
        tg_username=normalize_username(username),
        tg_user_id=tg_user_id,
        is_admin=is_admin,
        aliases=aliases or [],
    )
    session.add(member)
    await session.commit()
    return member


async def create_invite(
    session: AsyncSession, admin: Member, name: str | None, ttl_hours: int
) -> Invite:
    invite = Invite(
        token=secrets.token_urlsafe(12),
        family_id=admin.family_id,
        created_by=admin.id,
        name=(name or "").strip() or None,
        expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
    )
    session.add(invite)
    await session.commit()
    return invite


class InviteResult(StrEnum):
    joined = "joined"
    already_member = "already_member"
    invalid = "invalid"


async def accept_invite(
    session: AsyncSession, token: str, user: TgUser
) -> tuple[InviteResult, Member | None]:
    existing = await session.scalar(select(Member).where(Member.tg_user_id == user.id))
    if existing:
        return InviteResult.already_member, existing
    invite = await session.scalar(select(Invite).where(Invite.token == token).with_for_update())
    now = datetime.now(UTC)
    if invite is None or invite.used_at is not None or invite.expires_at < now:
        return InviteResult.invalid, None
    member = Member(
        family_id=invite.family_id,
        name=invite.name or user.first_name or normalize_username(user.username) or str(user.id),
        tg_user_id=user.id,
        tg_username=normalize_username(user.username),
    )
    session.add(member)
    await session.flush()
    invite.used_by = member.id
    invite.used_at = now
    await session.commit()
    return InviteResult.joined, member


def is_admin(member: Member, admin_ids: list[int] | tuple[int, ...] = ()) -> bool:
    return member.is_admin or (member.tg_user_id is not None and member.tg_user_id in admin_ids)
