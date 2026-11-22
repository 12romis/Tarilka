"""Наскрізні сценарії етапу 2: повідомлення → worker → картка → кнопки → каталог."""

import json
from datetime import UTC, datetime, timedelta

from aiogram.methods import SendDocument
from aiogram.types import Update
from sqlalchemy import select

from app import texts
from app.bot.handlers import what
from app.bot.keyboards import CookCb, DraftCb, RateCb
from app.bot.router import build_dispatcher
from app.db.models import CookLog, Dish, DishCook, DishStatus, ImportJob, JobStatus
from app.services import dishes as dish_svc
from app.services.members import add_member
from app.services.meta import load_meta
from tests.conftest import FakeAI, dish_json, make_callback_update, make_message_update
from tests.test_worker import make_worker


async def feed(dp, bot, upd: dict) -> None:
    await dp.feed_update(bot, Update.model_validate(upd, context={"bot": bot}))


async def press(dp, bot, user_id: int, cb, message_id: int) -> None:
    await feed(dp, bot, make_callback_update(user_id, cb.pack(), message_id=message_id))


async def run_worker(sessionmaker, bot, ai) -> None:
    w = make_worker(sessionmaker, bot, ai)
    await w.tick()
    for t in list(w.tasks):
        await t


async def test_bulk_add_acceptance(sessionmaker, session, fake_bot) -> None:
    """«борщ, деруни, олів'є — вміє мама» → 3 страви з правильним кухарем за 1 дотик."""
    igor = await add_member(session, "Ігор", tg_user_id=10, is_admin=True)
    mama = await add_member(session, "Зірка", tg_user_id=20, aliases=["мама"])
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "борщ, деруни, олів'є — вміє мама"))
    parsing = fake_bot.session.sent()[-1]
    assert parsing.text == texts.PARSING

    ai = FakeAI(
        [
            {
                "dishes": [
                    dish_json("Борщ", cooks=[mama.id], course=["soup"], cuisine="ukrainian"),
                    dish_json("Деруни", cooks=[mama.id], main_ingredient="potato", course=["main"]),
                    dish_json("Олів'є", cooks=[mama.id], course=["salad"], traits=["festive"]),
                ]
            }
        ]
    )
    await run_worker(sessionmaker, fake_bot, ai)
    assert "мама" in ai.calls[0][0]  # синонім передано в промпт
    card = fake_bot.session.edits()[-1]
    assert texts.FOUND_N.format(n=3) in card.text and "👤 Зірка" in card.text
    msg_id = card.message_id
    job = await session.scalar(select(ImportJob))

    await press(dp, fake_bot, 10, DraftCb(job=job.id, i=-1, a="all"), msg_id)  # 1 дотик
    dishes = list(await session.scalars(select(Dish).order_by(Dish.id)))
    assert [d.title for d in dishes] == ["Борщ", "Деруни", "Олів'є"]
    assert all(d.status == DishStatus.can_cook for d in dishes)
    cooks = set(await session.scalars(select(DishCook.member_id)))
    assert cooks == {mama.id}
    assert fake_bot.session.edits()[-1].text.startswith("✅ Збережено (3)")
    assert igor.id not in cooks


async def test_single_draft_buttons(sessionmaker, session, fake_bot) -> None:
    igor = await add_member(session, "Ігор", tg_user_id=10)
    zirka = await add_member(session, "Зірка", tg_user_id=20)
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "деруни"))
    await run_worker(sessionmaker, fake_bot, FakeAI([{"dishes": [dish_json("Деруни")]}]))
    msg_id = fake_bot.session.edits()[-1].message_id
    job = await session.scalar(select(ImportJob))
    cb = lambda a, arg="": DraftCb(job=job.id, i=0, a=a, arg=arg)  # noqa: E731

    await press(dp, fake_bot, 10, cb("tc", str(zirka.id)), msg_id)  # + Зірка
    await press(dp, fake_bot, 10, cb("tc", str(igor.id)), msg_id)  # − Ігор (автор)
    await press(dp, fake_bot, 10, cb("tt", "ingredient.potato"), msg_id)
    await press(dp, fake_bot, 10, cb("tt", "meal.dinner"), msg_id)
    await press(dp, fake_bot, 10, cb("tt", "meal.bogus"), msg_id)  # невідомий тег ігнорується
    await press(dp, fake_bot, 10, cb("st"), msg_id)
    card = fake_bot.session.edits()[-1].text
    assert "👤 Зірка" in card and "🥔 картопля" in card and "хочемо спробувати" in card

    # перейменування через ForceReply
    await press(dp, fake_bot, 10, cb("ren"), msg_id)
    prompt = fake_bot.session.sent()[-1]
    assert prompt.text.startswith("✏️ Нова назва")
    prompt_id = fake_bot.session.message_ids[-1]
    upd = make_message_update(10, "Деруни з грибами")
    upd["message"]["reply_to_message"] = {
        "message_id": prompt_id,
        "date": 0,
        "chat": {"id": 10, "type": "private"},
        "from": {"id": fake_bot.id, "is_bot": True, "first_name": "Tarilka"},
        "text": prompt.text,
    }
    await feed(dp, fake_bot, upd)
    assert fake_bot.session.sent_texts()[-1] == texts.RENAMED.format(title="Деруни з грибами")
    assert not any(m.text == texts.PARSING for m in fake_bot.session.sent()[-2:])

    await press(dp, fake_bot, 10, cb("save"), msg_id)
    dish = await session.scalar(select(Dish))
    assert dish.title == "Деруни з грибами" and dish.status == DishStatus.want_to_try
    meta = await load_meta(session, 1)
    assert dish.main_ingredient_id == meta.tag("ingredient", "potato").id
    assert list(await session.scalars(select(DishCook.member_id))) == [zirka.id]


