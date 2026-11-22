"""REST API для Mini App: авторизація через initData, фільтри, редагування, фото."""

import io
import time
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.main import create_app, mount_webapp
from app.services import dishes as dish_svc
from app.services.members import add_member
from app.services.meta import load_meta
from tests.conftest import make_init_data


def auth(user_id: int) -> dict[str, str]:
    raw = make_init_data({"id": user_id, "first_name": "T"}, auth_date=int(time.time()))
    return {"Authorization": f"tma {raw}"}


@pytest.fixture
async def client(sessionmaker):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c


@pytest.fixture
async def family(session):
    igor = await add_member(session, "Ігор", tg_user_id=10, is_admin=True)
    zirka = await add_member(session, "Зірка", tg_user_id=20, aliases=["мама"])
    meta = await load_meta(session, 1)

    async def mk(title, **kw):
        return await dish_svc.create_from_draft(
            session, meta, 1, {"title": title, **kw}, created_by=igor.id
        )

    dishes = {
        "borsch": await mk(
            "Борщ", meal=["dinner"], course=["soup"], cooks=[zirka.id], cook_time_min=120
        ),
        "omelet": await mk(
            "Омлет", meal=["breakfast"], main_ingredient="eggs", cook_time_min=10, cooks=[igor.id]
        ),
        "ramen": await mk("Рамен", status="want_to_try", cuisine="asian"),
    }
    dishes["borsch"].last_cooked_at = datetime.now(UTC) - timedelta(days=30)
    dishes["borsch"].rating = 5
    await session.commit()
    return {"igor": igor, "zirka": zirka, "meta": meta, **dishes}


async def titles(client, query: str = "", user: int = 10) -> list[str]:
    r = await client.get(f"/api/dishes?{query}", headers=auth(user))
    assert r.status_code == 200, r.text
    return [d["title"] for d in r.json()["items"]]


async def test_list_filters_and_sort(client, family) -> None:
    assert await titles(client) == ["Омлет", "Рамен", "Борщ"]  # stale: неготовані першими
    assert await titles(client, "sort=title") == ["Борщ", "Омлет", "Рамен"]
    assert (await titles(client, "sort=rating"))[0] == "Борщ"
    assert await titles(client, "status=can_cook&sort=title") == ["Борщ", "Омлет"]
    assert await titles(client, "meal=dinner&meal=breakfast&sort=title") == ["Борщ", "Омлет"]
    assert await titles(client, "meal=breakfast&course=soup") == []  # між групами — AND
    assert await titles(client, f"cook={family['zirka'].id}") == ["Борщ"]
    assert await titles(client, "ingredient=eggs") == ["Омлет"]  # основний продукт теж тег
    assert await titles(client, "max_time=30") == ["Омлет"]
    assert await titles(client, "q=бор") == ["Борщ"]


async def test_pagination(client, family) -> None:
    r = await client.get("/api/dishes?sort=title&limit=2", headers=auth(10))
    page = r.json()
    assert page["total"] == 3 and page["next_cursor"] == 2 and len(page["items"]) == 2
    r = await client.get("/api/dishes?sort=title&limit=2&cursor=2", headers=auth(10))
    assert r.json()["next_cursor"] is None and r.json()["items"][0]["title"] == "Рамен"


async def test_auth_required(client, family) -> None:
    assert (await client.get("/api/dishes")).status_code == 401
    assert (await client.get("/api/dishes", headers=auth(999))).status_code == 403


async def test_meta(client, family) -> None:
    r = await client.get("/api/meta", headers=auth(10))
    data = r.json()
    assert data["total_dishes"] == 3
    assert [m["name"] for m in data["members"]] == ["Ігор", "Зірка"]
    soup = next(t for t in data["tags"]["course"] if t["slug"] == "soup")
    assert soup["count"] == 1 and soup["label"] == "суп"
    assert list(data["tags"]) == ["meal", "course", "ingredient", "trait", "cuisine"]


async def test_dish_full_and_cooked(client, family) -> None:
    dish_id = family["ramen"].id
    r = await client.post(f"/api/dishes/{dish_id}/cooked", json={}, headers=auth(20))
    full = r.json()
    assert full["status"] == "can_cook" and full["cooked_count"] == 1
    assert full["cook_ids"] == [family["zirka"].id]
    assert full["cook_log"][0]["member_id"] == family["zirka"].id
    r = await client.get(f"/api/dishes/{dish_id}", headers=auth(10))
    assert r.json()["cook_log"] and r.json()["images"] == []


