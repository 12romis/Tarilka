"""Авторизація Mini App: заголовок `Authorization: tma <initData>`."""

import time
from typing import Annotated

from aiogram.utils.web_app import WebAppInitData, safe_parse_webapp_init_data
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import Member
from app.db.session import get_session
from app.services.members import TgUser, resolve_member


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


async def current_member(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Member:
    if settings.env == "dev" and settings.dev_auth_member_id is not None:
        member = await session.get(Member, settings.dev_auth_member_id)
        if member:
            return member
    scheme, _, raw = (authorization or "").partition(" ")
    if scheme.lower() != "tma" or not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Потрібен заголовок Authorization: tma")
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
