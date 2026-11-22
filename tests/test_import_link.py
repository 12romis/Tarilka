"""Етап 3: посилання й фото. Мережа — httpx.MockTransport, LLM — FakeAI, Telegram — FakeSession."""

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app import texts
from app.bot.keyboards import DraftCb
from app.bot.router import build_dispatcher
from app.db.models import Dish, DishImage, ImportJob, JobKind, JobStatus, SourceType
from app.importers import video
from app.importers.urls import (
    author_from_url,
    clean_url,
    find_urls,
    normalize_url,
    source_type_of,
    strip_urls,
)
from app.importers.video import raw_from_info
from app.importers.web import extract_from_html
from app.services import dishes as dish_svc
from app.services.members import add_member
from app.services.meta import load_meta
from tests.conftest import (
    FakeAI,
    dish_json,
    feed,
    make_message_update,
    press,
    run_worker,
)

FIX = Path(__file__).parent / "fixtures"
RECIPE_URL = "https://recipes.test/deruny/"
ARTICLE_URL = "https://journal.test/bilyi-borshch"
TT_SHORT = "https://vt.tiktok.com/ZSsRkfuqw/"
TT_FULL = "https://www.tiktok.com/@zoshyt.receptiv/video/7237032357616782598"


def jpeg(color: str = "orange") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(buf, "JPEG")
    return buf.getvalue()


