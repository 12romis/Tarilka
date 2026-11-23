"""Одиниці виміру інгредієнтів: різні написання («ст.л», «столова ложка») → одне.

Канонічні одиниці потрібні для перерахунку порцій (Mini App округлює за типом одиниці)
і, далі, для зведення однакових продуктів у списку покупок.
"""

import re

_ALIASES: dict[str, tuple[str, ...]] = {
    "г": ("г", "гр", "грам", "грамів", "грами", "грм", "g"),
    "кг": ("кг", "кілограм", "кілограми", "кілограмів", "kg"),
    "мл": ("мл", "мілілітр", "мілілітри", "мілілітрів", "ml"),
    "л": ("л", "літр", "літри", "літрів", "l"),
    "шт.": ("шт", "штука", "штуки", "штук", "плід", "pcs"),
    "ст. л.": (
        "стл",
        "столоваложка",
        "столовіложки",
        "столовихложок",
        "стіллож",
        "стілл",
        "стложка",
        "стложки",
        "tbsp",
    ),
    "ч. л.": ("чл", "чайналожка", "чайніложки", "чайнихложок", "чложка", "чложки", "tsp"),
    "склянка": ("скл", "склянка", "склянки", "склянок"),
    "зубчик": ("зубчик", "зубчики", "зубчиків", "зубчика", "зубці", "зубок"),
    "щіпка": ("щіпка", "щіпки", "дрібка", "дрібки"),
    "пучок": ("пучок", "пучки", "пучка"),
}
_CANON = {alias: unit for unit, aliases in _ALIASES.items() for alias in aliases}
_EMPTY = {"", "-", "—"}


def _key(unit: str) -> str:
    return re.sub(r"[\s.]+", "", unit.lower())


def canonical_unit(unit: str | None) -> str | None:
    """Відома одиниця → канонічна; невідома лишається як є (обрізана); порожня → None."""
    if unit is None:
        return None
    unit = " ".join(unit.split())
    if unit in _EMPTY:
        return None
    return _CANON.get(_key(unit), unit)


def normalize_ingredients(items: list[dict] | None) -> list[dict] | None:
    """Порожні назви відкидаються, одиниці — канонічні, нульова/від'ємна кількість → None."""
    if not items:
        return None
    out = []
    for item in items:
        name = " ".join(str(item.get("name") or "").split())
        if not name:
            continue
        qty = item.get("qty")
        out.append(
            {
                "name": name,
                "qty": qty if isinstance(qty, int | float) and qty > 0 else None,
                "unit": canonical_unit(item.get("unit")),
            }
        )
    return out or None
