"""Вхід у браузер/PWA: одноразові посилання й коди, cookie-сесії, захист від перебору й CSRF."""

import time
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app import texts
from app.api import web_login
from app.api.auth import SESSION_COOKIE
from app.api.web_login import FailLimiter
from app.bot.router import build_dispatcher
from app.db.models import LoginLink, WebSession
from app.main import create_app
from app.services import web_auth
from app.services.members import add_member
from tests.conftest import feed, make_init_data, make_message_update

BASE = "https://tarilka.test"


def tma(user_id: int) -> dict[str, str]:
    raw = make_init_data({"id": user_id, "first_name": "T"}, auth_date=int(time.time()))
    return {"Authorization": f"tma {raw}"}


@pytest.fixture
async def client(sessionmaker):
    web_login.limiter = FailLimiter()
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url=BASE) as c:
        yield c


@pytest.fixture
async def igor(session):
    return await add_member(session, "Ігор", tg_user_id=10, is_admin=True)


async def test_link_from_mini_app_then_login_by_token(client, igor, session) -> None:
    r = await client.post("/api/auth/link", headers=tma(10))
    assert r.status_code == 200, r.text
    link = r.json()
    assert link["url"].startswith(f"{BASE}/login?t=") and len(link["code"]) == 6
    token = link["url"].split("t=")[1]

    assert (await client.get("/api/me")).status_code == 401  # ще без сесії
    r = await client.post("/api/auth/login", json={"token": token})
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()

    me = await client.get("/api/me")  # httpx зберіг cookie
    assert me.status_code == 200 and me.json()["name"] == "Ігор"
    assert (await client.get("/api/dishes")).status_code == 200

    # посилання одноразове
    client.cookies.clear()
    again = await client.post("/api/auth/login", json={"token": token})
    assert again.status_code == 401
    stored = await session.scalar(select(WebSession))
    assert stored.token_hash != client.cookies.get(SESSION_COOKIE)  # у БД лише хеш


async def test_login_by_code_and_new_link_cancels_old(client, igor, session) -> None:
    _, old_code, _ = await web_auth.create_login_link(session, igor)
    _, code, _ = await web_auth.create_login_link(session, igor)
    assert (await client.post("/api/auth/login", json={"code": old_code})).status_code == 401
    assert (await client.post("/api/auth/login", json={"code": f" {code} "})).status_code == 204
    assert (await client.get("/api/me")).status_code == 200


async def test_expired_link(client, igor, session) -> None:
    token, _, _ = await web_auth.create_login_link(session, igor)
    await session.execute(
        update(LoginLink).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    assert (await client.post("/api/auth/login", json={"token": token})).status_code == 401


async def test_brute_force_limited(client, igor, session) -> None:
    _, code, _ = await web_auth.create_login_link(session, igor)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(10):
        assert (await client.post("/api/auth/login", json={"code": wrong})).status_code == 401
    r = await client.post("/api/auth/login", json={"code": code})
    assert r.status_code == 429  # навіть правильний код — після 10 невдач з цієї IP


def test_fail_limiter_window() -> None:
    lim = FailLimiter(limit=2, window_s=60)
    lim.fail("ip", now=0)
    lim.fail("ip", now=10)
    assert lim.blocked("ip", now=30) and not lim.blocked("other", now=30)
    assert not lim.blocked("ip", now=65)  # перша спроба вийшла з вікна


async def _login(client, session, member) -> None:
    token, _, _ = await web_auth.create_login_link(session, member)
    assert (await client.post("/api/auth/login", json={"token": token})).status_code == 204


async def test_sessions_list_revoke_logout(client, igor, session) -> None:
    await _login(client, session, igor)
    other_token = await web_auth.create_session(session, igor, "Other phone")
    listed = (await client.get("/api/auth/sessions")).json()
    assert len(listed) == 2 and [s["current"] for s in listed].count(True) == 1
    other = next(s for s in listed if not s["current"])
    assert other["user_agent"] == "Other phone"

    assert (await client.delete(f"/api/auth/sessions/{other['id']}")).status_code == 204
    assert await web_auth.find_session(session, other_token) is None
    assert len((await client.get("/api/auth/sessions")).json()) == 1

    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.get("/api/me")).status_code == 401


async def test_revoked_or_expired_session_is_401(client, igor, session) -> None:
    await _login(client, session, igor)
    await session.execute(
        update(WebSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    assert (await client.get("/api/me")).status_code == 401


async def test_cookie_session_rejects_foreign_origin(client, igor, session) -> None:
    await _login(client, session, igor)
    evil = {"Origin": "https://evil.example"}
    r = await client.post("/api/dishes", json={"title": "Хак"}, headers=evil)
    assert r.status_code == 403
    ok = await client.post("/api/dishes", json={"title": "Борщ"}, headers={"Origin": BASE})
    assert ok.status_code == 201
    assert (await client.get("/api/me", headers=evil)).status_code == 200  # читати можна


async def test_bot_web_command(sessionmaker, session, fake_bot, igor) -> None:
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "/web"))
    msg = fake_bot.session.sent()[-1]
    link = await session.scalar(select(LoginLink))
    assert f"<code>{link.code}</code>" in msg.text
    button = msg.reply_markup.inline_keyboard[0][0]
    assert button.text == texts.WEB_LOGIN_BUTTON and button.url.startswith(f"{BASE}/login?t=")

    await feed(dp, fake_bot, make_message_update(10, "/start web"))  # кнопка «отримати код»
    assert "<code>" in fake_bot.session.sent()[-1].text

    await feed(dp, fake_bot, make_message_update(10, "/web", chat_type="group"))
    assert fake_bot.session.sent()[-1].text == texts.WEB_LOGIN_PRIVATE_ONLY