def mock_http() -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url == RECIPE_URL:
            return httpx.Response(
                200,
                text=(FIX / "recipe_jsonld.html").read_text(),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if url == ARTICLE_URL:
            return httpx.Response(
                200,
                text=(FIX / "article_no_recipe.html").read_text(),
                headers={"content-type": "text/html"},
            )
        if url.endswith(".jpg"):
            return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
        if url.startswith(TT_SHORT):
            return httpx.Response(301, headers={"location": TT_FULL + "?_r=1&_t=ZS-9"})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.fixture
def no_ytdlp(monkeypatch):
    """TikTok/YouTube з IP сервера: yt-dlp падає, oEmbed порожній (як у проді без cookies)."""

    def fail(*_a, **_k):
        raise RuntimeError("Unexpected response from webpage request")

    monkeypatch.setattr(video, "_ytdlp_info", fail)


# --- чисті функції -----------------------------------------------------------------------------


def test_urls_helpers() -> None:
    text = "дивись https://vt.tiktok.com/ZSsRkfuqw/ і https://site.ua/r?utm_source=x&id=5."
    assert find_urls(text) == [
        "https://vt.tiktok.com/ZSsRkfuqw/",
        "https://site.ua/r?utm_source=x&id=5",
    ]
    assert strip_urls(text) == "дивись і"
    assert clean_url(TT_FULL + "?_r=1&_t=x") == TT_FULL
    assert clean_url("https://site.ua/r?utm_source=x&id=5#top") == "https://site.ua/r?id=5"
    assert (
        clean_url("https://www.instagram.com/reel/ABC?igsh=1")
        == "https://www.instagram.com/reel/ABC/"
    )
    assert source_type_of(TT_FULL) == SourceType.tiktok
    assert source_type_of("https://m.youtube.com/shorts/x") == SourceType.youtube
    assert source_type_of(RECIPE_URL) == SourceType.web
    assert author_from_url(TT_FULL) == "@zoshyt.receptiv"


async def test_normalize_short_link() -> None:
    async with mock_http() as http:
        assert await normalize_url(http, TT_SHORT) == TT_FULL


def test_extract_schema_recipe() -> None:
    raw = extract_from_html((FIX / "recipe_jsonld.html").read_text(), RECIPE_URL, 12000)
    assert raw.structured is not None
    assert raw.structured["ingredients"][0] == "1 кг картоплі"
    assert len(raw.structured["steps"]) == 3
    assert raw.image_urls == ["https://recipes.test/img/deruny.jpg"]
    assert raw.text_blocks == []  # текст сторінки не потрібен, коли є рецепт


def test_extract_article_text_fallback() -> None:
    raw = extract_from_html((FIX / "article_no_recipe.html").read_text(), ARTICLE_URL, 12000)
    assert raw.structured is None
    assert "хрін" in raw.text_blocks[0]
    assert raw.title == "Білий борщ"
    assert raw.image_urls == ["https://journal.test/img/bilyi-borshch.jpg"]


def test_tiktok_prefers_author_cover() -> None:
    info = {
        "thumbnail": "https://cdn.test/origin.jpg",
        "thumbnails": [
            {"id": "dynamicCover", "preference": -2, "url": "https://cdn.test/dynamic.webp"},
            {"id": "cover", "preference": -1, "url": "https://cdn.test/cover.jpg"},
            {"id": "originCover", "preference": -1, "url": "https://cdn.test/origin.jpg"},
        ],
    }
    assert video.cover_urls(info) == ["https://cdn.test/cover.jpg", "https://cdn.test/origin.jpg"]
    # Instagram/YouTube: лише thumbnail або thumbnails без id — порядок за preference
    assert video.cover_urls({"thumbnail": "https://cdn.test/t.jpg"}) == ["https://cdn.test/t.jpg"]


def test_author_handle() -> None:
    tiktok = {
        "uploader": "mil_alexx_pp",
        "uploader_id": "7017077935286977541",
        "channel": "Алекс🍕",
        "uploader_url": "https://www.tiktok.com/@mil_alexx_pp",
    }
    insta = {
        "uploader": "ANNA SHOLKOVA | РЕЦЕПТИ",
        "uploader_id": "5051673073",
        "channel": "_anna_sholkova_",
    }
    assert raw_from_info(TT_FULL, SourceType.tiktok, tiktok).source_author == "@mil_alexx_pp"
    assert raw_from_info("x", SourceType.instagram, insta).source_author == "@_anna_sholkova_"
    assert raw_from_info("x", SourceType.instagram, {"uploader_id": "123"}).source_author is None


def test_ytdlp_info_mapping() -> None:
    raw = raw_from_info(
        TT_FULL,
        SourceType.tiktok,
        {
            "title": "Video by someone",
            "description": "Сирники 🧀 Інгредієнти: сир 400 г",
            "uploader": "anna",
            "thumbnail": "https://cdn.test/t.jpg",
        },
    )
    assert raw.title is None and raw.text_blocks == ["Сирники 🧀 Інгредієнти: сир 400 г"]
    assert raw.source_author == "@anna" and raw.image_urls == ["https://cdn.test/t.jpg"]


# --- наскрізно: бот → worker → картка ------------------------------------------------------------


async def _send(dp, bot, text: str, user: int = 10) -> None:
    await feed(dp, bot, make_message_update(user, text))


async def test_site_recipe_import(sessionmaker, session, fake_bot, tmp_path) -> None:
    await add_member(session, "Ігор", tg_user_id=10)
    dp = build_dispatcher(sessionmaker, [])
    await _send(dp, fake_bot, f"{RECIPE_URL}?utm_source=tg")
    job = await session.scalar(select(ImportJob))
    assert job.kind == JobKind.url and job.payload["text"] == ""
    ai = FakeAI(
        [
            {
                "dishes": [
                    dish_json(
                        "Деруни",
                        main_ingredient="potato",
                        status="want_to_try",
                        ingredients=[{"name": "картопля", "qty": 1, "unit": "кг"}],
                        steps=["Натерти", "Смажити"],
                        has_recipe=True,
                    )
                ]
            }
        ]
    )
    await run_worker(sessionmaker, fake_bot, ai, http=mock_http(), media_dir=str(tmp_path))
    assert "1 кг картоплі" in ai.calls[0][1]  # структурований рецепт пішов у LLM
    card = fake_bot.session.last_card_text()
    assert "Деруни" in card and "🔗 recipes.test" in card
    await session.refresh(job)
    d = job.result["drafts"][0]
    assert d["source_url"] == RECIPE_URL and d["cooks"] == []  # з посилання кухаря не вгадуємо
    assert len(d["steps"]) == 3  # кроки з schema.org дослівно, а не стиснуті LLM
    assert len(d["images"]) == 1 and (tmp_path / d["images"][0]["path"]).is_file()

    await press(dp, fake_bot, 10, DraftCb(job=job.id, i=0, a="save"), job.tg_message_id)
    dish = await session.scalar(select(Dish))
    assert dish.source_url == RECIPE_URL and dish.source_type == SourceType.web
    img = await session.scalar(select(DishImage))
    assert img.is_cover and img.dish_id == dish.id
    assert fake_bot.session.last_card_text().startswith("✅ Збережено")  # підпис фото-картки

    # те саме посилання вдруге — одразу наявна страва, без задачі
    await _send(dp, fake_bot, RECIPE_URL)
    assert fake_bot.session.sent_texts()[-1].startswith(texts.ALREADY_HAVE_LINK)
    assert await session.scalar(select(ImportJob).where(ImportJob.id != job.id)) is None


async def test_tiktok_blocked_uses_comment(sessionmaker, session, fake_bot, no_ytdlp) -> None:
    zirka = await add_member(session, "Зірка", tg_user_id=10)
    dp = build_dispatcher(sessionmaker, [])
    await _send(dp, fake_bot, f"{TT_SHORT} лінивий хачапурі, Зірка вже готувала")
    ai = FakeAI([{"dishes": [dish_json("Лінивий хачапурі", cooks=[zirka.id])]}])
    await run_worker(sessionmaker, fake_bot, ai, http=mock_http())
    prompt = ai.calls[0][1]
    assert "Коментар користувача: лінивий хачапурі, Зірка вже готувала" in prompt
    assert "@zoshyt.receptiv" in prompt
    job = await session.scalar(select(ImportJob))
    d = job.result["drafts"][0]
    assert d["title"] == "Лінивий хачапурі" and d["source_url"] == TT_FULL
    assert job.result["meta"]["video"]["cookies"] is False
    assert "TikTok @zoshyt.receptiv" in fake_bot.session.last_card_text()


async def test_tiktok_blocked_no_comment_link_only(
    sessionmaker, session, fake_bot, no_ytdlp
) -> None:
    await add_member(session, "Ігор", tg_user_id=10)
    dp = build_dispatcher(sessionmaker, [])
    await _send(dp, fake_bot, TT_SHORT)
    ai = FakeAI([])
    await run_worker(sessionmaker, fake_bot, ai, http=mock_http())
    assert ai.calls == []  # без даних LLM не кличемо
    job = await session.scalar(select(ImportJob))
    d = job.result["drafts"][0]
    assert d["title"] == "Відео з TikTok @zoshyt.receptiv"
    assert d["status"] == "want_to_try" and d["source_url"] == TT_FULL
    card = fake_bot.session.edits()[-1]
    buttons = [b for row in card.reply_markup.inline_keyboard for b in row]
    assert any(b.url == TT_FULL for b in buttons)  # кнопка «🔗 Відкрити»


async def test_photo_with_blocked_link(
    sessionmaker, session, fake_bot, tmp_path, no_ytdlp, monkeypatch
) -> None:
    igor = await add_member(session, "Ігор", tg_user_id=10)

    async def fake_download(file_id, destination):
        Path(destination).write_bytes(jpeg("green"))  # noqa: ASYNC240 — тестовий фейк

    monkeypatch.setattr(fake_bot, "download", fake_download)
    dp = build_dispatcher(sessionmaker, [])
    upd = make_message_update(10, "x")
    msg = upd["message"]
    del msg["text"]
    msg["photo"] = [{"file_id": "P", "file_unique_id": "p", "width": 800, "height": 600}]
    msg["caption"] = TT_SHORT
    await feed(dp, fake_bot, upd)
    job = await session.scalar(select(ImportJob))
    assert job.kind == JobKind.photo
    # посилання «мовчить» і коментаря немає → LLM для посилання не кличемо, лише vision
    ai = FakeAI([{"kind": "dish", "dishes": [dish_json("Сирники", course=["dessert"])]}])
    await run_worker(sessionmaker, fake_bot, ai, http=mock_http(), media_dir=str(tmp_path))
    await session.refresh(job)
    d = job.result["drafts"][0]
    assert d["title"] == "Сирники" and d["source_url"] == TT_FULL
    assert d["cooks"] == [igor.id]  # фото готової страви — приготував автор
    assert d["images"][0]["origin"] == "upload"
    assert fake_bot.session.last_card_text().startswith("🍰 Сирники")  # фото-картка
    assert Image.open(io.BytesIO(ai.images[0])).size == (800, 600)  # ≤ 1024 — без змін
    assert job.result["meta"]["cost_usd"] == 0.001


async def test_discard_deletes_images(sessionmaker, session, fake_bot, media_dir) -> None:
    tmp_path = media_dir
    await add_member(session, "Ігор", tg_user_id=10)
    dp = build_dispatcher(sessionmaker, [])
    await _send(dp, fake_bot, RECIPE_URL)
    ai = FakeAI([{"dishes": [dish_json("Деруни")]}])
    await run_worker(sessionmaker, fake_bot, ai, http=mock_http(), media_dir=str(tmp_path))
    job = await session.scalar(select(ImportJob))
    path = tmp_path / job.result["drafts"][0]["images"][0]["path"]
    assert path.is_file()
    await press(dp, fake_bot, 10, DraftCb(job=job.id, i=0, a="del"), job.tg_message_id)
    assert not path.exists()
    await session.refresh(job)
    assert job.status == JobStatus.done


async def test_merge_by_url_adds_image(session, tmp_path) -> None:
    meta = await load_meta(session, 1)
    dish = await dish_svc.create_from_draft(
        session, meta, 1, {"title": "Борщ", "source_url": RECIPE_URL}, created_by=None
    )
    await dish_svc.merge_draft(
        session,
        meta,
        dish,
        {
            "title": "Борщ",
            "images": [
                {"path": "dishes/a.webp", "path_thumb": "dishes/a_t.webp", "origin": "og_image"}
            ],
        },
    )
    await session.commit()
    imgs = list(await session.scalars(select(DishImage)))
    assert len(imgs) == 1 and imgs[0].is_cover
    assert (await dish_svc.find_by_url(session, 1, RECIPE_URL)).id == dish.id


async def test_group_link_is_processed(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10)
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, f"глянь {RECIPE_URL}", chat_type="group"))
    job = await session.scalar(select(ImportJob))
    assert job is not None and job.payload == {"url": RECIPE_URL, "text": "глянь"}
