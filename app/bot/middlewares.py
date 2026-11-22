from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import texts
from app.services.members import INVITE_PREFIX, TgUser, resolve_member


def is_invite_start(event: TelegramObject) -> bool:
    return (
        isinstance(event, Message)
        and event.chat.type == ChatType.PRIVATE
        and (event.text or "").startswith(f"/start {INVITE_PREFIX}")
    )


class AccessMiddleware(BaseMiddleware):
    """Білий список: пропускає лише членів сім'ї (та `/start inv_…` від нових).

    Чужим у приватному чаті один раз відповідає «Бот приватний», далі мовчить.
    Перелік попереджених живе в пам'яті процесу: після рестарту — ще одне попередження.
    """

    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], admin_ids: list[int]
    ) -> None:
        self.sessionmaker = sessionmaker
        self.admin_ids = admin_ids
        self.warned: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.is_bot:
            return None
        async with self.sessionmaker() as session:
            member = await resolve_member(
                session, TgUser(user.id, user.username, user.first_name), self.admin_ids
            )
            data["session"] = session
            data["member"] = member
            if member is not None or is_invite_start(event):
                return await handler(event, data)
        await self._deny(event, user.id)
        return None

    async def _deny(self, event: TelegramObject, user_id: int) -> None:
        if isinstance(event, CallbackQuery):
            await event.answer(texts.PRIVATE_BOT, show_alert=True)
            return
        if not isinstance(event, Message) or event.chat.type != ChatType.PRIVATE:
            return  # у групах чужих просто ігноруємо
        if user_id in self.warned:
            return
        self.warned.add(user_id)
        await event.answer(texts.PRIVATE_BOT)
