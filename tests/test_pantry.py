"""Холодильник: наявність продуктів, що приготувати з наявного, зв'язок зі списком покупок."""

import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app import texts
from app.ai.client import AIError, default_ai
from app.bot.handlers.pantry import pantry_request
from app.bot.router import build_dispatcher
from app.db.models import ImportJob, JobKind, PantryItem, Product, ShoppingItem
from app.main import create_app
from app.services import dishes as dish_svc
from app.services import pantry, shopping
from app.services.jobs import enqueue
from app.services.members import add_member
from app.services.meta import load_meta
from tests.conftest import FakeAI, feed, make_init_data, make_message_update, make_worker


def test_pantry_request() -> None:
    assert pantry_request("Є вдома: яйця, сир") == ("add", "яйця, сир")
    assert pantry_request("у холодильнику є молоко") == ("add", "молоко")
    assert pantry_request("Закінчилось молоко.") == ("remove", "молоко")
    assert pantry_request("закінчився хліб") == ("remove", "хліб")
    assert pantry_request("борщ") is None


def ing(*names: str) -> list[dict]:
    return [{"name": n, "qty": None, "unit": None} for n in names]


@pytest.fixture
async def ctx(session):
    igor = await add_member(session, "Ігор", tg_user_id=10, is_admin=True)
    # «зазвичай є вдома» і «не купують» — заздалегідь, щоб обійтися без AI
    session.add_all(
        [
            Product(family_id=1, name="сіль", aisle="spices", staple=True),
            Product(family_id=1, name="вода", skip=True),
        ]
    )
    await session.commit()
    meta = await load_meta(session, 1)

    async def mk(title, names, **kw):
        draft = {"title": title, "ingredients": ing(*names)} | kw
        return await dish_svc.create_from_draft(session, meta, 1, draft, created_by=igor.id)

    dishes = {
        "omelet": await mk("Омлет", ["Яйця", "молоко", "сіль"]),
        "fried": await mk("Яєчня", ["яйця", "сіль", "вода"]),
        "lasagna": await mk("Лазанья", ["яйця", "фарш", "сир", "тісто", "томати"]),
        "salad": await mk("Салат", ["огірки", "помідори"]),
        "old": await mk("Стара яєчня", ["яйця"], status="archived"),
    }
    return {"igor": igor, **dishes}


async def names_in_pantry(session) -> list[str]:
    return [p.name for p in await pantry.load(session, 1)]


async def test_add_remove_text_without_ai(session, ctx) -> None:
    added = await pantry.add_text(session, None, ctx["igor"], "яйця, сир\n вода")
    assert [p.name for p in added] == ["яйця", "сир"]  # воду не кладемо
    await pantry.add_text(session, None, ctx["igor"], "яйця")  # повторно — без дубля
    assert sorted(await names_in_pantry(session)) == ["сир", "яйця"]
    assert await pantry.remove_text(session, None, ctx["igor"], "сир, хліб") == ["сир"]
    assert await names_in_pantry(session) == ["яйця"]


async def test_add_text_with_ai_groups_by_aisle(session, ctx) -> None:
    item = {"qty": None, "unit": None, "staple": False, "skip": False}
    ai = FakeAI(
        [
            {
                "items": [
                    {**item, "product": "сир", "aisle": "dairy"},
                    {**item, "product": "огірки", "aisle": "produce"},
                ]
            }
        ]
    )
    await pantry.add_text(session, ai, ctx["igor"], "сир і пару огірків")
    assert await names_in_pantry(session) == ["огірки", "сир"]  # овочі перед молочним


async def test_cook_options(session, ctx) -> None:
    assert await pantry.cook_options(session, None, 1) == []  # порожній холодильник
    await pantry.add_text(session, None, ctx["igor"], "яйця")
    options = await pantry.cook_options(session, None, 1)
    by_title = {o.title: o for o in options}
    # сіль — «зазвичай є», вода — не купують; Лазаньї бракує 4 > MAX_MISSING; Салат — нічого
    # спільного; архівна — ні
    assert [o.title for o in options] == ["Яєчня", "Омлет"]
    assert by_title["Яєчня"].missing == [] and by_title["Яєчня"].have == ["яйця"]
    assert by_title["Омлет"].missing == ["молоко"]


