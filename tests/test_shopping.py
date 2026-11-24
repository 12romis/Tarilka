"""Список покупок: канонізація продуктів, зведення кількостей, API і бот."""

import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app import texts
from app.ai.client import AIError, default_ai
from app.bot.handlers.shopping import fmt_qty, shopping_request
from app.bot.router import build_dispatcher
from app.db.models import ImportJob, IngredientProduct, Product, ShoppingItem
from app.main import create_app
from app.services import dishes as dish_svc
from app.services import shopping
from app.services.members import add_member
from app.services.meta import load_meta
from tests.conftest import FakeAI, feed, make_init_data, make_message_update


def canon(i: int, product: str, aisle: str = "other", staple=False, skip=False) -> dict:
    return {"i": i, "product": product, "aisle": aisle, "staple": staple, "skip": skip}


def test_sum_parts() -> None:
    parts, no_qty = shopping.sum_parts(
        [(3, "шт."), (2, None), (0.5, "кг"), (700, "г"), (None, None), (1, "ст. л.")]
    )
    assert parts == [(5, "шт."), (1.2, "кг"), (1, "ст. л.")] and no_qty
    assert shopping.sum_parts([(200, "мл"), (0.3, "л")])[0] == [(500, "мл")]


def test_uk_sort() -> None:
    names = ["яйця", "італійські трави", "ґрунт", "борошно", "їжак"]
    assert sorted(names, key=shopping.uk_sort_key) == [
        "борошно",
        "ґрунт",
        "італійські трави",
        "їжак",
        "яйця",
    ]


def test_shopping_request_and_fmt() -> None:
    assert shopping_request("Купити: молоко, хліб") == "молоко, хліб"
    assert shopping_request("треба купити яйця") == "яйця"
    assert shopping_request("купи сир") == "сир"
    assert shopping_request("купити") == ""
    assert shopping_request("борщ, деруни") is None
    assert (fmt_qty(1.5), fmt_qty(0.25), fmt_qty(2), fmt_qty(1.2)) == ("1½", "¼", "2", "1,2")


@pytest.fixture
async def ctx(session):
    igor = await add_member(session, "Ігор", tg_user_id=10, is_admin=True)
    meta = await load_meta(session, 1)

    async def mk(title, ingredients, servings=None):
        return await dish_svc.create_from_draft(
            session,
            meta,
            1,
            {"title": title, "ingredients": ingredients, "servings": servings},
            created_by=igor.id,
        )

    lasagna = await mk(
        "Лазанья",
        [
            {"name": "Куряче яйце", "qty": 2, "unit": "шт."},
            {"name": "фарш", "qty": 0.6, "unit": "кг"},
            {"name": "сіль", "qty": None, "unit": None},
            {"name": "вода", "qty": 200, "unit": "мл"},
        ],
        servings=4,
    )
    omelet = await mk(
        "Омлет",
        [{"name": "яйця", "qty": 3, "unit": None}, {"name": "фарш", "qty": 100, "unit": "г"}],
    )
    return {"igor": igor, "lasagna": lasagna, "omelet": omelet}


def lasagna_ai() -> dict:
    return {
        "items": [
            canon(0, "Яйця", "dairy"),
            canon(1, "фарш", "meat"),
            canon(2, "сіль", "spices", staple=True),
            canon(3, "вода", skip=True),
        ]
    }


async def test_add_dishes_aggregates_and_caches(session, ctx) -> None:
    ai = FakeAI([lasagna_ai(), {"items": []}])
    # Лазанья на 8 порцій замість 4: кількості ×2; воду не купують
    assert await shopping.add_dish(session, ai, ctx["lasagna"], ctx["igor"], servings=8) == 3
    assert "0. куряче яйце" in ai.calls[0][1]

    # Омлет: «яйця» і «фарш» уже відомі як продукти, але назви нові → один виклик AI,
    # відповідь порожня → fallback на ту саму назву, і вона збігається з відомим продуктом
    await shopping.add_dish(session, ai, ctx["omelet"], ctx["igor"])
    view = await shopping.load_view(session, 1)
    by_name = {line.name: line for line in view.lines}
    assert by_name["яйця"].parts == [(7, "шт.")]  # 2×2 + 3 (без одиниці → шт.)
    assert by_name["фарш"].parts == [(1.3, "кг")]  # 1,2 кг + 100 г
    assert by_name["яйця"].sources == ["Лазанья", "Омлет"]
    assert by_name["сіль"].staple and by_name["сіль"].no_qty
    assert "вода" not in by_name
    assert [line.aisle for line in view.lines] == ["meat", "dairy", "spices"]  # порядок відділів
    assert {d.title: d.servings for d in view.dishes} == {"Лазанья": 8, "Омлет": None}

    # повторне додавання не кличе AI (кеш) і замінює внесок страви
    await shopping.add_dish(session, FakeAI([]), ctx["lasagna"], ctx["igor"], servings=4)
    view = await shopping.load_view(session, 1)
    assert {line.name: line.parts for line in view.lines}["яйця"] == [(5, "шт.")]


