from sqlalchemy import select

from app.ai.schemas import DishDraft, clean_draft
from app.db.models import CookLog, Dish, DishCook, DishStatus, DishTag, JobKind, JobStatus, Tag
from app.services import dishes as dish_svc
from app.services import drafts as dr
from app.services.jobs import enqueue
from app.services.members import add_member
from app.services.meta import load_meta
from tests.conftest import dish_json

SLUGS = {
    "meal": {"dinner", "breakfast"},
    "course": {"soup", "main"},
    "ingredient": {"potato", "mushrooms", "beef"},
    "trait": {"quick"},
    "cuisine": {"ukrainian"},
}


def test_clean_draft_drops_unknown_and_defaults_cook() -> None:
    d = DishDraft.model_validate(
        dish_json(
            " Деруни ",
            meal=["dinner", "brunch"],
            course=["main", "main"],
            main_ingredient="tofu",
            ingredient_tags=["potato", "mushrooms"],
            traits=["spicy"],
            cuisine="french",
            cooks=[99],
            difficulty=7,
            cook_time_min=0,
        )
    )
    out = clean_draft(d, slugs=SLUGS, member_ids={1, 2}, default_cook=1)
    assert out["title"] == "Деруни"
    assert out["meal"] == ["dinner"] and out["course"] == ["main"]
    # невідомий основний продукт → першим стає наступний відомий
    assert out["main_ingredient"] == "potato" and out["ingredient_tags"] == ["mushrooms"]
    assert out["traits"] == [] and out["cuisine"] is None
    assert out["cooks"] == [1]  # чужий id відкинуто → автор повідомлення
    assert out["difficulty"] is None and out["cook_time_min"] is None


def test_clean_draft_want_to_try_has_no_default_cook() -> None:
    d = DishDraft.model_validate(dish_json("Рамен", status="want_to_try"))
    assert clean_draft(d, slugs=SLUGS, member_ids={1}, default_cook=1)["cooks"] == []


def test_clean_draft_keeps_found_cooks() -> None:
    d = DishDraft.model_validate(dish_json("Борщ", cooks=[2, 2]))
    assert clean_draft(d, slugs=SLUGS, member_ids={1, 2}, default_cook=1)["cooks"] == [2]


def test_toggle_ingredient_main_first() -> None:
    d: dict = {"main_ingredient": None, "ingredient_tags": []}
    dr.toggle_tag(d, "ingredient", "potato")
    dr.toggle_tag(d, "ingredient", "mushrooms")
    assert (d["main_ingredient"], d["ingredient_tags"]) == ("potato", ["mushrooms"])
    dr.toggle_tag(d, "ingredient", "potato")  # прибрали основний → основним стає наступний
    assert (d["main_ingredient"], d["ingredient_tags"]) == ("mushrooms", [])
    dr.toggle_tag(d, "cuisine", "ukrainian")
    assert d["cuisine"] == "ukrainian"
    dr.toggle_tag(d, "cuisine", "ukrainian")
    assert d["cuisine"] is None
    dr.toggle_tag(d, "meal", "dinner")
    assert d["meal"] == ["dinner"]
    dr.toggle_tag(d, "meal", "dinner")
    assert d["meal"] == []


async def _job_with(session, drafts: list[dict], author: int):
    job = await enqueue(
        session, family_id=1, kind=JobKind.text, payload={"text": "x"}, created_by=author
    )
    job.status = JobStatus.draft_ready
    job.result = {
        "drafts": [d | {"state": "pending", "dish_id": None} for d in drafts],
        "view": "summary",
    }
    await session.commit()
    return job


async def test_save_all_creates_dishes_with_tags_and_cooks(session) -> None:
    m = await add_member(session, "Зірка", tg_user_id=2)
    meta = await load_meta(session, 1)
    job = await _job_with(
        session,
        [
            {
                "title": "Борщ",
                "status": "can_cook",
                "cooks": [m.id],
                "course": ["soup"],
                "cuisine": "ukrainian",
                "main_ingredient": "beef",
                "meal": ["dinner"],
            },
            {"title": "Деруни", "status": "want_to_try", "cooks": [], "traits": ["quick"]},
        ],
        m.id,
    )
    assert await dr.save_all(session, meta, job) == 2
    assert job.status == JobStatus.done
    dishes = {d.title: d for d in await session.scalars(select(Dish))}
    borsch = dishes["Борщ"]
    assert (
        borsch.status == DishStatus.can_cook and dishes["Деруни"].status == DishStatus.want_to_try
    )
    assert borsch.main_ingredient_id == meta.tag("ingredient", "beef").id
    slugs = set(
        await session.scalars(
            select(Tag.slug)
            .join(DishTag, DishTag.tag_id == Tag.id)
            .where(DishTag.dish_id == borsch.id)
        )
    )
    assert slugs == {"soup", "ukrainian", "beef", "dinner"}
    cooks = list(
        await session.scalars(select(DishCook.member_id).where(DishCook.dish_id == borsch.id))
    )
    assert cooks == [m.id]
    # повторне збереження нічого не дублює
    assert await dr.save_all(session, meta, job) == 0


async def test_discard_one_then_save_rest(session) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1)
    meta = await load_meta(session, 1)
    job = await _job_with(session, [{"title": "А"}, {"title": "Б"}], m.id)
    await dr.discard(session, job, 0)
    assert dr.pending_indexes(job) == [1] and job.status == JobStatus.draft_ready
    await dr.save_one(session, meta, job, 1)
    assert job.status == JobStatus.done
    assert [d.title for d in await session.scalars(select(Dish))] == ["Б"]


async def test_mark_cooked(session) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1)
    meta = await load_meta(session, 1)
    dish = await dish_svc.create_from_draft(
        session, meta, 1, {"title": "Рамен", "status": "want_to_try"}, created_by=m.id
    )
    await dish_svc.mark_cooked(session, dish, m)
    await dish_svc.mark_cooked(session, dish, m)  # двічі — без дубля в dish_cooks
    assert dish.status == DishStatus.can_cook and dish.cooked_count == 2
    assert dish.last_cooked_at is not None
    assert len(list(await session.scalars(select(CookLog)))) == 2
    assert list(await session.scalars(select(DishCook.member_id))) == [m.id]


async def test_search_trigram(session) -> None:
    meta = await load_meta(session, 1)
    for t in ("Деруни з грибами", "Борщ український", "Олів'є"):
        await dish_svc.create_from_draft(session, meta, 1, {"title": t}, created_by=None)
    assert [d.title for d in await dish_svc.search(session, 1, "деруни")] == ["Деруни з грибами"]
    assert [d.title for d in await dish_svc.search(session, 1, "борш")] == ["Борщ український"]
    assert await dish_svc.search(session, 1, "піца") == []
