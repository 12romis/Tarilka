"""Формат відповіді LLM (structured output) і нормалізація чернетки."""

from typing import Literal

from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    name: str
    qty: float | None
    unit: str | None


class DishDraft(BaseModel):
    has_recipe: bool
    confidence: float = Field(description="0..1 — наскільки впевнено розпізнано страву")
    title: str
    description: str | None
    status: Literal["can_cook", "want_to_try"]
    cooks: list[int] = Field(description="id членів сім'ї зі списку")
    meal: list[str]
    course: list[str]
    main_ingredient: str | None
    ingredient_tags: list[str]
    traits: list[str]
    cuisine: str | None
    cook_time_min: int | None
    difficulty: int | None = Field(description="1 — легко, 2 — середньо, 3 — складно")
    servings: int | None
    ingredients: list[Ingredient]
    steps: list[str]
    notes: str | None


class TextParse(BaseModel):
    """Відповідь на текстовий/голосовий ввід: одна або кілька страв."""

    dishes: list[DishDraft]


def clean_draft(
    draft: DishDraft,
    *,
    slugs: dict[str, set[str]],
    member_ids: set[int],
    default_cook: int | None,
) -> dict:
    """Відкидає все, чого немає в довідниках, і заповнює типові значення.

    `slugs` — kind → множина slug із БД. LLM не може вигадати тег чи кухаря: невідоме зникає.
    `default_cook` — автор повідомлення: стає кухарем, якщо LLM нікого не знайшов
    і страву вже вміють готувати.
    """

    def only(kind: str, values: list[str]) -> list[str]:
        seen: list[str] = []
        for v in values:
            if v in slugs.get(kind, set()) and v not in seen:
                seen.append(v)
        return seen

    ingredients = only("ingredient", ([draft.main_ingredient] if draft.main_ingredient else []))
    main = ingredients[0] if ingredients else None
    extra = [s for s in only("ingredient", draft.ingredient_tags) if s != main]
    if main is None and extra:
        main, extra = extra[0], extra[1:]
    cooks = [c for c in dict.fromkeys(draft.cooks) if c in member_ids]
    # «Хочемо спробувати» ще ніхто не вміє — автора підставляємо лише для «вміємо».
    if not cooks and default_cook is not None and draft.status == "can_cook":
        cooks = [default_cook]
    cuisine = draft.cuisine if draft.cuisine in slugs.get("cuisine", set()) else None
    difficulty = draft.difficulty if draft.difficulty in (1, 2, 3) else None

    def positive(v: int | None) -> int | None:
        return v if v is not None and v > 0 else None

    return {
        "title": draft.title.strip()[:200],
        "description": draft.description,
        "status": draft.status,
        "cooks": cooks,
        "meal": only("meal", draft.meal),
        "course": only("course", draft.course),
        "main_ingredient": main,
        "ingredient_tags": extra,
        "traits": only("trait", draft.traits),
        "cuisine": cuisine,
        "cook_time_min": positive(draft.cook_time_min),
        "difficulty": difficulty,
        "servings": positive(draft.servings),
        "ingredients": [i.model_dump() for i in draft.ingredients] or None,
        "steps": draft.steps or None,
        "notes": draft.notes,
        "confidence": draft.confidence,
        "has_recipe": draft.has_recipe,
    }


class PhotoParse(BaseModel):
    """Відповідь vision: що на фото і які страви з нього вийшли."""

    kind: Literal["recipe", "dish", "other"]
    dishes: list[DishDraft]
