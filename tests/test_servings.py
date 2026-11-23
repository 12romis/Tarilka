"""Порції: канонічні одиниці інгредієнтів і дозаповнення порцій через AI."""

import pytest

from app.services import dishes as dish_svc
from app.services.meta import load_meta
from app.services.servings import fill_servings
from app.services.units import canonical_unit, normalize_ingredients
from tests.conftest import FakeAI


@pytest.mark.parametrize(
    ("raw", "canon"),
    [
        ("ст.л.", "ст. л."),
        ("ст. л", "ст. л."),
        ("ст л", "ст. л."),
        ("стіл. л.", "ст. л."),
        ("Столова ложка", "ст. л."),
        ("ч.л", "ч. л."),
        ("ч.ложка", "ч. л."),
        ("чайна ложка", "ч. л."),
        ("гр.", "г"),
        ("грам", "г"),
        ("грм", "г"),
        ("мілілітрів", "мл"),
        ("літри", "л"),
        ("шт", "шт."),
        ("штука", "шт."),
        ("зубчиків", "зубчик"),
        ("зубці", "зубчик"),
        ("скл.", "склянка"),
        ("дрібка", "щіпка"),
        ("пачка (200 г)", "пачка (200 г)"),  # невідоме лишається як є
        ("  за  смаком ", "за смаком"),
        ("-", None),
        ("", None),
        (None, None),
    ],
)
def test_canonical_unit(raw, canon) -> None:
    assert canonical_unit(raw) == canon


def test_normalize_ingredients() -> None:
    assert normalize_ingredients(
        [
            {"name": " цукор ", "qty": 1.5, "unit": "ст.л"},
            {"name": "", "qty": 1, "unit": "шт"},
            {"name": "сіль", "qty": 0, "unit": "-"},
        ]
    ) == [
        {"name": "цукор", "qty": 1.5, "unit": "ст. л."},
        {"name": "сіль", "qty": None, "unit": None},
    ]
    assert normalize_ingredients([{"name": " "}]) is None
    assert normalize_ingredients(None) is None


async def test_fill_servings(session) -> None:
    meta = await load_meta(session, 1)

    async def mk(title, **kw):
        return await dish_svc.create_from_draft(
            session, meta, 1, {"title": title, **kw}, created_by=None
        )

    eggs = [{"name": "яйця", "qty": 3, "unit": "шт."}]
    omelet = await mk("Омлет", ingredients=eggs)
    known = await mk("Сирники", ingredients=eggs, servings=4)  # з рецепта — не чіпаємо
    no_qty = await mk("Салат", ingredients=[{"name": "огірок", "qty": None, "unit": None}])
    pie = await mk("Пиріг", ingredients=eggs)
    ai = FakeAI([{"items": [{"id": omelet.id, "servings": 2}, {"id": pie.id, "servings": 99}]}])

    result, cost = await fill_servings(session, ai)

    assert result == {omelet.id: 2}  # 99 — поза межами, відкинуто
    assert cost == pytest.approx(0.001)
    sent = ai.calls[0][1]
    assert "Омлет" in sent and "3 шт. яйця" in sent
    assert "Сирники" not in sent and "Салат" not in sent
    for d in (omelet, known, no_qty, pie):
        await session.refresh(d)
    assert (omelet.servings, known.servings, no_qty.servings, pie.servings) == (2, 4, None, None)


async def test_fill_servings_dry_run(session) -> None:
    meta = await load_meta(session, 1)
    d = await dish_svc.create_from_draft(
        session,
        meta,
        1,
        {"title": "Омлет", "ingredients": [{"name": "яйця", "qty": 3}]},
        created_by=None,
    )
    result, _ = await fill_servings(
        session, FakeAI([{"items": [{"id": d.id, "servings": 2}]}]), dry_run=True
    )
    await session.refresh(d)
    assert result == {d.id: 2} and d.servings is None