async def test_create_patch_archive(client, family) -> None:
    meta = family["meta"]
    potato = meta.tag("ingredient", "potato").id
    dinner = meta.tag("meal", "dinner").id
    r = await client.post(
        "/api/dishes",
        headers=auth(10),
        json={
            "title": "Деруни",
            "tag_ids": [dinner],
            "main_ingredient_id": potato,
            "cook_ids": [family["zirka"].id],
            "ingredients": [{"name": "картопля", "qty": 1, "unit": "кг"}, {"name": ""}],
        },
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert set(d["tag_ids"]) == {dinner, potato} and d["main_ingredient_id"] == potato
    assert d["ingredients"] == [{"name": "картопля", "qty": 1.0, "unit": "кг"}]

    dup = await client.post("/api/dishes", headers=auth(10), json={"title": " деруни "})
    assert dup.status_code == 409 and dup.json()["detail"]["existing_id"] == d["id"]

    r = await client.patch(
        f"/api/dishes/{d['id']}",
        headers=auth(10),
        json={
            "title": "Деруни з грибами",
            "tag_ids": [],
            "rating": 4,
            "steps": ["Натерти", "Смажити"],
        },
    )
    p = r.json()
    assert p["title"] == "Деруни з грибами" and p["rating"] == 4
    assert p["tag_ids"] == [potato]  # основний продукт лишається тегом
    assert p["cook_ids"] == [family["zirka"].id]  # не передали — не змінилося
    bad = await client.patch(f"/api/dishes/{d['id']}", headers=auth(10), json={"rating": 9})
    assert bad.status_code == 422

    assert (await client.delete(f"/api/dishes/{d['id']}", headers=auth(10))).status_code == 204
    assert "Деруни з грибами" not in await titles(client)
    assert "Деруни з грибами" in await titles(client, "status=archived")


async def test_foreign_family_dish_is_404(client, family, session) -> None:
    from sqlalchemy import text

    await session.execute(text("INSERT INTO families (id, name) VALUES (2, 'Сусіди')"))
    await session.commit()
    meta2 = await load_meta(session, 2)
    other = await dish_svc.create_from_draft(
        session, meta2, 2, {"title": "Чужий борщ"}, created_by=None
    )
    try:
        assert (await client.get(f"/api/dishes/{other.id}", headers=auth(10))).status_code == 404
        r = await client.patch(f"/api/dishes/{other.id}", headers=auth(10), json={"title": "x"})
        assert r.status_code == 404
        assert "Чужий борщ" not in await titles(client)
    finally:
        await session.execute(text("DELETE FROM families WHERE id = 2"))
        await session.commit()


def png(color: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), color).save(buf, "PNG")
    return buf.getvalue()


async def test_images(client, family, media_dir) -> None:
    tmp_path = media_dir
    url = f"/api/dishes/{family['borsch'].id}/images"
    r1 = await client.post(
        url, headers=auth(10), files={"file": ("a.png", png("red"), "image/png")}
    )
    assert r1.status_code == 201, r1.text
    r2 = await client.post(
        url, headers=auth(10), files={"file": ("b.png", png("blue"), "image/png")}
    )
    imgs = r2.json()["images"]
    assert [i["is_cover"] for i in imgs] == [True, False]
    assert r2.json()["cover"]["id"] == imgs[0]["id"]
    assert (tmp_path / imgs[0]["url"].removeprefix("/media/")).is_file()

    r = await client.patch(f"{url}/{imgs[1]['id']}", headers=auth(10), json={"is_cover": True})
    assert r.json()["cover"]["id"] == imgs[1]["id"]
    r = await client.delete(f"{url}/{imgs[1]['id']}", headers=auth(10))
    left = r.json()["images"]
    assert len(left) == 1 and left[0]["is_cover"]  # обкладинка перейшла на наступне
    assert not (tmp_path / imgs[1]["url"].removeprefix("/media/")).exists()

    bad = await client.post(
        url, headers=auth(10), files={"file": ("x.txt", b"hello", "text/plain")}
    )
    assert bad.status_code == 415
    fake = await client.post(
        url, headers=auth(10), files={"file": ("x.png", b"not png", "image/png")}
    )
    assert fake.status_code == 422


async def test_suggest(client, family) -> None:
    r = await client.post("/api/suggest", headers=auth(10), json={"meal": ["breakfast"]})
    data = r.json()
    assert [d["title"] for d in data["dishes"]] == ["Омлет"]
    assert data["total_candidates"] == 1 and data["total_dishes"] == 3
    r = await client.post(
        "/api/suggest",
        headers=auth(10),
        json={"include_new": True, "exclude_ids": [family["omelet"].id]},
    )
    assert {d["title"] for d in r.json()["dishes"]} == {"Борщ", "Рамен"}


async def test_members_admin(client, family) -> None:
    r = await client.post("/api/members", headers=auth(20), json={"name": "Бабуся"})
    assert r.status_code == 403
    r = await client.post(
        "/api/members", headers=auth(10), json={"name": "Бабуся", "aliases": [" Бабця ", "бабця"]}
    )
    assert r.status_code == 201 and r.json()["aliases"] == ["бабця"]
    assert r.json()["has_telegram"] is False
    r = await client.patch(
        f"/api/members/{family['zirka'].id}",
        headers=auth(10),
        json={"aliases": ["мама", "Зіронька"]},
    )
    assert r.json()["aliases"] == ["мама", "зіронька"]


async def test_spa_fallback(tmp_path) -> None:
    from fastapi import FastAPI

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets" / "a-123.js").write_text("js")
    app = FastAPI()
    mount_webapp(app, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/dish/5/edit")
        assert r.text == "<html>app</html>" and r.headers["cache-control"] == "no-cache"
        r = await c.get("/assets/a-123.js")
        assert r.text == "js" and "immutable" in r.headers["cache-control"]
        assert (await c.get("/api/nope")).status_code == 404
        assert (await c.get("/../../etc/passwd")).text == "<html>app</html>"
