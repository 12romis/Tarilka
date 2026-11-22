from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.db.models import Family, Member, Tag
from app.services.members import (
    InviteResult,
    TgUser,
    accept_invite,
    add_member,
    create_invite,
    resolve_member,
)


async def test_seed_dictionaries(session) -> None:
    assert await session.scalar(select(func.count()).select_from(Family)) == 1
    counts = dict((await session.execute(select(Tag.kind, func.count()).group_by(Tag.kind))).all())
    assert {k.value: v for k, v in counts.items()} == {
        "meal": 4,
        "course": 9,
        "ingredient": 15,
        "trait": 7,
        "cuisine": 5,
    }
    ext = await session.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='pg_trgm'"))
    assert ext == 1


async def test_resolve_by_tg_id(session) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1)
    assert (await resolve_member(session, TgUser(1))).id == m.id


async def test_stranger_is_not_resolved(session) -> None:
    await add_member(session, "Ігор", tg_user_id=1)
    assert await resolve_member(session, TgUser(2, "someone")) is None


async def test_username_binds_to_id_once(session) -> None:
    m = await add_member(session, "Зірка", username="@Vesta282")
    resolved = await resolve_member(session, TgUser(55, "vesta282"))
    assert resolved is not None and resolved.id == m.id and resolved.tg_user_id == 55
    # Інший акаунт, що згодом візьме той самий username, доступу не отримає.
    assert await resolve_member(session, TgUser(66, "vesta282")) is None


async def test_admin_ids_bootstrap(session) -> None:
    m = await resolve_member(session, TgUser(9, "boss", "Ігор"), admin_ids=[9])
    assert m is not None and m.is_admin and m.name == "Ігор"


async def test_invite_flow(session) -> None:
    admin = await add_member(session, "Ігор", tg_user_id=1, is_admin=True)
    inv = await create_invite(session, admin, "Бабуся", ttl_hours=24)
    result, member = await accept_invite(session, inv.token, TgUser(3, "granny", "Галя"))
    assert result is InviteResult.joined and member is not None
    assert member.name == "Бабуся" and member.tg_user_id == 3
    assert (await resolve_member(session, TgUser(3))).id == member.id
    # повторно те саме посилання не працює
    result, _ = await accept_invite(session, inv.token, TgUser(4))
    assert result is InviteResult.invalid


async def test_invite_without_name_uses_tg_name(session) -> None:
    admin = await add_member(session, "Ігор", tg_user_id=1, is_admin=True)
    inv = await create_invite(session, admin, None, ttl_hours=24)
    _, member = await accept_invite(session, inv.token, TgUser(3, None, "Оля"))
    assert member is not None and member.name == "Оля"


async def test_expired_invite(session) -> None:
    admin = await add_member(session, "Ігор", tg_user_id=1, is_admin=True)
    inv = await create_invite(session, admin, None, ttl_hours=24)
    inv.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()
    result, member = await accept_invite(session, inv.token, TgUser(3))
    assert result is InviteResult.invalid and member is None


async def test_invite_for_existing_member(session) -> None:
    admin = await add_member(session, "Ігор", tg_user_id=1, is_admin=True)
    inv = await create_invite(session, admin, None, ttl_hours=24)
    result, member = await accept_invite(session, inv.token, TgUser(1))
    assert result is InviteResult.already_member and member.id == admin.id
    assert await session.scalar(select(func.count()).select_from(Member)) == 1
