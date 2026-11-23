import random
from collections import Counter
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db.models import DishStatus
from app.services import dishes as dish_svc
from app.services.members import add_member
from app.services.meta import load_meta
from app.services.suggest import (
    Candidate,
    Filters,
    Weights,
    default_meal,
    load_candidates,
    pick,
    suggest,
    weight,
)

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)
W = Weights()


def cand(
    id_: int,
    *,
    days: float | None = None,
    rating: int | None = None,
    status: DishStatus = DishStatus.can_cook,
    main: int | None = None,
) -> Candidate:
    last = NOW - timedelta(days=days) if days is not None else None
    return Candidate(id_, status, rating, last, main)


# --- вага -------------------------------------------------------------------------------------


def test_weight_freshness_clamped() -> None:
    assert weight(cand(1, days=1), NOW, W) == 0.3  # мінімум
    assert weight(cand(1, days=14), NOW, W) == 1.0
    assert weight(cand(1, days=100), NOW, W) == 3.0  # максимум
    assert weight(cand(1), NOW, W) == 2.0  # ніколи не готували


def test_weight_rating_and_novelty() -> None:
    assert weight(cand(1, days=14, rating=5), NOW, W) == 1.6
    assert weight(cand(1, days=14, rating=1), NOW, W) == 0.8
    assert round(weight(cand(1, days=14, status=DishStatus.want_to_try), NOW, W), 6) == 0.7


# --- вибір -----------------------------------------------------------------------------------


def test_pick_three_unique_deterministic() -> None:
    cands = [cand(i, days=i) for i in range(1, 11)]
    a = pick(cands, NOW, random.Random(42))
    b = pick(cands, NOW, random.Random(42))
    assert [c.id for c in a] == [c.id for c in b]
    assert len({c.id for c in a}) == 3


def test_pick_prefers_stale() -> None:
    cands = [cand(1, days=1), cand(2, days=60)]
    rng = random.Random(0)
    firsts = Counter(pick(cands, NOW, rng, n=1)[0].id for _ in range(2000))
    # ваги 0.3 і 3.0 → давно не готувана має випадати ~10 разів частіше
    assert firsts[2] > firsts[1] * 6


def test_pick_diversity_by_main_ingredient() -> None:
    cands = [
        cand(1, main=10),
        cand(2, main=10),
        cand(3, main=10),
        cand(4, main=20),
        cand(5, main=30),
    ]
    for seed in range(50):
        mains = [c.main_ingredient_id for c in pick(cands, NOW, random.Random(seed))]
        assert sorted(mains) == [10, 20, 30]


def test_pick_diversity_relaxed_when_not_enough() -> None:
    cands = [cand(1, main=10), cand(2, main=10), cand(3, main=10)]
    assert len(pick(cands, NOW, random.Random(1))) == 3


def test_pick_fewer_candidates() -> None:
    assert [c.id for c in pick([cand(7)], NOW, random.Random(1))] == [7]
    assert pick([], NOW, random.Random(1)) == []


def test_default_meal() -> None:
    assert default_meal(8) == "breakfast"
    assert default_meal(11) == "lunch"
    assert default_meal(15) == "lunch"
    assert default_meal(16) == "dinner"


# --- кандидати з БД ---------------------------------------------------------------------------


async def _dish(session, meta, title, member=None, **draft):
    return await dish_svc.create_from_draft(
        session, meta, 1, {"title": title, **draft}, created_by=member.id if member else None
    )


async def test_cooked_yesterday_excluded(session) -> None:
    m = await add_member(session, "Ігор", tg_user_id=1)
    meta = await load_meta(session, 1)
    borsch = await _dish(session, meta, "Борщ")
    await _dish(session, meta, "Деруни")
    await dish_svc.mark_cooked(session, borsch, m)
    borsch.last_cooked_at = datetime.now(UTC) - timedelta(days=1)
    await session.commit()
    now = datetime.now(UTC)
    ids = {c.id for c in await load_candidates(session, 1, Filters(), now, 3)}
    assert borsch.id not in ids and len(ids) == 1
    for _ in range(20):
        dishes, _total = await suggest(session, 1, Filters(), get_settings(), now)
        assert "Борщ" not in [d.title for d in dishes]


async def test_filters(session) -> None:
    m = await add_member(session, "Зірка", tg_user_id=2)
    meta = await load_meta(session, 1)
    a = await _dish(session, meta, "Омлет", meal=["breakfast"], cooks=[m.id])
    b = await _dish(session, meta, "Плов", meal=["dinner"], cook_time_min=90)
    c = await _dish(session, meta, "Салат", cook_time_min=15)  # без прийому їжі
    d = await _dish(session, meta, "Рамен", status="want_to_try", traits=["quick"])
    now = datetime.now(UTC)

    async def ids(f: Filters) -> set[int]:
        return {x.id for x in await load_candidates(session, 1, f, now, 3)}

    assert await ids(Filters()) == {a.id, b.id, c.id}
    assert await ids(Filters(include_new=True)) == {a.id, b.id, c.id, d.id}
    assert await ids(Filters(meal=["breakfast"])) == {a.id}  # без тегу — не підходить
    assert await ids(Filters(cooks=[m.id])) == {a.id}
    assert await ids(Filters(quick=True, include_new=True)) == {c.id, d.id}
    assert await ids(Filters(exclude_ids=[a.id])) == {b.id, c.id}