async def test_ai_failure_falls_back_without_caching(session, ctx) -> None:
    await shopping.add_dish(session, FakeAI([AIError("down")]), ctx["omelet"], ctx["igor"])
    names = {line.name for line in (await shopping.load_view(session, 1)).lines}
    assert names == {"яйця", "фарш"}
    assert await session.scalar(select(func.count()).select_from(IngredientProduct)) == 0
    assert await shopping.add_dish(session, None, ctx["omelet"], ctx["igor"]) == 2  # і без AI


async def test_check_and_clear(session, ctx) -> None:
    await shopping.add_dish(session, FakeAI([lasagna_ai()]), ctx["lasagna"], ctx["igor"])
    eggs = await session.scalar(select(Product).where(Product.name == "яйця"))
    await shopping.set_checked(session, 1, eggs.id, True, ctx["igor"])
    view = await shopping.load_view(session, 1)
    assert [line.name for line in view.lines if line.checked] == ["яйця"]
    assert view.open_count == 2
    await shopping.clear(session, 1, only_checked=True)
    assert {line.name for line in (await shopping.load_view(session, 1)).lines} == {"фарш", "сіль"}
    await shopping.remove_dish(session, 1, ctx["lasagna"].id)
    assert (await shopping.load_view(session, 1)).lines == []


async def test_add_text(session, ctx) -> None:
    ai = FakeAI(
        [
            {
                "items": [
                    {**canon(0, "молоко", "dairy"), "qty": 2, "unit": "л"},
                    {**canon(0, "хліб", "bakery"), "qty": None, "unit": None},
                ]
            }
        ]
    )
    assert await shopping.add_text(session, ai, ctx["igor"], "молоко 2 л і хліб") == 2
    assert await shopping.add_text(session, None, ctx["igor"], "сир, \n банани") == 2
    view = await shopping.load_view(session, 1)
    lines = {line.name: line for line in view.lines}
    assert lines["молоко"].parts == [(2, "л")] and lines["молоко"].sources == ["Ігор"]
    assert set(lines) == {"молоко", "хліб", "сир", "банани"}


def tma(user_id: int) -> dict[str, str]:
    raw = make_init_data({"id": user_id, "first_name": "T"}, auth_date=int(time.time()))
    return {"Authorization": f"tma {raw}"}


async def test_api(sessionmaker, session, ctx) -> None:
    app = create_app()
    ai = FakeAI([lasagna_ai()])
    app.dependency_overrides[default_ai] = lambda: ai
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/shopping/dishes",
            json={"dish_id": ctx["lasagna"].id, "servings": 2},
            headers=tma(10),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert [a["slug"] for a in data["aisles"]] == ["meat", "dairy"]
        meat = data["aisles"][0]["lines"][0]
        assert meat["name"] == "фарш" and meat["amounts"] == [{"qty": 300.0, "unit": "г"}]
        assert [s["name"] for s in data["staples"]] == ["сіль"] and data["open_count"] == 3

        pid = data["aisles"][1]["lines"][0]["product_id"]
        r = await c.patch(f"/api/shopping/products/{pid}", json={"checked": True}, headers=tma(10))
        assert [x["name"] for x in r.json()["checked"]] == ["яйця"]
        r = await c.delete("/api/shopping", headers=tma(10))  # лише куплене
        assert r.json()["checked"] == [] and r.json()["open_count"] == 2

        missing = await c.post("/api/shopping/dishes", json={"dish_id": 999}, headers=tma(10))
        assert missing.status_code == 404
        r = await c.delete("/api/shopping?only_checked=false", headers=tma(10))
        assert r.json()["open_count"] == 0


async def test_bot_buy_and_list(sessionmaker, session, fake_bot, ctx) -> None:
    dp = build_dispatcher(sessionmaker, [])  # без AI: ділить за комами
    await feed(dp, fake_bot, make_message_update(10, "Купити: молоко, хліб"))
    reply = fake_bot.session.sent()[-1]
    assert reply.text == texts.SHOPPING_ADDED.format(names="молоко, хліб", n=2)
    assert reply.reply_markup.inline_keyboard[0][0].web_app.url.endswith("/shopping")
    assert await session.scalar(select(ImportJob)) is None  # не пішло в розбір страв
    assert await session.scalar(select(func.count()).select_from(ShoppingItem)) == 2

    await feed(dp, fake_bot, make_message_update(10, "/list"))
    text = fake_bot.session.sent()[-1].text
    assert text.startswith("🛒 <b>Список покупок</b> (2)") and "• молоко" in text
