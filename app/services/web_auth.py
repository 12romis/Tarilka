"""Вхід у браузер/PWA без Telegram initData.

Бот (/web) або Mini App видає одноразове посилання з токеном і 6-значний код (15 хв).
Обмін на сесію: довгий випадковий токен у cookie, у БД — лише його sha256. Код потрібен
для iPhone: застосунок на головному екрані має окреме від Safari сховище cookie.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LoginLink, Member, WebSession

LINK_TTL = timedelta(minutes=15)
SESSION_TTL = timedelta(days=180)
SESSION_TOUCH = timedelta(hours=12)  # не частіше — оновлення last_seen/expires


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login_url(base_url: str, token: str) -> str:
    """Сторінка /login у PWA сама надсилає токен POST-ом: прев'ю посилань його не «з'їдять»."""
    return f"{base_url.rstrip('/')}/login?t={token}"


def _now() -> datetime:
    return datetime.now(UTC)


async def create_login_link(session: AsyncSession, member: Member) -> tuple[str, str, datetime]:
    """Новий токен і код; попередні невикористані посилання члена сім'ї гасяться."""
    now = _now()
    await session.execute(
        update(LoginLink)
        .where(LoginLink.member_id == member.id, LoginLink.used_at.is_(None))
        .values(used_at=now)
    )
    active = set(
        await session.scalars(
            select(LoginLink.code).where(LoginLink.used_at.is_(None), LoginLink.expires_at > now)
        )
    )
    code = f"{secrets.randbelow(10**6):06d}"
    while code in active:
        code = f"{secrets.randbelow(10**6):06d}"
    token = secrets.token_urlsafe(32)
    link = LoginLink(
        member_id=member.id, token_hash=_hash(token), code=code, expires_at=now + LINK_TTL
    )
    session.add(link)
    await session.commit()
    return token, code, link.expires_at


async def redeem(
    session: AsyncSession, *, token: str | None = None, code: str | None = None
) -> Member | None:
    """Погашає посилання (за токеном або кодом). Кожне спрацьовує лише раз."""
    now = _now()
    stmt = select(LoginLink).where(LoginLink.used_at.is_(None), LoginLink.expires_at > now)
    if token:
        stmt = stmt.where(LoginLink.token_hash == _hash(token))
    elif code and code.isdigit() and len(code) == 6:
        stmt = stmt.where(LoginLink.code == code)
    else:
        return None
    link = await session.scalar(stmt.with_for_update())
    if link is None:
        return None
    link.used_at = now
    member = await session.get(Member, link.member_id)
    await session.commit()
    return member


async def create_session(session: AsyncSession, member: Member, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        WebSession(
            member_id=member.id,
            token_hash=_hash(token),
            user_agent=(user_agent or "")[:300] or None,
            expires_at=_now() + SESSION_TTL,
        )
    )
    await session.commit()
    return token


async def find_session(session: AsyncSession, token: str) -> WebSession | None:
    ws = await session.scalar(select(WebSession).where(WebSession.token_hash == _hash(token)))
    now = _now()
    if ws is None or ws.revoked_at is not None or ws.expires_at <= now:
        return None
    if now - ws.last_seen_at > SESSION_TOUCH:  # ковзний строк: активні сесії не спливають
        ws.last_seen_at = now
        ws.expires_at = now + SESSION_TTL
        await session.commit()
    return ws


async def list_sessions(session: AsyncSession, member: Member) -> list[WebSession]:
    return list(
        await session.scalars(
            select(WebSession)
            .where(
                WebSession.member_id == member.id,
                WebSession.revoked_at.is_(None),
                WebSession.expires_at > _now(),
            )
            .order_by(WebSession.last_seen_at.desc())
        )
    )


async def revoke(session: AsyncSession, member: Member, session_id: int) -> bool:
    result = await session.execute(
        update(WebSession)
        .where(
            WebSession.id == session_id,
            WebSession.member_id == member.id,
            WebSession.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    await session.commit()
    return bool(result.rowcount)  # type: ignore[attr-defined]
