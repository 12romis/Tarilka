"""Авторизація API.

Mini App у Telegram — заголовок `Authorization: tma <initData>`.
Браузер/PWA — cookie сесії (див. app/services/web_auth.py).
"""

import time
from typing import Annotated
from urllib.parse import urlsplit

from aiogram.utils.web_app import WebAppInitData, safe_parse_webapp_init_data
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import Member
from app.db.session import get_session
from app.services.members import TgUser, resolve_member
from app.services.web_auth import find_session

SESSION_COOKIE = "tarilka_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def validate_init_data(
    init_data: str, bot_token: str, max_age_s: int, now: float | None = None
) -> WebAppInitData:
    """Перевіряє підпис (HMAC-SHA256 з ключем HMAC("WebAppData", BOT_TOKEN)) і свіжість.

    Кидає ValueError, якщо дані підроблені, без користувача або старші за `max_age_s`.
    """
    data = safe_parse_webapp_init_data(bot_token, init_data)  # ValueError, якщо підпис невірний
    if data.user is None:
        raise ValueError("initData без user")
    age = (now if now is not None else time.time()) - data.auth_date.timestamp()
    if age > max_age_s:
        raise ValueError("initData прострочені")
    return data


def _same_origin(request: Request, public_base_url: str) -> bool:
    """Захист cookie-сесії від CSRF: змінювати дані можна лише зі сторінок самої Тарілки."""
    origin = request.headers.get("origin")
    if request.method in SAFE_METHODS or not origin:
        return True  # SameSite=Lax і так не шле cookie з чужих POST; Origin — друга лінія
    allowed = {urlsplit(public_base_url).netloc, request.headers.get("host", "")}
    return urlsplit(origin).netloc in allowed


async def current_member(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Member:
    if settings.env == "dev" and settings.dev_auth_member_id is not None:
        member = await session.get(Member, settings.dev_auth_member_id)
        if member:
            return member
    if not authorization and session_token:
        if not _same_origin(request, settings.public_base_url):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Чужий Origin")
        ws = await find_session(session, session_token)
        member = await session.get(Member, ws.member_id) if ws else None
        if member is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сесія завершилась")
        request.state.web_session_id = ws.id if ws else None
        return member
    scheme, _, raw = (authorization or "").partition(" ")
    if scheme.lower() != "tma" or not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Потрібен вхід")
    try:
        data = validate_init_data(raw, settings.bot_token, settings.init_data_max_age_s)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Невалідні initData") from e
    assert data.user is not None
    member = await resolve_member(
        session,
        TgUser(data.user.id, data.user.username, data.user.first_name),
        settings.admin_tg_ids,
    )
    if member is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Не член сім'ї")
    return member


CurrentMember = Annotated[Member, Depends(current_member)]
