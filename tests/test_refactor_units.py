"""Юніт-тести дрібних модулів: AI-клієнт, поля чернетки, навігація, реєстр імпортерів."""

import httpx
import pytest
from openai import APIConnectionError

from app import texts
from app.ai.client import AIError, OpenAIClient
from app.ai.schemas import TextParse
from app.config import get_settings
from app.db.models import ImportJob, JobKind, JobStatus, SourceType
from app.importers.registry import IMPORTERS
from app.services import draft_fields as fields
from app.services import drafts as dr
from app.services.jobs import enqueue
from app.services.members import add_member
from tests.conftest import FakeAI, make_worker


async def test_openai_errors_wrapped(monkeypatch) -> None:
    client = OpenAIClient(get_settings().model_copy(update={"llm_api_key": "sk-test"}))

    async def boom(**_):
        raise APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))

    monkeypatch.setattr(client._client.chat.completions, "parse", boom)
    with pytest.raises(AIError):
        await client.structured("s", "u", TextParse)


def test_tag_slugs_main_first() -> None:
    d = {"main_ingredient": "potato", "ingredient_tags": ["eggs", "potato"], "cuisine": None}
    assert fields.tag_slugs(d, "ingredient") == ["potato", "eggs"]
    assert fields.tag_slugs(d, "cuisine") == []
    assert fields.tag_slugs({}, "meal") == []


def test_raw_text_draft() -> None:
    assert fields.raw_text_draft("  ", 3) == {
        "title": "Без назви",
        "status": "can_cook",
        "cooks": [3],
    }


def test_new_result_and_navigation() -> None:
    job = ImportJob(
        result=dr.new_result(
            [{"title": "А"}, {"title": "Б"}, {"title": "В"}],
            source_text="x",
            source_type=SourceType.voice,
        )
    )
    assert job.result["view"] == dr.SUMMARY and job.result["source_type"] == "voice"
    assert dr.next_pending(job, -1) == 0
    assert dr.next_pending(job, 2) == 0  # по колу
    job.result["drafts"][1]["state"] = dr.SAVED
    assert dr.next_pending(job, 0) == 2
    for d in job.result["drafts"]:
        d["state"] = dr.SAVED
    assert dr.next_pending(job, 0) == dr.SUMMARY
    single = dr.new_result([{"title": "А"}], source_text="x", source_type=SourceType.manual)
    assert single["view"] == 0


def test_registry_covers_input_kinds() -> None:
    assert set(IMPORTERS) == {JobKind.text, JobKind.voice}


async def test_unknown_kind_fails_permanently(session, sessionmaker, fake_bot) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1, is_admin=True)
    job = await enqueue(
        session,
        family_id=1,
        kind=JobKind.url,
        payload={"url": "x"},
        created_by=m.id,
        tg_chat_id=1,
        tg_message_id=5,
    )
    await make_worker(sessionmaker, fake_bot, FakeAI()).process(job.id)
    await session.refresh(job)
    assert job.status == JobStatus.failed  # без повторів
    assert fake_bot.session.edits()[-1].text == texts.PARSE_FAILED.format(
        reason=texts.REASON_UNKNOWN
    )
    assert fake_bot.session.sent_texts() == []  # адміна не турбуємо
