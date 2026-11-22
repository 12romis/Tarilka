import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.conftest import make_message_update


@pytest.fixture
async def client(sessionmaker):
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c


async def test_healthz(client) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "up", "queued": 0, "failed_24h": 0}


async def test_webhook_requires_secret(client) -> None:
    r = await client.post("/tg/webhook", json={"update_id": 1})
    assert r.status_code == 403
    r = await client.post(
        "/tg/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}
    )
    assert r.status_code == 403


async def test_webhook_accepts_valid_secret(client) -> None:
    # Чужий у групі ігнорується без звернень до Bot API, тож мережа не потрібна.
    r = await client.post(
        "/tg/webhook",
        json=make_message_update(99, "привіт", chat_type="group"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
    )
    assert r.status_code == 200