async def test_shopping_list_knows_pantry(session, ctx) -> None:
    await pantry.add_text(session, None, ctx["igor"], "яйця")
    await shopping.add_dish(session, None, ctx["omelet"], ctx["igor"])
    view = await shopping.load_view(session, 1)
    lines = {line.name: line for line in view.lines}
    assert lines["яйця"].at_home and not lines["молоко"].at_home
    assert view.open_count == 2  # молоко і сіль; яйця вже є

    # куплене — у холодильник; зняв відмітку — прибирається
    milk = lines["молоко"].product_id
    await shopping.set_checked(session, 1, milk, True, ctx["igor"])
    assert "молоко" in await names_in_pantry(session)
    await shopping.set_checked(session, 1, milk, False, ctx["igor"])
    assert "молоко" not in await names_in_pantry(session)

    # те, що лежало в холодильнику раніше, зняття відмітки не чіпає
    eggs = lines["яйця"].product_id
    await shopping.set_checked(session, 1, eggs, True, ctx["igor"])
    await shopping.set_checked(session, 1, eggs, False, ctx["igor"])
    assert "яйця" in await names_in_pantry(session)


def tma(user_id: int) -> dict[str, str]:
    raw = make_init_data({"id": user_id, "first_name": "T"}, auth_date=int(time.time()))
    return {"Authorization": f"tma {raw}"}


async def test_api(sessionmaker, session, ctx) -> None:
    app = create_app()
    app.dependency_overrides[default_ai] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/pantry/items", json={"text": "яйця, сир"}, headers=tma(10))
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["count"] == 2 and data["aisles"][0]["slug"] == "other"

        cook = (await c.get("/api/pantry/cook", headers=tma(10))).json()
        assert [o["title"] for o in cook] == ["Яєчня", "Омлет", "Лазанья"]  # бракує рівно 3
        assert cook[2]["missing"] == ["фарш", "тісто", "томати"] and cook[0]["thumb"] is None

        cheese = next(i for i in data["aisles"][0]["items"] if i["name"] == "сир")
        r = await c.delete(
            f"/api/pantry/products/{cheese['product_id']}?to_list=true", headers=tma(10)
        )
        assert r.json()["count"] == 1
        listed = (await c.get("/api/shopping", headers=tma(10))).json()
        assert [x["name"] for x in listed["aisles"][0]["lines"]] == ["сир"]

        r = await c.post(f"/api/pantry/products/{cheese['product_id']}", headers=tma(10))
        assert r.json()["count"] == 2
        listed = (await c.get("/api/shopping", headers=tma(10))).json()
        assert [x["name"] for x in listed["at_home"]] == ["сир"] and listed["open_count"] == 0
        assert (await c.post("/api/pantry/products/9999", headers=tma(10))).status_code == 404


async def test_bot(sessionmaker, session, fake_bot, ctx) -> None:
    dp = build_dispatcher(sessionmaker, [])  # без AI: ділить за комами
    await feed(dp, fake_bot, make_message_update(10, "Є вдома: яйця, <сир>"))
    reply = fake_bot.session.sent()[-1]
    assert reply.text == texts.PANTRY_ADDED.format(names="яйця, &lt;сир&gt;")
    assert reply.reply_markup.inline_keyboard[0][0].web_app.url.endswith("/fridge")
    assert await session.scalar(select(ImportJob)) is None  # не пішло в розбір страв

    await feed(dp, fake_bot, make_message_update(10, "закінчилось <сир>"))
    assert fake_bot.session.sent()[-1].text == texts.PANTRY_REMOVED.format(names="&lt;сир&gt;")
    await feed(dp, fake_bot, make_message_update(10, "закінчилось молоко"))
    assert fake_bot.session.sent()[-1].text == texts.PANTRY_NOT_THERE

    await feed(dp, fake_bot, make_message_update(10, "/fridge"))
    text = fake_bot.session.sent()[-1].text
    assert text.startswith("🧊 <b>Вдома є</b> (1)") and "• яйця" in text
    assert "• Яєчня — є все" in text and "• Омлет — бракує: молоко" in text


async def test_voice_goes_to_shopping_list(session, sessionmaker, fake_bot, ctx) -> None:
    job = await enqueue(
        session,
        family_id=1,
        kind=JobKind.voice,
        payload={"file_id": "F"},
        created_by=ctx["igor"].id,
        tg_chat_id=10,
        tg_message_id=7,
    )

    async def fake_download(file_id, destination):
        pass

    fake_bot.download = fake_download
    w = make_worker(sessionmaker, fake_bot, FakeAI([AIError("down")], transcript="Купити хліб"))
    await w.process(job.id)
    await session.refresh(job)
    assert job.status.value == "done" and job.result["transcript"] == "Купити хліб"
    edit = fake_bot.session.edits()[-1]
    assert edit.text.startswith("🎙 <i>Купити хліб</i>") and "Додав у список: хліб" in edit.text
    assert await session.scalar(select(func.count()).select_from(ShoppingItem)) == 1
    assert await session.scalar(select(func.count()).select_from(PantryItem)) == 0
