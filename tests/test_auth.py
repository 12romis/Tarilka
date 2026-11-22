import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import validate_init_data
from app.services.members import add_member
from tests.conftest import BOT_TOKEN, make_init_data

USER = {"id": 42, "first_name": "Ігор", "username": "romis12"}


def test_valid_init_data() -> None:
    data = validate_init_data(make_init_data(USER, auth_date=int(time.time())), BOT_TOKEN, 3600)
    assert data.user is not None and data.user.id == 42


def test_wrong_token_rejected() -> None:
    raw = make_init_data(USER, auth_date=int(time.time()), token="999:other")
    with pytest.raises(ValueError):
        validate_init_data(raw, BOT_TOKEN, 3600)


def test_tampered_data_rejected() -> None:
    raw = make_init_data(USER, auth_date=int(time.time())).replace("romis12", "hacker")
    with pytest.raises(ValueError):
        validate_init_data(raw, BOT_TOKEN, 3600)


def test_expired_init_data_rejected() -> None:
    now = time.time()
    raw = make_init_data(USER, auth_date=int(now) - 25 * 3600)
    with pytest.raises(ValueError, match="прострочені"):
        validate_init_data(raw, BOT_TOKEN, 24 * 3600, now=now)


@pytest.fixture
async def client(sessionmaker):
    from app.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c


async def test_api_me_member(client, session) -> None:
    await add_member(session, "Ігор", tg_user_id=42, is_admin=True)
    raw = make_init_data(USER, auth_date=int(time.time()))
    r = await client.get("/api/me", headers={"Authorization": f"tma {raw}"})
    assert r.status_code == 200
    assert r.json()["name"] == "Ігор" and r.json()["is_admin"] is True


async def test_api_me_stranger_forbidden(client) -> None:
    raw = make_init_data({"id": 777, "first_name": "Чужий"}, auth_date=int(time.time()))
    r = await client.get("/api/me", headers={"Authorization": f"tma {raw}"})
    assert r.status_code == 403


async def test_api_me_without_header_unauthorized(client) -> None:
    r = await client.get("/api/me")
    assert r.status_code == 401
