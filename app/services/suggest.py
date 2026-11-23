"""«Що приготувати?»: зважений випадковий вибір без повторів серед відфільтрованих страв.

Вага = freshness × rating_k × novelty_k (див. план, розділ Mini App). Сам вибір — чиста
функція `pick` з параметром rng для тестів; ваги — у Settings (SUGGEST_*).
"""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Dish, DishCook, DishStatus, DishTag, Tag


@dataclass(frozen=True)
class Weights:
    fresh_days: float = 14
    fresh_min: float = 0.3
    fresh_max: float = 3.0
    never_cooked: float = 2.0
    novelty_k: float = 0.7

    @classmethod
    def from_settings(cls, s: Settings) -> "Weights":
        return cls(
            s.suggest_fresh_days,
            s.suggest_fresh_min,
            s.suggest_fresh_max,
            s.suggest_never_cooked,
            s.suggest_novelty_k,
        )


@dataclass(frozen=True)
class Candidate:
    id: int
    status: DishStatus
    rating: int | None
    last_cooked_at: datetime | None
    main_ingredient_id: int | None


def weight(c: Candidate, now: datetime, w: Weights) -> float:
    if c.last_cooked_at is None:
        freshness = w.never_cooked
    else:
        days = (now - c.last_cooked_at).total_seconds() / 86400
        freshness = min(max(days / w.fresh_days, w.fresh_min), w.fresh_max)
    rating_k = 0.6 + 0.2 * c.rating if c.rating else 1.0
    novelty_k = w.novelty_k if c.status == DishStatus.want_to_try else 1.0
    return freshness * rating_k * novelty_k


def pick(
    candidates: list[Candidate],
    now: datetime,
    rng: random.Random,
    n: int = 3,
    w: Weights = Weights(),  # noqa: B008 — незмінний frozen dataclass
) -> list[Candidate]:
    """n страв без повторів; поки можливо — не більше однієї з тим самим основним продуктом."""
    pool = list(candidates)
    chosen: list[Candidate] = []
    used_main: set[int] = set()
    while pool and len(chosen) < n:
        diverse = [
            c for c in pool if c.main_ingredient_id is None or c.main_ingredient_id not in used_main
        ]
        source = diverse or pool
        c = rng.choices(source, weights=[weight(x, now, w) for x in source], k=1)[0]
        chosen.append(c)
        pool.remove(c)
        if c.main_ingredient_id is not None:
            used_main.add(c.main_ingredient_id)
    return chosen


@dataclass
class Filters:
    meal: list[str] = field(default_factory=list)  # slug, OR усередині групи
    cooks: list[int] = field(default_factory=list)
    quick: bool = False
    include_new: bool = False
    exclude_ids: list[int] = field(default_factory=list)


def _has_tag(kind: str, slugs: list[str]):
    return exists().where(
        DishTag.dish_id == Dish.id,
        DishTag.tag_id == Tag.id,
        Tag.kind == kind,
        Tag.slug.in_(slugs),
    )


async def load_candidates(
    session: AsyncSession, family_id: int, f: Filters, now: datetime, recent_days: int
) -> list[Candidate]:
    statuses = [DishStatus.can_cook] + ([DishStatus.want_to_try] if f.include_new else [])
    stmt = select(
        Dish.id, Dish.status, Dish.rating, Dish.last_cooked_at, Dish.main_ingredient_id
    ).where(
        Dish.family_id == family_id,
        Dish.status.in_(statuses),
        (Dish.last_cooked_at.is_(None)) | (Dish.last_cooked_at < now - timedelta(days=recent_days)),
    )
    if f.exclude_ids:
        stmt = stmt.where(Dish.id.not_in(f.exclude_ids))
    if f.meal:
        # Строго, як у каталозі: страва без тегу прийому їжі (соус, тісто) не пропонується.
        stmt = stmt.where(_has_tag("meal", f.meal))
    if f.cooks:
        stmt = stmt.where(
            exists().where(DishCook.dish_id == Dish.id, DishCook.member_id.in_(f.cooks))
        )
    if f.quick:
        stmt = stmt.where((Dish.cook_time_min <= 30) | _has_tag("trait", ["quick"]))
    return [Candidate(*row) for row in await session.execute(stmt)]


async def suggest(
    session: AsyncSession,
    family_id: int,
    f: Filters,
    settings: Settings,
    now: datetime,
    rng: random.Random | None = None,
    n: int = 3,
) -> tuple[list[Dish], int]:
    """Повертає до n страв і загальну кількість кандидатів."""
    cands = await load_candidates(session, family_id, f, now, settings.suggest_recent_days)
    chosen = pick(cands, now, rng or random.Random(), n, Weights.from_settings(settings))
    dishes = {
        d.id: d
        for d in await session.scalars(select(Dish).where(Dish.id.in_([c.id for c in chosen])))
    }
    return [dishes[c.id] for c in chosen], len(cands)


def default_meal(local_hour: int) -> str:
    """До 11:00 — сніданок, 11–16 — обід, після 16 — вечеря."""
    if local_hour < 11:
        return "breakfast"
    if local_hour < 16:
        return "lunch"
    return "dinner"
