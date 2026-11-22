from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import texts
from app.ai.client import AIError
from app.db.models import Dish, ImportJob, JobKind, JobStatus
from app.services.jobs import claim, enqueue
from app.services.members import add_member
from tests.conftest import FakeAI, dish_json, make_worker


async def _enqueue_text(session, member, text="борщ"):
    return await enqueue(
        session,
        family_id=1,
        kind=JobKind.text,
        payload={"text": text},
        created_by=member.id,
        tg_chat_id=member.tg_user_id,
        tg_message_id=500,
    )


async def test_claim_skip_locked_and_run_after(session, sessionmaker) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1)
    job = await _enqueue_text(session, m)
    job.run_after = datetime.now(UTC) + timedelta(minutes=5)
    await session.commit()
    async with sessionmaker() as s:
        assert await claim(s) is None  # ще не час
    job.run_after = datetime.now(UTC)
    await session.commit()
    async with sessionmaker() as s:
        claimed = await claim(s)
    assert claimed is not None and claimed.status == JobStatus.running and claimed.attempts == 1
    async with sessionmaker() as s:
        assert await claim(s) is None  # вже взято


async def test_text_job_produces_card(session, sessionmaker, fake_bot) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1)
    ai = FakeAI([{"dishes": [dish_json("Борщ", course=["soup"])]}])
    job = await _enqueue_text(session, m, "борщ")
    w = make_worker(sessionmaker, fake_bot, ai)
    assert await w.tick() == 1
    for t in list(w.tasks):
        await t
    await session.refresh(job)
    assert job.status == JobStatus.draft_ready
    d = job.result["drafts"][0]
    assert d["title"] == "Борщ" and d["cooks"] == [m.id]  # автор за замовчуванням
    assert job.result["meta"]["llm"]["model"] == "fake"
    edit = fake_bot.session.edits()[-1]
    assert edit.message_id == 500 and "🍲 Борщ" in edit.text and "вміємо" in edit.text
    assert "Ігор" in ai.calls[0][0]  # члени сім'ї в промпті


async def test_no_dishes(session, sessionmaker, fake_bot) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1)
    job = await _enqueue_text(session, m, "привіт")
    w = make_worker(sessionmaker, fake_bot, FakeAI([{"dishes": []}]))
    await w.process(job.id)
    await session.refresh(job)
    assert job.status == JobStatus.done
    assert fake_bot.session.edits()[-1].text == texts.NO_DISHES


async def test_retries_then_fails_with_save_as_is(session, sessionmaker, fake_bot) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1, is_admin=True)
    ai = FakeAI([AIError("x"), AIError("x"), AIError("x")])
    job = await _enqueue_text(session, m, "якась страва")
    w = make_worker(sessionmaker, fake_bot, ai)
    for attempt in range(3):
        async with sessionmaker() as s:
            claimed = await claim(s)
        assert claimed is not None and claimed.attempts == attempt + 1
        await w.process(claimed.id)
    await session.refresh(job)
    assert job.status == JobStatus.failed and job.attempts == 3
    last = fake_bot.session.edits()[-1]
    assert last.text == texts.PARSE_FAILED.format(reason=texts.REASON_AI)
    assert last.reply_markup.inline_keyboard[0][0].text == texts.BTN_SAVE_AS_IS
    # адміна сповіщено
    assert any(t.startswith("⚠️ Задача") for t in fake_bot.session.sent_texts())


async def test_voice_job(session, sessionmaker, fake_bot, monkeypatch) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1)
    ai = FakeAI([{"dishes": [dish_json("Вареники")]}], transcript="вареники з вишнею")
    job = await enqueue(
        session,
        family_id=1,
        kind=JobKind.voice,
        payload={"file_id": "F"},
        created_by=m.id,
        tg_chat_id=1,
        tg_message_id=7,
    )
    w = make_worker(sessionmaker, fake_bot, ai)

    downloaded = []

    async def fake_download(file_id, destination):
        downloaded.append((file_id, destination.name))

    monkeypatch.setattr(fake_bot, "download", fake_download)
    await w.process(job.id)
    await session.refresh(job)
    assert downloaded == [("F", "voice.ogg")]
    assert job.result["transcript"] == "вареники з вишнею"
    assert job.result["source_type"] == "voice"
    assert ai.calls[0][1] == "вареники з вишнею"
    assert "🎙 Почув: «вареники з вишнею»" in fake_bot.session.edits()[-1].text


async def test_autosave_after_48h(session, sessionmaker, fake_bot) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1)
    job = await _enqueue_text(session, m)
    job.status = JobStatus.draft_ready
    job.result = {
        "drafts": [
            {
                "title": "Борщ",
                "status": "want_to_try",
                "cooks": [m.id],
                "state": "pending",
                "dish_id": None,
            }
        ],
        "view": 0,
    }
    await session.commit()
    w = make_worker(sessionmaker, fake_bot, FakeAI())
    assert await w.autosave() == 0  # ще свіжа
    await session.execute(
        ImportJob.__table__.update().values(updated_at=datetime.now(UTC) - timedelta(hours=49))
    )
    await session.commit()
    assert await w.autosave() == 1
    dish = await session.scalar(select(Dish))
    assert dish.title == "Борщ" and dish.status.value == "want_to_try"
    assert fake_bot.session.edits()[-1].text.endswith(texts.AUTOSAVED)