async def test_discard(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10)
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "щось"))
    await run_worker(sessionmaker, fake_bot, FakeAI([{"dishes": [dish_json("Щось")]}]))
    msg_id = fake_bot.session.edits()[-1].message_id
    job = await session.scalar(select(ImportJob))
    await press(dp, fake_bot, 10, DraftCb(job=job.id, i=0, a="del"), msg_id)
    await session.refresh(job)
    assert job.status == JobStatus.done
    assert fake_bot.session.edits()[-1].text == texts.DRAFT_DISCARDED
    assert await session.scalar(select(Dish)) is None


async def test_foreign_family_job_rejected(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10)
    dp = build_dispatcher(sessionmaker, [])
    await press(dp, fake_bot, 10, DraftCb(job=999, i=0, a="save"), 1)
    assert await session.scalar(select(Dish)) is None


async def _seed(session, meta, titles, **kw):
    return [
        await dish_svc.create_from_draft(session, meta, 1, {"title": t, **kw}, created_by=None)
        for t in titles
    ]


async def test_what_skips_cooked_yesterday_and_cook_flow(sessionmaker, session, fake_bot) -> None:
    m = await add_member(session, "Ігор", tg_user_id=10)
    meta = await load_meta(session, 1)
    dishes = await _seed(session, meta, ["Борщ", "Деруни", "Плов", "Омлет"])
    dishes[0].last_cooked_at = datetime.now(UTC) - timedelta(days=1)
    await session.commit()
    dp = build_dispatcher(sessionmaker, [])
    for _ in range(10):
        await feed(dp, fake_bot, make_message_update(10, "/what"))
        reply = fake_bot.session.sent()[-1]
        assert reply.text.startswith("🎲 Що приготувати")
        assert "Борщ" not in reply.text
        assert reply.text.count("\n1. ") == 1 and "\n3. " in reply.text
    assert texts.WHAT_EMPTY_DB.strip() in reply.text  # страв < 10 → заклик додати

    # «Готую!» → cook_log + запит оцінки → оцінка
    plov = next(d for d in dishes if d.title == "Плов")
    await press(dp, fake_bot, 10, CookCb(dish=plov.id), 1)
    assert fake_bot.session.sent_texts()[-1] == texts.RATE_PROMPT.format(title="Плов")
    await press(dp, fake_bot, 10, RateCb(dish=plov.id, n=5), 2)
    await session.refresh(plov)
    assert plov.rating == 5 and plov.cooked_count == 1
    log = await session.scalar(select(CookLog))
    assert log.dish_id == plov.id and log.member_id == m.id


async def test_what_text_trigger_and_more(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10)
    meta = await load_meta(session, 1)
    await _seed(session, meta, [f"Страва {n}" for n in range(6)])
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "Що приготувати?"))
    first = fake_bot.session.sent()[-1]
    assert first.text.startswith("🎲")
    assert await session.scalar(select(ImportJob)) is None  # не пішло в розбір
    more = first.reply_markup.inline_keyboard[-1][-1]
    assert more.text == texts.WHAT_MORE
    first_id = fake_bot.session.message_ids[-1]
    shown_first = set(what.shown.get(10, first_id))
    await feed(dp, fake_bot, make_callback_update(10, more.callback_data, message_id=first_id))
    shown_all = what.shown.get(10, first_id)
    assert len(shown_all) == 6 and set(shown_all[:3]) == shown_first  # нові 3 — інші


async def test_find_and_cooked(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10)
    meta = await load_meta(session, 1)
    await _seed(session, meta, ["Деруни з грибами", "Борщ"])
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "/find деруни"))
    reply = fake_bot.session.sent()[-1]
    assert "Деруни з грибами" in reply.text and "Борщ" not in reply.text
    await feed(dp, fake_bot, make_message_update(10, "/find піца"))
    assert fake_bot.session.sent_texts()[-1] == texts.FIND_NOTHING.format(q="піца")
    await feed(dp, fake_bot, make_message_update(10, "/cooked борщ"))
    btn = fake_bot.session.sent()[-1].reply_markup.inline_keyboard[0][0]
    assert btn.text == texts.BTN_COOKED.format(title="Борщ")


async def test_export_admin_only(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10, is_admin=True)
    await add_member(session, "Зірка", tg_user_id=20)
    meta = await load_meta(session, 1)
    await _seed(session, meta, ["Борщ"])
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(20, "/export"))
    assert fake_bot.session.sent_texts()[-1] == texts.ADMIN_ONLY
    await feed(dp, fake_bot, make_message_update(10, "/export"))
    doc = fake_bot.session.of(SendDocument)[-1]
    data = json.loads(doc.document.data)
    assert [d["title"] for d in data["dishes"]] == ["Борщ"] and len(data["tags"]) == 40


async def test_group_only_when_addressed(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10)
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "борщ", chat_type="group"))
    assert await session.scalar(select(ImportJob)) is None
    await feed(dp, fake_bot, make_message_update(10, "@tarilka_test_bot борщ", chat_type="group"))
    job = await session.scalar(select(ImportJob))
    assert job is not None and job.payload["text"] == "борщ"


async def test_failed_job_save_as_is(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10)
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "бабусині пиріжки"))
    job = await session.scalar(select(ImportJob))
    job.status = JobStatus.failed
    await session.commit()
    await press(dp, fake_bot, 10, DraftCb(job=job.id, i=-1, a="asis"), 1)
    dish = await session.scalar(select(Dish))
    assert dish.title == "бабусині пиріжки"
