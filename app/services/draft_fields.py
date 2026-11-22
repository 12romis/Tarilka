"""Поля чернетки (dict у JSONB) і чисті операції над ними — без БД.

Чернетка: title, description, status, cooks[member_id], meal[], course[],
main_ingredient, ingredient_tags[], traits[], cuisine, cook_time_min, difficulty,
servings, ingredients[{name, qty, unit}], steps[], notes, confidence, has_recipe,
плюс службові state і dish_id (див. services/drafts).
"""

from typing import Any

Draft = dict[str, Any]

# kind тегу → поле зі списком slug. Для ingredient перед списком іде main_ingredient,
# cuisine — одне значення (поле cuisine).
TAG_LIST_FIELDS = {
    "meal": "meal",
    "course": "course",
    "ingredient": "ingredient_tags",
    "trait": "traits",
}


def tag_slugs(d: Draft, kind: str) -> list[str]:
    """Обрані slug цього kind; для продуктів основний — першим."""
    if kind == "cuisine":
        return [d["cuisine"]] if d.get("cuisine") else []
    values = list(d.get(TAG_LIST_FIELDS[kind]) or [])
    if kind == "ingredient" and d.get("main_ingredient"):
        values = [d["main_ingredient"], *[v for v in values if v != d["main_ingredient"]]]
    return values


def toggle_tag(d: Draft, kind: str, slug: str) -> None:
    """Перемикає тег. Для продуктів перший обраний — основний (main_ingredient)."""
    if kind == "cuisine":
        d["cuisine"] = None if d.get("cuisine") == slug else slug
        return
    if kind == "ingredient":
        selected = tag_slugs(d, "ingredient")
        selected.remove(slug) if slug in selected else selected.append(slug)
        d["main_ingredient"] = selected[0] if selected else None
        d["ingredient_tags"] = selected[1:]
        return
    key = TAG_LIST_FIELDS[kind]
    values = list(d.get(key) or [])
    values.remove(slug) if slug in values else values.append(slug)
    d[key] = values


def toggle_cook(d: Draft, member_id: int) -> None:
    cooks = d.setdefault("cooks", [])
    cooks.remove(member_id) if member_id in cooks else cooks.append(member_id)


def toggle_status(d: Draft) -> None:
    d["status"] = "want_to_try" if d.get("status") == "can_cook" else "can_cook"


def raw_text_draft(text: str, cook: int | None) -> Draft:
    """Чернетка «як є», коли розбір не вдався: назва — початок тексту."""
    return {
        "title": text.strip()[:100] or "Без назви",
        "status": "can_cook",
        "cooks": [cook] if cook else [],
    }
