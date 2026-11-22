"""Спільні фікстури. DB-тести потребують TEST_DATABASE_URL (окрема база, її схема скидається)."""

import hashlib
import hmac
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetMe, SendMessage, TelegramMethod
from aiogram.types import Chat, Message, User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BOT_TOKEN = "123456:TEST-token"
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")

os.environ.update(
    ENV="test",
    BOT_TOKEN=BOT_TOKEN,
    WEBHOOK_SECRET="test-secret",
    PUBLIC_BASE_URL="https://tarilka.test",
    ADMIN_TG_IDS="",
    DATABASE_URL=TEST_DB_URL or "postgresql+asyncpg://nobody@localhost:1/none",
    MEDIA_DIR="/nonexistent",
)

needs_db = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL не задано")


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Скидає схему тестової бази й проганяє всі міграції (раз за сесію)."""
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL не задано")
    import asyncio

    async def reset() -> None:
        engine = create_async_engine(TEST_DB_URL)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(reset())
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"url={TEST_DB_URL}", "upgrade", "head"],
        check=True,
        capture_output=True,
    )
    return TEST_DB_URL


@pytest.fixture
async def sessionmaker(migrated_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_db)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE invites, members RESTART IDENTITY CASCADE"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def session(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


# --- Telegram ---------------------------------------------------------------------------------

BOT_USER = User(id=123456, is_bot=True, first_name="Tarilka", username="tarilka_test_bot")


class FakeSession(BaseSession):
    """Записує виклики Bot API замість мережі; відповідає правдоподібними об'єктами."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None):
        self.requests.append(method)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.requests),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            )
        return True

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        yield b""

    async def close(self) -> None:
        pass

    def sent_texts(self) -> list[str]:
        return [m.text for m in self.requests if isinstance(m, SendMessage)]


@pytest.fixture
def fake_bot() -> Bot:
    return Bot(BOT_TOKEN, session=FakeSession())


_update_id = 0


def make_message_update(
    user_id: int,
    text_: str,
    *,
    username: str | None = None,
    first_name: str = "Тест",
    chat_type: str = "private",
) -> dict[str, Any]:
    global _update_id
    _update_id += 1
    chat_id = user_id if chat_type == "private" else -100500
    msg: dict[str, Any] = {
        "message_id": _update_id,
        "date": int(datetime.now(UTC).timestamp()),
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": user_id, "is_bot": False, "first_name": first_name, "username": username},
        "text": text_,
    }
    if text_.startswith("/"):
        msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text_.split()[0])}]
    return {"update_id": _update_id, "message": msg}


def make_init_data(user: dict[str, Any], *, auth_date: int, token: str = BOT_TOKEN) -> str:
    """Підписує initData так само, як Telegram (для тестів)."""
    fields = {"auth_date": str(auth_date), "query_id": "AAE", "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)
