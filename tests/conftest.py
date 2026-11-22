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
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    EditMessageCaption,
    EditMessageText,
    GetMe,
    SendDocument,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
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
        await conn.execute(
            text(
                "TRUNCATE invites, members, dishes, dish_cooks, dish_tags, dish_images, cook_log, "
                "import_jobs RESTART IDENTITY CASCADE"
            )
        )
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
        self.message_ids: list[int] = []  # id надісланих повідомлень, по порядку
        self.photo_ids: set[int] = set()  # повідомлення-фото: edit_message_text для них падає

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None):
        self.requests.append(method)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, EditMessageText) and method.message_id in self.photo_ids:
            raise TelegramBadRequest(
                method=method, message="Bad Request: there is no text in the message to edit"
            )
        if isinstance(method, SendPhoto):
            self.message_ids.append(1000 + len(self.requests))
            self.photo_ids.add(self.message_ids[-1])
            return Message(
                message_id=self.message_ids[-1],
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                caption=method.caption,
            )
        if isinstance(method, SendMessage | SendDocument):
            self.message_ids.append(1000 + len(self.requests))
            return Message(
                message_id=self.message_ids[-1],
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=getattr(method, "text", None),
            )
        return True

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        yield b""

    async def close(self) -> None:
        pass

    def sent_texts(self) -> list[str]:
        return [m.text for m in self.requests if isinstance(m, SendMessage)]

    def sent(self) -> list[SendMessage]:
        return [m for m in self.requests if isinstance(m, SendMessage)]

    def edits(self) -> list[EditMessageText]:
        return [m for m in self.requests if isinstance(m, EditMessageText)]

    def last_card_text(self) -> str:
        """Текст останньої картки — з edit_message_text, edit_message_caption чи send_photo."""
        for m in reversed(self.requests):
            if isinstance(m, EditMessageCaption | SendPhoto):
                return m.caption or ""
            if isinstance(m, EditMessageText) and m.message_id not in self.photo_ids:
                return m.text
        return ""

    def of(self, cls: type) -> list[Any]:
        return [m for m in self.requests if isinstance(m, cls)]


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


def make_callback_update(
    user_id: int, data: str, *, message_id: int, chat_id: int | None = None
) -> dict[str, Any]:
    global _update_id
    _update_id += 1
    return {
        "update_id": _update_id,
        "callback_query": {
            "id": str(_update_id),
            "from": {"id": user_id, "is_bot": False, "first_name": "Тест"},
            "chat_instance": "ci",
            "data": data,
            "message": {
                "message_id": message_id,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": chat_id or user_id, "type": "private"},
                "from": {"id": BOT_USER.id, "is_bot": True, "first_name": "Tarilka"},
                "text": "card",
            },
        },
    }


class FakeAI:
    """Замість OpenAI: повертає заздалегідь задані відповіді й записує промпти."""

    def __init__(self, responses: list[Any] | None = None, transcript: str = "") -> None:
        self.responses = list(responses or [])
        self.transcript = transcript
        self.calls: list[tuple[str, str]] = []

    async def structured(self, system, user, schema, images=None):
        self.calls.append((system, user))
        resp = self.responses.pop(0)
        if isinstance(resp, BaseException):
            raise resp
        return schema.model_validate(resp), {"model": "fake", "seconds": 0}

    async def transcribe(self, audio_path):
        return self.transcript, {"model": "fake-stt", "seconds": 0}


def dish_json(title: str, **kw: Any) -> dict[str, Any]:
    """Відповідь LLM для однієї страви з типовими порожніми полями."""
    base: dict[str, Any] = {
        "has_recipe": False,
        "confidence": 0.9,
        "title": title,
        "description": None,
        "status": "can_cook",
        "cooks": [],
        "meal": [],
        "course": [],
        "main_ingredient": None,
        "ingredient_tags": [],
        "traits": [],
        "cuisine": None,
        "cook_time_min": None,
        "difficulty": None,
        "servings": None,
        "ingredients": [],
        "steps": [],
        "notes": None,
    }
    return base | kw


# --- Хелпери наскрізних сценаріїв -------------------------------------------------------------


async def feed(dp, bot: Bot, upd: dict[str, Any]) -> None:
    """Проганяє update через Dispatcher, як це робить вебхук."""
    from aiogram.types import Update

    await dp.feed_update(bot, Update.model_validate(upd, context={"bot": bot}))


async def press(dp, bot: Bot, user_id: int, cb: Any, message_id: int) -> None:
    """Натискання inline-кнопки з CallbackData."""
    await feed(dp, bot, make_callback_update(user_id, cb.pack(), message_id=message_id))


def make_worker(sessionmaker, bot: Bot, ai: Any, http: Any = None, media_dir: str = "/nonexistent"):
    """Worker для тестів: без затримок повторів, з імітованим HTTP (httpx.MockTransport)."""
    import httpx

    from app.config import get_settings
    from app.worker import Worker

    s = get_settings().model_copy(
        update={"job_retry_delays_s": [0, 0], "media_dir": media_dir, "ytdlp_cookies_file": ""}
    )
    w = Worker(sessionmaker, bot, ai, s)
    w.http = http or httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    return w


async def run_worker(sessionmaker, bot: Bot, ai: Any, **kw: Any) -> None:
    """Один прохід worker: бере всі доступні задачі й чекає їх завершення."""
    w = make_worker(sessionmaker, bot, ai, **kw)
    await w.tick()
    for t in list(w.tasks):
        await t
